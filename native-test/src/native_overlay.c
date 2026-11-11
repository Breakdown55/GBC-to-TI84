#include "native_overlay.h"
#if GB_NATIVE_OVERLAYS
#include <ti/vars.h>
#include <string.h>

extern const char gb_native_prefix[6];
extern const uint8_t gb_native_digest[32];

enum { IMAGE_HEADER = 46 };
/* The guest's RAM occupies most of the small OS BSS region. Keep native
 * cache bookkeeping in the program's own reserved RAM allocation. */
#define NATIVE_STATE __attribute__((section(".data.gb_native_cache")))
static const uint8_t *images[GB_NATIVE_OVERLAYS] NATIVE_STATE;
static uint16_t lengths[GB_NATIVE_OVERLAYS] NATIVE_STATE;
static uint24_t offsets[GB_NATIVE_OVERLAYS] NATIVE_STATE;
static uint16_t fixup_counts[GB_NATIVE_OVERLAYS] NATIVE_STATE;
static uint24_t used[GB_NATIVE_OVERLAYS] NATIVE_STATE;
static uint8_t cached[GB_NATIVE_OVERLAYS] NATIVE_STATE;
static uint24_t clock;
uint24_t gb_native_loads;
uint24_t gb_native_evictions;
char gb_native_archive_name[9];

static uint16_t word(const uint8_t *bytes)
{
    return bytes[0] | ((uint16_t)bytes[1] << 8);
}

static uint24_t pointer(const uint8_t *bytes)
{
    return bytes[0] | ((uint24_t)bytes[1] << 8) | ((uint24_t)bytes[2] << 16);
}

uint8_t gb_native_init(void)
{
    static const char hex[] = "0123456789ABCDEF";
    memcpy(gb_native_archive_name, gb_native_prefix, 5);
    gb_native_archive_name[8] = 0;
    if (gb_native_bank_count != GB_NATIVE_OVERLAYS) return 0;
    for (unsigned i = 0; i < gb_native_bank_count; ++i) {
        const GbNativeBank *bank = &gb_native_banks[i];
        gb_native_archive_name[5] = hex[(bank->bank >> 8) & 15];
        gb_native_archive_name[6] = hex[(bank->bank >> 4) & 15];
        gb_native_archive_name[7] = hex[bank->bank & 15];
        int archived = 0;
        const var_t *variable = os_GetAppVarData(gb_native_archive_name, &archived);
        if (!variable || !archived || variable->size < IMAGE_HEADER) return 0;
        const uint8_t *data = variable->data;
        unsigned length = word(data + 38), fixups = word(data + 44);
        uintptr_t base = GB_NATIVE_CACHE;
        unsigned targets = (uintptr_t)bank->targets - base;
        if (memcmp(data, "GBN2", 4) || memcmp(data + 4, gb_native_digest, 32) ||
                word(data + 36) != bank->bank || variable->size != IMAGE_HEADER + length + 2 * fixups ||
                !length || length > GB_NATIVE_CACHE_SIZE || targets > length ||
                word(data + 40) != targets || word(data + 42) != bank->count ||
                bank->count > (length - targets) / sizeof(GbOverlayTarget)) return 0;
        const uint8_t *relocations = data + IMAGE_HEADER + length;
        unsigned previous = 0;
        for (unsigned j = 0; j < fixups; ++j) {
            unsigned offset = word(relocations + 2 * j);
            if (length < 3 || offset > length - 3 || (j && offset < previous + 3)) return 0;
            uintptr_t value = pointer(data + IMAGE_HEADER + offset);
            if (value < base || value - base >= length) return 0;
            previous = offset;
        }
        images[i] = data + IMAGE_HEADER;
        lengths[i] = length;
        fixup_counts[i] = fixups;
    }
    memset(cached, 0, sizeof cached);
    clock = gb_native_loads = gb_native_evictions = 0;
    gb_native_cache_reset();
    return 1;
}

static unsigned find_space(unsigned index)
{
    unsigned length = lengths[index];
    for (;;) {
        unsigned start = 0;
        while (start + length <= GB_NATIVE_CACHE_SIZE) {
            unsigned next = start;
            for (unsigned j = 0; j < gb_native_bank_count; ++j) {
                if (cached[j] && start < offsets[j] + lengths[j] &&
                        offsets[j] < start + length && next < offsets[j] + lengths[j]) {
                    next = offsets[j] + lengths[j];
                }
            }
            if (next == start) return start;
            start = (next + 15) & ~15u;
        }
        unsigned oldest = gb_native_bank_count;
        uint24_t age = 0;
        for (unsigned j = 0; j < gb_native_bank_count; ++j) {
            uint24_t candidate = (clock - used[j]) & 0xffffff;
            if (cached[j] && (oldest == gb_native_bank_count || candidate > age)) {
                oldest = j;
                age = candidate;
            }
        }
        if (oldest == gb_native_bank_count) return 0xffffff;
        cached[oldest] = 0;
        ++gb_native_evictions;
    }
}

GbNativeBlock gb_native_lookup(uint16_t address)
{
    uint16_t key = address < 0x4000 ? GB_NATIVE_FIXED_REGION + (address >> GB_NATIVE_FIXED_PAGE_SHIFT)
        : (gb_rom_bank << (14 - GB_NATIVE_BANK_PAGE_SHIFT)) | ((address & 0x3fff) >> GB_NATIVE_BANK_PAGE_SHIFT);
    unsigned first = 0, last = gb_native_bank_count;
    while (first < last) {
        unsigned middle = first + (last - first) / 2;
        if (gb_native_banks[middle].bank < key) first = middle + 1;
        else last = middle;
    }
    if (first == gb_native_bank_count || gb_native_banks[first].bank != key) return 0;
    unsigned index = first;
    const GbNativeBank *bank = &gb_native_banks[index];
    if (!cached[index]) {
        uint24_t evictions = gb_native_evictions;
        unsigned offset = find_space(index);
        if (evictions != gb_native_evictions) gb_native_cache_reset();
        if (offset == 0xffffff) return 0;
        uint8_t *destination = (uint8_t *)(GB_NATIVE_CACHE + offset);
        memcpy(destination, images[index], lengths[index]);
        const uint8_t *relocations = images[index] + lengths[index];
        for (unsigned j = 0; j < fixup_counts[index]; ++j) {
            uint8_t *fixup = destination + word(relocations + 2 * j);
            uint24_t value = pointer(fixup) + offset;
            fixup[0] = value;
            fixup[1] = value >> 8;
            fixup[2] = value >> 16;
        }
        offsets[index] = offset;
        cached[index] = 1;
        ++gb_native_loads;
    }
    clock = (clock + 1) & 0xffffff;
    used[index] = clock;
    uintptr_t loaded = GB_NATIVE_CACHE + offsets[index];
    const GbOverlayTarget *targets = (const GbOverlayTarget *)(loaded + ((uintptr_t)bank->targets - GB_NATIVE_CACHE));
    first = 0;
    last = bank->count;
    while (first < last) {
        unsigned middle = first + (last - first) / 2;
        if (targets[middle].address < address) first = middle + 1;
        else last = middle;
    }
    if (first == bank->count || targets[first].address != address) return 0;
    GbNativeBlock entry = targets[first].entry;
    uintptr_t value = (uintptr_t)entry;
    if (value < loaded || value - loaded >= lengths[index]) return 0;
    return entry;
}
#endif
