"""Package optional archived cartridge data without changing native code generation."""
import hashlib
import struct

# Leave room for the AppVar header so two chunks fit a 64 KiB archive sector.
CHUNK_SIZE = 0x7f80
BANK_CHUNK_SIZE = 0x4000


def appvar(name, body):
    if len(name) != 8 or not name.isascii() or len(body) > 0xffff - 19:
        raise ValueError("invalid AppVar name or size")
    variable = struct.pack("<H", len(body)) + body
    entry = (struct.pack("<HHB", 13, len(variable), 0x15) + name.encode("ascii") +
             b"\0\x80" + struct.pack("<H", len(variable)) + variable)
    header = b"**TI83F*\x1a\x0a\0" + bytes(42) + struct.pack("<H", len(entry))
    return header + entry + struct.pack("<H", sum(entry) & 0xffff)


def cartridge_chunks(data, banks=None):
    digest = hashlib.sha256(data).digest()
    prefix = ("G" if banks is None else "B") + digest.hex()[:4].upper()
    size = CHUNK_SIZE if banks is None else BANK_CHUNK_SIZE
    indices = range((len(data) + size - 1) // size) if banks is None else sorted(banks)
    for index in indices:
        start = index * size
        payload = data[start:start + size]
        name = f"{prefix}{index:03X}"
        body = b"GBR1" + digest + struct.pack("<HH", index, len(payload)) + payload
        yield name, appvar(name, body)


def storage_metadata(data, banks=None):
    digest = hashlib.sha256(data).digest()
    prefix = ("G" if banks is None else "B") + digest.hex()[:4].upper()
    metadata = (f'\nconst char gb_archive_prefix[] = "{prefix}";\n'
                'const unsigned char gb_archive_digest[] = {' +
                ','.join(str(byte) for byte in digest) + '};\n')
    if banks is not None:
        bitmap = bytearray((len(data) // BANK_CHUNK_SIZE + 7) // 8)
        for bank in banks:
            bitmap[bank // 8] |= 1 << (bank & 7)
        metadata += 'const unsigned char gb_archive_banks[] = {' + ','.join(map(str, bitmap)) + '};\n'
    return metadata
