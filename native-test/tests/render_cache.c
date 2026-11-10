#include "render.h"
#include "runtime.h"
#include <sys/lcd.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>

uint8_t test_lcd[LCD_SIZE + 16];
uint16_t test_palette[256];
uintptr_t test_control, test_base;
uint8_t gb_cgb, gb_lcdc, gb_scx, gb_scy, gb_wx, gb_wy;
uint8_t gb_vram[2][GB_VRAM_SIZE];
extern uint8_t reference_oam[GB_OAM_SIZE];
void reference_init(void);
void reference_write(uint8_t, uint8_t);
void reference_invalidate(unsigned, unsigned, unsigned);
void reference_line(uint8_t, uint16_t *);

static uint32_t seed = 19;
static uint8_t random_byte(void)
{
    seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5;
    return seed;
}

static void port(uint8_t address, uint8_t value)
{
    gb_video_write(address, value);
    reference_write(address, value);
}

static void verify(unsigned scene, unsigned step)
{
    gb_present_frame();
    /* The oracle always decodes VRAM afresh and composes complete scanlines.
     * It never reads the retained background or the live renderer's caches. */
    reference_invalidate(0, 0, GB_VRAM_SIZE);
    reference_invalidate(1, 0, GB_VRAM_SIZE);
    for (unsigned y = 0; y < GB_SCREEN_HEIGHT; ++y) {
        uint16_t expected[GB_SCREEN_WIDTH];
        reference_line(y, expected);
        for (unsigned x = 0; x < GB_SCREEN_WIDTH; ++x) {
            uint16_t rgb = expected[x];
            uint16_t packed = (rgb & 31) | ((rgb >> 1) & 0x7fe0) | ((rgb & 0x20) << 10);
            uint8_t index = test_lcd[(y + 48) * 320 + x + 80];
            if (test_palette[index] != packed) {
                fprintf(stderr, "scene %u step %u pixel %u,%u\n", scene, step, x, y);
                assert(0);
            }
        }
    }
    for (unsigned i = LCD_SIZE; i < sizeof test_lcd; ++i) assert(test_lcd[i] == 0xa5);
}

int main(void)
{
    for (unsigned scene = 0; scene < 48; ++scene) {
        memset(test_lcd, 0xa5, sizeof test_lcd);
        for (unsigned i = 0; i < 256; ++i) test_palette[i] = i * 193;
        uint16_t saved[256]; memcpy(saved, test_palette, sizeof saved);
        test_control = 0x92b; test_base = 0x1234;
        gb_cgb = scene & 1; gb_lcdc = random_byte() | 0x80;
        gb_scx = random_byte(); gb_scy = random_byte();
        gb_wx = random_byte(); gb_wy = random_byte() % 144;
        gb_video_init(); reference_init();
        for (unsigned bank = 0; bank < 2; ++bank)
            for (unsigned i = 0; i < GB_VRAM_SIZE; ++i) gb_vram[bank][i] = random_byte();
        for (unsigned i = 0; i < GB_OAM_SIZE; ++i) gb_oam[i] = reference_oam[i] = random_byte();
        for (unsigned i = 0; i < 64; ++i) {
            port(0x68, i); port(0x69, random_byte());
            port(0x6a, i); port(0x6b, random_byte());
        }
        verify(scene, 0);
        for (unsigned step = 1; step <= 14; ++step) {
            switch (step) {
            case 1: break; /* No changes. */
            case 2: case 8: /* Only sprites move, including their old coverage. */
                for (unsigned i = 0; i < GB_OAM_SIZE; ++i) {
                    uint8_t value = random_byte(); gb_oam_store(i, value); reference_oam[i] = value;
                }
                break;
            case 3: /* Palette-only update. */
                port(0x47, random_byte()); port(0x48, random_byte());
                port(0x68, 0x80); port(0x69, random_byte());
                break;
            case 4: case 5: /* CPU-sized writes and DMA-sized ranges in both banks. */
                for (unsigned bank = 0; bank < 2; ++bank) {
                    unsigned start = step == 4 ? 17 : 0x17f0;
                    unsigned length = step == 4 ? 1 : 48;
                    for (unsigned i = 0; i < length; ++i) gb_vram[bank][start + i] ^= 0xff;
                    gb_video_invalidate(bank, start, length);
                }
                break;
            case 6:
                gb_scx += 7; gb_scy += 9; gb_video_dirty = 1; break;
            case 7:
                gb_wx = 3; gb_wy = 17; gb_lcdc ^= 0x70; gb_video_dirty = 1; break;
            case 9: case 10:
                gb_lcdc ^= 0x80; gb_video_dirty = 1; break;
            case 11:
                gb_lcdc ^= 0x0f; gb_video_dirty = 1; break;
            case 12:
                gb_vram[1][0x1fff] ^= 0xff; gb_video_invalidate(1, 0x1fff, 1); break;
            case 13:
                gb_lcdc = (gb_lcdc | 0x91) & ~0x20;
                gb_scx = 254; gb_scy = 2; gb_video_dirty = 1;
                break;
            case 14: /* Reuse the overlap across wrapping scroll and VRAM edits. */
                gb_scx += 3; gb_scy -= 5;
                for (unsigned bank = 0; bank < 2; ++bank) {
                    unsigned map = gb_lcdc & 8 ? 0x1c00 : 0x1800;
                    gb_vram[bank][map] ^= 0xff;
                    gb_video_invalidate(bank, map, 1);
                }
                break;
            }
            verify(scene, step);
        }
        gb_end_frame();
        assert(test_control == 0x92b && test_base == 0x1234);
        assert(!memcmp(saved, test_palette, sizeof saved));
        /* Re-enter after another screen owner overwrites all LCD cache memory. */
        memset(test_lcd, 0xa5, sizeof test_lcd);
        verify(scene, 15);
        gb_end_frame();
    }
    puts("768 cached frames match fresh scanline rendering; LCD state restored");
}
