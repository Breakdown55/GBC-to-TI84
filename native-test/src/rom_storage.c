#include "rom_storage.h"
#if GB_ROM_CHUNKS
#include "runtime.h"
#include <ti/vars.h>
#include <string.h>

extern const char gb_archive_prefix[];
extern const uint8_t gb_archive_digest[32];
#if GB_ARCHIVE_BANKS
extern const uint8_t gb_archive_banks[];
#endif
char gb_archive_name[9];
static const uint8_t *chunks[GB_ROM_CHUNKS];

uint8_t gb_archive_init(void)
{
    static const char hex[] = "0123456789ABCDEF";
    memcpy(gb_archive_name, gb_archive_prefix, 5);
    gb_archive_name[8] = 0;
    for (unsigned i = 0; i < GB_ROM_CHUNKS; ++i) {
#if GB_ARCHIVE_BANKS
        if (!(gb_archive_banks[i / 8] & (1 << (i & 7)))) continue;
#endif
        gb_archive_name[5] = hex[(i >> 8) & 15];
        gb_archive_name[6] = hex[(i >> 4) & 15];
        gb_archive_name[7] = hex[i & 15];
        int archived = 0;
        const var_t *variable = os_GetAppVarData(gb_archive_name, &archived);
        unsigned length = GB_ROM_SIZE - i * GB_ROM_CHUNK_SIZE;
        if (length > GB_ROM_CHUNK_SIZE) length = GB_ROM_CHUNK_SIZE;
        if (!variable || !archived || variable->size != length + 40) return 0;
        const uint8_t *data = variable->data;
        if (memcmp(data, "GBR1", 4) || memcmp(data + 4, gb_archive_digest, 32) ||
            (data[36] | ((unsigned)data[37] << 8)) != i ||
            (data[38] | ((unsigned)data[39] << 8)) != length) return 0;
        chunks[i] = data + 40;
    }
    return 1;
}

const uint8_t *gb_archive_pointer(uint16_t address, unsigned length)
{
    if (address >= 0x8000 || length > 0x4000 - (address & 0x3fff)) return 0;
    unsigned bank = address < 0x4000 ? 0 : gb_rom_bank;
    unsigned linear = (bank << 14) | (address & 0x3fff);
    if (linear >= GB_ROM_SIZE || length > GB_ROM_SIZE - linear) return 0;
    unsigned chunk = linear / GB_ROM_CHUNK_SIZE;
    unsigned offset = linear - chunk * GB_ROM_CHUNK_SIZE;
#if GB_ARCHIVE_BANKS
    if (!chunks[chunk]) return 0;
#endif
    if (length > GB_ROM_CHUNK_SIZE - offset) return 0;
    return chunks[chunk] + offset;
}

uint16_t gb_archive_read(uint16_t address)
{
    const uint8_t *byte = gb_archive_pointer(address, 1);
    return byte ? *byte : GB_UNMAPPED_BYTE;
}
#endif
