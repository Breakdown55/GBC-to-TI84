#ifndef GB_NATIVE_OVERLAY_H
#define GB_NATIVE_OVERLAY_H
#include "runtime.h"

typedef struct __attribute__((packed)) {
    uint16_t address;
    GbNativeBlock entry;
} GbOverlayTarget;

typedef struct __attribute__((packed)) {
    uint16_t bank;
    const GbOverlayTarget *targets;
    uint16_t count;
} GbNativeBank;

#if GB_NATIVE_OVERLAYS
extern const GbNativeBank gb_native_banks[];
extern const unsigned gb_native_bank_count;
extern char gb_native_archive_name[9];
extern uint24_t gb_native_loads;
extern uint24_t gb_native_evictions;
uint8_t gb_native_init(void);
GbNativeBlock gb_native_lookup(uint16_t address);
void gb_native_cache_reset(void);
#endif
#endif
