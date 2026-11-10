#include "timing.h"
#include "runtime.h"
#include "vram_dma.h"
#include <assert.h>

volatile uint8_t gb_a, gb_f, gb_ime;
volatile uint24_t gb_pc;
uint8_t gb_key1, gb_tac, gb_tma, gb_lcdc, gb_ly, gb_lyc, gb_stat, gb_if, gb_ie;
uint8_t gb_video_dirty, gb_cgb;

void gb_vram_dma_reset(void) {}
uint8_t gb_vram_dma_hblank(void) { return GB_VRAM_DMA_IDLE; }
uint8_t gb_push_word(uint24_t value) { (void)value; assert(0); return 0; }

static void setup(uint8_t speed)
{
    gb_lcdc = 0x80;
    gb_key1 = speed;
    gb_if = gb_ie = gb_stat = gb_tac = 0;
    gb_a = 0xa5;
    gb_f = 0;
    gb_timing_init();
    gb_timing_write(0x07, 0x05); /* A TIMA increment every 16 CPU cycles. */
}

int main(void)
{
    setup(0);
    gb_timing_skip_poll(0x0090); /* LDH LY, CP $90, JR NZ. */
    assert(gb_timing_read(0x05) == 4); /* Two 32-cycle iterations. */
    assert((gb_timing_read(0x41) & 3) == 2);
    assert(gb_a == 0 && gb_f == 0x50);
    gb_timing_skip_poll(0x0090);
    assert(gb_timing_read(0x05) == 4); /* The remaining time is under one iteration. */

    setup(0);
    gb_timing_skip_poll(0x0402); /* LDH STAT, AND 2, JR NZ. */
    assert(gb_timing_read(0x05) == 4 && gb_a == 2 && gb_f == 0x20);
    setup(0);
    gb_f = 0x10;
    gb_timing_skip_poll(0x0802); /* LDH STAT, BIT 1,A, JR NZ. */
    assert(gb_timing_read(0x05) == 4 && gb_a == 0x86 && gb_f == 0x30);
    setup(0);
    gb_timing_skip_poll(0x0000); /* The first comparison exits. */
    assert(gb_timing_read(0x05) == 0 && gb_a == 0xa5);

    setup(0x80);
    gb_timing_skip_poll(0x0090);
    assert(gb_timing_read(0x05) == 8 && (gb_timing_read(0x41) & 3) == 2);

    setup(0);
    gb_timing_enable_ime();
    gb_ie = 4;
    gb_timing_write(0x05, 0xff);
    gb_timing_skip_poll(0x0090);
    assert(gb_timing_read(0x05) == 0xff && gb_a == 0xa5);
    setup(0);
    gb_ei();
    gb_timing_skip_poll(0x0090);
    assert(gb_timing_read(0x05) == 0 && gb_a == 0xa5);
}
