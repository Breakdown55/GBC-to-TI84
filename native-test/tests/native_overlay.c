#include "native_overlay.h"
#include <ti/vars.h>
#include <assert.h>
#include <stddef.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif

volatile uint16_t gb_rom_bank;
const char gb_native_prefix[6] = "N1234";
const uint8_t gb_native_digest[32] = {42};
const GbNativeBank gb_native_banks[] = {
    {4, (const GbOverlayTarget *)(test_cache + 32), 2},
    {8, (const GbOverlayTarget *)(test_cache + 64), 1},
    {0x404, (const GbOverlayTarget *)(test_cache + 64), 1},
    {0x800, (const GbOverlayTarget *)(test_cache + 32), 2},
    {0x801, (const GbOverlayTarget *)(test_cache + 64), 1},
};
const unsigned gb_native_bank_count = 5;
enum { TEST_IMAGE_SIZE = 4096 };
static var_t variables[5];
static int absent = -1, in_ram = -1;
static unsigned invalidations;

const var_t *os_GetAppVarData(const char *name, int *archived)
{
    static const char *names[] = {"N1234004", "N1234008", "N1234404", "N1234800", "N1234801"};
    int index = 0;
    while (index < 5 && strcmp(name, names[index])) ++index;
    assert(index < 5);
    *archived = index != in_ram;
    return index == absent ? 0 : &variables[index];
}

void gb_native_cache_reset(void)
{
    ++invalidations;
}

static void initialize(unsigned index)
{
    var_t *variable = &variables[index];
    const GbNativeBank *bank = &gb_native_banks[index];
    memset(variable, 0, sizeof *variable);
    variable->size = 46 + TEST_IMAGE_SIZE + bank->count * 2;
    uint8_t *data = variable->data;
    memcpy(data, "GBN2", 4);
    memcpy(data + 4, gb_native_digest, 32);
    data[36] = bank->bank;
    data[37] = bank->bank >> 8;
    data[38] = TEST_IMAGE_SIZE & 255;
    data[39] = TEST_IMAGE_SIZE >> 8;
    data[40] = bank->count == 2 ? 32 : 64;
    data[42] = data[44] = bank->count;
    data[46] = 0xaa + index;
    unsigned first_address = index < 3 ? 0x4000 : index == 3 ? 0x400 : 0x800;
    GbOverlayTarget targets[] = {
        {first_address, (GbNativeBlock)(test_cache + 8)},
        {first_address + 16, (GbNativeBlock)(test_cache + 16)},
    };
    memcpy(data + 46 + data[40], targets, bank->count * sizeof *targets);
    for (unsigned i = 0; i < bank->count; ++i) {
        unsigned offset = data[40] + i * sizeof *targets + offsetof(GbOverlayTarget, entry);
        data[46 + TEST_IMAGE_SIZE + 2 * i] = offset;
        data[46 + TEST_IMAGE_SIZE + 2 * i + 1] = offset >> 8;
    }
}

int main(void)
{
    /* Native relocation words are 24 bits. Reserve matching low addresses in
     * this test process so host pointers exercise those words unchanged. */
#ifdef _WIN32
    void *memory = VirtualAlloc((void *)0xd20000, 0x20000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
#else
    void *memory = mmap((void *)0xd20000, 0x20000, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
#endif
    assert(memory == (void *)0xd20000);
    for (unsigned i = 0; i < 5; ++i) initialize(i);
    assert(gb_native_init() && invalidations == 1);
    gb_rom_bank = 1;
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + 8));
    assert(test_cache[0] == 0xaa && gb_native_loads == 1);
    assert(gb_native_lookup(0x4010) == (GbNativeBlock)(test_cache + 16));
    assert(gb_native_loads == 1);
    gb_rom_bank = 0x101;
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + TEST_IMAGE_SIZE + 8));
    assert(test_cache[TEST_IMAGE_SIZE] == 0xac && gb_native_loads == 2);
    gb_rom_bank = 1;
    assert(gb_native_lookup(0x4010) == (GbNativeBlock)(test_cache + 16));
    assert(gb_native_loads == 2 && invalidations == 1);
    assert(!gb_native_lookup(0x4001));
    gb_rom_bank = 2;
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + TEST_IMAGE_SIZE + 8));
    assert(test_cache[TEST_IMAGE_SIZE] == 0xab && gb_native_loads == 3);
    assert(gb_native_evictions == 1 && invalidations == 2);
    gb_rom_bank = 1;
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + 8));
    assert(gb_native_lookup(0x400) == (GbNativeBlock)(test_cache + TEST_IMAGE_SIZE + 8));
    assert(test_cache[TEST_IMAGE_SIZE] == 0xad && gb_native_loads == 4);
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + 8));
    assert(gb_native_lookup(0x800) == (GbNativeBlock)(test_cache + TEST_IMAGE_SIZE + 8));
    assert(test_cache[TEST_IMAGE_SIZE] == 0xae && gb_native_loads == 5);
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + 8));
    assert(gb_native_lookup(0x410) == (GbNativeBlock)(test_cache + TEST_IMAGE_SIZE + 16));
    assert(test_cache[TEST_IMAGE_SIZE] == 0xad && gb_native_loads == 6);
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + 8));
    gb_rom_bank = 0x101;
    assert(gb_native_lookup(0x4000) == (GbNativeBlock)(test_cache + TEST_IMAGE_SIZE + 8));
    assert(test_cache[TEST_IMAGE_SIZE] == 0xac && gb_native_loads == 7);
    assert(gb_native_evictions == 5 && invalidations == 6);

    absent = 1;
    assert(!gb_native_init());
    absent = -1;
    in_ram = 0;
    assert(!gb_native_init());
    in_ram = -1;
    variables[0].data[4] ^= 1;
    assert(!gb_native_init());
    initialize(0);
    variables[0].data[40] = 255;
    assert(!gb_native_init());
    initialize(0);
    variables[0].data[38] = 1;
    assert(!gb_native_init());
    initialize(0);
    variables[0].data[46 + TEST_IMAGE_SIZE] = 255;
    assert(!gb_native_init());
    initialize(0);
    assert(gb_native_init());
    return 0;
}
