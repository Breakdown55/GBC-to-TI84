#include "vram_dma.h"
#include "runtime.h"

static uint16_t source;
static uint16_t destination;

static uint8_t transfer(uint8_t control)
{
    if (control & 0x80) {
        return 0; /* HBlank DMA needs a timed transfer state. */
    }

    unsigned length = ((unsigned)control + 1) * 16;
    unsigned source_end = (unsigned)source + length;
    unsigned destination_end = (unsigned)destination + length;
    if (destination_end > GB_VRAM_SIZE ||
        !(source_end <= 0x8000 || (source >= 0xa000 && source_end <= 0xe000))) {
        return 0;
    }

    /* ROM data may have been omitted by the static recompiler. Check before writing. */
    for (unsigned i = 0; i < length; ++i) {
        if (gb_read_byte(source + i) > 0xff) {
            return 0;
        }
    }
    uint8_t *bytes = &gb_vram[gb_vbk][destination];
    for (unsigned i = 0; i < length; ++i) {
        bytes[i] = (uint8_t)gb_read_byte(source + i);
    }
    source = source_end;
    destination = destination_end & (GB_VRAM_SIZE - 1);
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
    case 0x55: return transfer(value);
    default: return 0;
    }
    return 1;
}
