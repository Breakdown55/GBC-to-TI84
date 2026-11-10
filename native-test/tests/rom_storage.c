#include "runtime.h"
#include "rom_storage.h"
#include <ti/vars.h>
#include <assert.h>
#include <string.h>

const char gb_archive_prefix[] = "G1234";
const uint8_t gb_archive_digest[32] = {42};
volatile uint16_t gb_rom_bank;
static var_t variables[3];
static int absent = -1, in_ram = -1;

var_t *os_GetAppVarData(const char *name, int *archived)
{
    assert(!memcmp(name, "G123400", 7) && !name[8]);
    unsigned index = name[7] - '0';
    assert(index < 3);
    *archived = (int)index != in_ram;
    return (int)index == absent ? 0 : &variables[index];
}

int main(void)
{
    for (unsigned i = 0; i < 3; ++i) {
        unsigned length = i == 2 ? GB_ROM_SIZE - 2 * GB_ROM_CHUNK_SIZE : GB_ROM_CHUNK_SIZE;
        variables[i].size = length + 40;
        uint8_t *data = variables[i].data;
        memcpy(data, "GBR1", 4);
        memcpy(data + 4, gb_archive_digest, 32);
        data[36] = i;
        data[38] = length;
        data[39] = length >> 8;
        for (unsigned j = 0; j < length; ++j) data[j + 40] = (i * GB_ROM_CHUNK_SIZE + j) >> 14;
    }
    assert(gb_archive_init());
    gb_rom_bank = 3;
    assert(gb_archive_read(0x3fff) == 0);
    assert(gb_archive_read(0x4000) == 3);
    assert(gb_archive_read(0x7fff) == 3);
    assert(gb_archive_read(0x8000) == GB_UNMAPPED_BYTE);
    assert(gb_archive_pointer(0x4000, 0x3f00));
    assert(!gb_archive_pointer(0x4000, 0x4000)); /* Chunk boundary within bank 3. */
    assert(!gb_archive_pointer(0x3fff, 2));
    assert(!gb_archive_pointer(0x7fff, 2));
    gb_rom_bank = 0;
    assert(gb_archive_read(0x4000) == 0);
    gb_rom_bank = 4;
    assert(gb_archive_read(0x4fff) == 4);
    assert(gb_archive_read(0x5000) == GB_UNMAPPED_BYTE);
    gb_rom_bank = 6;
    assert(gb_archive_read(0x4000) == GB_UNMAPPED_BYTE);
    absent = 1;
    assert(!gb_archive_init() && !strcmp(gb_archive_name, "G1234001"));
    absent = -1;
    in_ram = 2;
    assert(!gb_archive_init());
    in_ram = -1;
    variables[1].data[4] ^= 1;
    assert(!gb_archive_init());
    variables[1].data[4] ^= 1;
    variables[1].size--;
    assert(!gb_archive_init());
    variables[1].size++;
    variables[1].data[36] = 0;
    assert(!gb_archive_init());
    variables[1].data[36] = 1;
    assert(gb_archive_init());
    return 0;
}
