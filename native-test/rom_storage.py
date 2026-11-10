"""Package optional archived cartridge data without changing native code generation."""
import hashlib
import struct

# Leave room for the AppVar header so two chunks fit a 64 KiB archive sector.
CHUNK_SIZE = 0x7f80


def cartridge_chunks(data):
    digest = hashlib.sha256(data).digest()
    prefix = "G" + digest.hex()[:4].upper()
    for index, start in enumerate(range(0, len(data), CHUNK_SIZE)):
        payload = data[start:start + CHUNK_SIZE]
        name = f"{prefix}{index:03X}"
        body = b"GBR1" + digest + struct.pack("<HH", index, len(payload)) + payload
        variable = struct.pack("<H", len(body)) + body
        entry = (struct.pack("<HHB", 13, len(variable), 0x15) + name.encode("ascii") +
                 b"\0\x80" + struct.pack("<H", len(variable)) + variable)
        header = b"**TI83F*\x1a\x0a\0" + bytes(42) + struct.pack("<H", len(entry))
        yield name, header + entry + struct.pack("<H", sum(entry) & 0xffff)


def storage_metadata(data):
    digest = hashlib.sha256(data).digest()
    prefix = "G" + digest.hex()[:4].upper()
    return (f'\nconst char gb_archive_prefix[] = "{prefix}";\n'
            'const unsigned char gb_archive_digest[] = {' +
            ','.join(str(byte) for byte in digest) + '};\n')
