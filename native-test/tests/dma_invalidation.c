#include "runtime.h"
#include "render.h"
#include "timing.h"
#include "vram_dma.h"
#include <assert.h>
#include <string.h>

uint8_t gb_cgb = 1, gb_vbk = 1, gb_timing_enabled;
uint8_t gb_vram[2][GB_VRAM_SIZE];
static uint8_t data[32], contiguous, fault;
static unsigned invalidations, invalid_bank, invalid_offset, invalid_length;

uint24_t gb_read_byte(uint24_t address)
{
    return fault && address == 0x432a ? 0x100 : data[address - 0x4320];
}
const uint8_t *gb_read_contiguous(uint16_t address, unsigned length)
{
    return contiguous && address == 0x4320 && length == sizeof data ? data : 0;
}
void gb_video_invalidate(unsigned bank, unsigned offset, unsigned length)
{
    ++invalidations; invalid_bank = bank; invalid_offset = offset; invalid_length = length;
}
void gb_timing_stall(uint16_t dots) { (void)dots; }
uint8_t gb_timing_claim_hblank(void) { return 0; }

static void setup(void)
{
    gb_vram_dma_reset();
    assert(gb_vram_dma_write(0x51, 0x43));
    assert(gb_vram_dma_write(0x52, 0x20));
    assert(gb_vram_dma_write(0x53, 0x17));
    assert(gb_vram_dma_write(0x54, 0xf0));
}

int main(void)
{
    memset(data, 0xa5, sizeof data);
    for (contiguous = 0; contiguous < 2; ++contiguous) {
        memset(gb_vram, 0, sizeof gb_vram); invalidations = 0;
        setup(); assert(gb_vram_dma_write(0x55, 1));
        assert(invalidations == 1 && invalid_bank == 1 && invalid_offset == 0x17f0 && invalid_length == 32);
        assert(!memcmp(&gb_vram[1][0x17f0], data, 32) && !gb_vram[0][0x17f0]);
        setup(); assert(gb_vram_dma_write(0x55, 1)); assert(invalidations == 1);
    }
    contiguous = 0; fault = 1; invalidations = 0;
    memset(gb_vram, 0, sizeof gb_vram);
    setup(); assert(!gb_vram_dma_write(0x55, 1));
    assert(!invalidations && !gb_vram[1][0x17f0]);
    fault = 0; gb_timing_enabled = 1;
    setup(); assert(gb_vram_dma_write(0x55, 0x81));
    assert(!invalidations);
    assert(gb_vram_dma_hblank() == GB_VRAM_DMA_COPIED);
    assert(invalidations == 1 && invalid_offset == 0x17f0 && invalid_length == 16);
    assert(gb_vram_dma_hblank() == GB_VRAM_DMA_COPIED);
    assert(invalidations == 2 && invalid_offset == 0x1800 && invalid_length == 16);
    assert(gb_vram_dma_hblank() == GB_VRAM_DMA_IDLE);
}
