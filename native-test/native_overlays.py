"""Link native cartridge banks into one reusable RAM address range."""
import hashlib
import re
import struct

from rom_storage import appvar


CACHE_ADDRESS = 0xd26000
CACHE_SIZE = 0x17000
RESIDENT_LIMIT = 0x400
FIXED_PAGE_SHIFT = 11
BANK_PAGE_SHIFT = 12
FIXED_REGION_BASE = 0x800


def region_key(bank, pc):
    if RESIDENT_LIMIT <= pc < 0x4000:
        return FIXED_REGION_BASE + (pc >> FIXED_PAGE_SHIFT)
    if 0x4000 <= pc < 0x8000:
        return (bank << (14 - BANK_PAGE_SHIFT)) | ((pc & 0x3fff) >> BANK_PAGE_SHIFT)
    return None


def overlay_banks(assembly):
    return sorted({int(value, 16) for value in re.findall(r"\.section \.gb_native_([0-9a-f]{3})\b", assembly)})


def linker_script(original, banks):
    reserve = f"""
    ASSERT(ADDR(.data) + SIZEOF(.data) <= 0x{CACHE_ADDRESS:x},
           "resident code overlaps the native bank cache")
    .gb_native_cache 0x{CACHE_ADDRESS:x} :
    {{
        FILL(0)
        BYTE(0)
        . += 0x{CACHE_SIZE - 1:x};
    }} >prgm
    ___data_high = .;"""
    if original.count("___data_high = .;") != 1:
        raise ValueError("unrecognized CE linker data layout")
    result = original.replace("___data_high = .;", reserve)
    sections = "\n".join(f"        .gb_native_{bank:03x} {{ KEEP(*(.gb_native_{bank:03x})) }}" for bank in banks)
    overlay = f"""
    OVERLAY 0x{CACHE_ADDRESS:x} : AT(0x400000)
    {{
{sections}
    }} >prgm

"""
    marker = "    .debug_info 0"
    if result.count(marker) != 1:
        raise ValueError("unrecognized CE linker debug layout")
    return result.replace(marker, overlay + marker)


def symbol(mapping, name):
    match = re.search(r"(?m)^\s*0x([0-9a-f]+)\s+" + re.escape(name) + r"\s*$", mapping)
    if match is None:
        raise ValueError(f"missing linked native bank symbol {name}")
    return int(match[1], 16)


def elf_sections(data):
    if data[:6] != b"\x7fELF\x01\x01" or len(data) < 52:
        raise ValueError("expected a little-endian 32-bit native ELF")
    offset = struct.unpack_from("<I", data, 32)[0]
    stride, count, names_index = struct.unpack_from("<HHH", data, 46)
    if stride != 40 or names_index >= count or offset + count * stride > len(data):
        raise ValueError("invalid native ELF section headers")
    headers = [struct.unpack_from("<10I", data, offset + index * stride) for index in range(count)]
    names_header = headers[names_index]
    names = data[names_header[4]:names_header[4] + names_header[5]]
    result = {}
    for header in headers:
        if header[1] == 8:  # SHT_NOBITS has no payload in the file.
            continue
        end = names.find(b"\0", header[0])
        if end < 0 or header[4] + header[5] > len(data):
            raise ValueError("invalid native ELF section payload")
        name = names[header[0]:end].decode("utf-8")
        result[name] = header[3], data[header[4]:header[4] + header[5]]
    return result


def package_overlays(run, work, banks, load_address):
    mapping = (work / "program.map").read_text()
    tables = (work / "targets.c").read_text()
    sections = elf_sections((work / "program.obj").read_bytes())
    listing = run("binutils/bin/z80-none-elf-readelf", "-r", "-W", "program.obj",
                  capture_output=True, text=True).stdout
    relocations = {}
    active = None
    for line in listing.splitlines():
        if line.startswith("Relocation section "):
            match = re.search(r"'\.rela?\.gb_native_([0-9a-f]{3})'", line)
            active = int(match[1], 16) if match else None
        elif active is not None:
            fields = line.split()
            if len(fields) >= 3 and re.fullmatch(r"[0-9a-fA-F]+", fields[0]):
                name = fields[4] if len(fields) >= 5 else ""
                relocations.setdefault(active, []).append((int(fields[0], 16), fields[2], name))
    images = []
    for bank in banks:
        section = f".gb_native_{bank:03x}"
        if section not in sections:
            raise ValueError(f"missing linked native region {bank:03X}")
        address, payload = sections[section]
        if not payload:
            raise ValueError(f"empty linked native region {bank:03X}")
        cache, capacity = CACHE_ADDRESS, min(CACHE_SIZE, 0xffff - 65)
        if address != cache:
            raise ValueError("native region is linked at the wrong cache address")
        if len(payload) > capacity:
            raise ValueError(f"native region {bank:03X} requires {len(payload):,} bytes, cache holds {capacity:,}")
        offset = symbol(mapping, f"_gb_native_targets_{bank:03x}") - cache
        count_match = re.search(r"\{0x" + f"{bank:03x}" + r", gb_native_targets_[0-9a-f]+, (\d+)\}", tables)
        if count_match is None:
            raise ValueError(f"missing native target count for bank {bank:03X}")
        count = int(count_match[1])
        if not 0 <= offset <= len(payload) or count * 5 > len(payload) - offset:
            raise ValueError("native bank target table is outside its image")
        previous = -1
        for index in range(count):
            position = offset + index * 5
            pc = int.from_bytes(payload[position:position + 2], "little")
            pointer = int.from_bytes(payload[position + 2:position + 5], "little")
            expected = region_key(0 if bank >= FIXED_REGION_BASE else bank >> (14 - BANK_PAGE_SHIFT), pc)
            if not previous < pc or expected != bank or not cache <= pointer < cache + len(payload):
                raise ValueError("invalid native bank target")
            previous = pc
        fixups = []
        for location, kind, name in relocations.get(bank, ()):
            location -= cache
            if kind == "R_Z80_24":
                if not 0 <= location <= len(payload) - 3:
                    raise ValueError("native relocation is outside its image")
                pointer = int.from_bytes(payload[location:location + 3], "little")
                if cache <= pointer < cache + len(payload):
                    guest = re.fullmatch(r"_gb_([0-9a-f]{3})_([0-9a-f]{4})", name)
                    section = re.fullmatch(r"\.gb_native_([0-9a-f]{3})", name)
                    if ((guest and region_key(int(guest[1], 16), int(guest[2], 16)) != bank)
                            or (section and int(section[1], 16) != bank)):
                        raise ValueError("native branch bypasses a different region's loader")
                    fixups.append(location)
            elif "PCREL" not in kind:
                # The generated native code uses three-byte absolute pointers.
                raise ValueError(f"unsupported native relocation {kind}")
        fixups = sorted(set(fixups))
        if len(payload) + 2 * len(fixups) > 0xffff - 65:
            raise ValueError(f"native region {bank:03X} and its relocations exceed one AppVar")
        images.append((bank, payload, offset, count, fixups))
    remove = [f"--remove-section=.gb_native_{bank:03x}" for bank in banks]
    run("binutils/bin/z80-none-elf-objcopy", "--remove-relocations=*", *remove, "program.obj", "resident.obj")
    run("binutils/bin/z80-none-elf-objcopy", "-O", "binary", "resident.obj", "resident.bin")
    resident = (work / "resident.bin").read_bytes()
    if len(resident) > CACHE_ADDRESS + CACHE_SIZE - load_address:
        raise ValueError("resident program extends beyond its reserved native cache")
    fingerprint = hashlib.sha256(resident)
    for bank, payload, offset, count, fixups in images:
        fingerprint.update(struct.pack("<HHH", bank, offset, count))
        fingerprint.update(payload)
        fingerprint.update(b"".join(struct.pack("<H", fixup) for fixup in fixups))
    digest = fingerprint.digest()
    prefix = "N" + digest.hex()[:4].upper()
    if ".rodata" not in sections:
        raise ValueError("missing resident read-only data section")
    rodata_address, rodata_bytes = sections[".rodata"]
    rodata = bytearray(rodata_bytes)
    for name, value in (("_gb_native_prefix", prefix.encode("ascii") + b"\0"),
                        ("_gb_native_digest", digest)):
        offset = symbol(mapping, name) - rodata_address
        if not 0 <= offset <= len(rodata) - len(value):
            raise ValueError("native build identity is outside read-only data")
        rodata[offset:offset + len(value)] = value
    (work / "resident-rodata.bin").write_bytes(rodata)
    run("binutils/bin/z80-none-elf-objcopy", "--update-section", ".rodata=resident-rodata.bin",
        "resident.obj", "program.obj")
    directory = work / "native-data"
    directory.mkdir()
    for bank, payload, offset, count, fixups in images:
        name = f"{prefix}{bank:03X}"
        body = (b"GBN2" + digest + struct.pack("<HHHHH", bank, len(payload), offset, count, len(fixups))
                + payload + b"".join(struct.pack("<H", fixup) for fixup in fixups))
        (directory / f"{name}.8xv").write_bytes(appvar(name, body))
    return [(bank, len(payload)) for bank, payload, _, _, _ in images]
