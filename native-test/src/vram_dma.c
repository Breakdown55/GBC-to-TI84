#include "vram_dma.h"
#include "runtime.h"
#include "render.h"
#include "timing.h"
#include <string.h>

enum { BLOCK_SIZE = 16, HBLANK_MODE = 0x80 };

static uint16_t source;
static uint16_t destination;
static uint8_t blocks_left;
static uint8_t hblank_active;

void gb_vram_dma_reset(void)
{
    source = 0;
    destination = 0;
    blocks_left = 0;
    hblank_active = 0;
}

uint8_t gb_vram_dma_read(uint8_t offset)
{
    if (!gb_cgb || offset != 0x55 || !blocks_left) {
        return 0xff;
    }
    return (hblank_active ? 0 : HBLANK_MODE) | (blocks_left - 1);
}

static uint8_t transfer(unsigned length)
{
    unsigned source_end = (unsigned)source + length;
    unsigned destination_end = (unsigned)destination + length;
    if (destination_end > GB_VRAM_SIZE ||
        !(source_end <= 0x8000 || (source >= 0xa000 && source_end <= 0xe000))) {
        return 0;
    }

    uint8_t *bytes = &gb_vram[gb_vbk][destination];
    const uint8_t *data = gb_read_contiguous(source, length);
    if (data) {
        if (memcmp(bytes, data, length)) {
            memcpy(bytes, data, length);
            gb_video_invalidate(gb_vbk, destination, length);
        }
    } else {
        /* Validate all bytes before writing when the source isn't contiguous. */
        for (unsigned i = 0; i < length; ++i) {
            if (gb_read_byte(source + i) > 0xff) {
                return 0;
            }
        }
        uint8_t changed = 0;
        for (unsigned i = 0; i < length; ++i) {
            uint8_t value = (uint8_t)gb_read_byte(source + i);
            if (bytes[i] != value) {
                bytes[i] = value;
                changed = 1;
            }
        }
        if (changed) gb_video_invalidate(gb_vbk, destination, length);
    }
    source = source_end;
    destination = destination_end;
    return 1;
}

uint8_t gb_vram_dma_hblank(void)
{
    if (!hblank_active) {
        return GB_VRAM_DMA_IDLE;
    }
    if (!transfer(BLOCK_SIZE)) {
        hblank_active = 0;
        return GB_VRAM_DMA_FAULT;
    }
    --blocks_left;
    if (!blocks_left || destination == GB_VRAM_SIZE) {
        hblank_active = 0;
        destination &= GB_VRAM_SIZE - 1;
    }
    return GB_VRAM_DMA_COPIED;
}

static uint8_t start(uint8_t control)
{
    uint8_t length = (control & 0x7f) + 1;
    if (!(control & HBLANK_MODE)) {
        if (hblank_active) {
            hblank_active = 0;
            return 1;
        }
        if (!transfer((unsigned)length * BLOCK_SIZE)) {
            return 0;
        }
        blocks_left = 0;
        destination &= GB_VRAM_SIZE - 1;
        gb_timing_stall((uint16_t)length * GB_VRAM_DMA_BLOCK_DOTS);
        return 1;
    }
    if (!gb_timing_enabled) {
        return 0;
    }
    hblank_active = 1;
    blocks_left = length;
    if (gb_timing_claim_hblank()) {
        uint8_t result = gb_vram_dma_hblank();
        if (result == GB_VRAM_DMA_FAULT) {
            return 0;
        }
        gb_timing_stall(GB_VRAM_DMA_BLOCK_DOTS);
    }
    return 1;
}

uint8_t gb_vram_dma_write(uint8_t offset, uint8_t value)
{
    if (!gb_cgb) {
        return 1;
    }
    switch (offset) {
    case 0x51: source = (source & 0xf0) | ((uint16_t)value << 8); break;
    case 0x52: source = (source & 0xff00) | (value & 0xf0); break;
    case 0x53: destination = (destination & 0xf0) | ((uint16_t)(value & 0x1f) << 8); break;
    case 0x54: destination = (destination & 0x1f00) | (value & 0xf0); break;
    case 0x55: return start(value);
    default: return 0;
    }
    return 1;
}
