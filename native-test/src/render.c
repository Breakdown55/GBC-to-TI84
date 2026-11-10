#include "render.h"
#include "runtime.h"

#include <stdbool.h>
#include <string.h>

enum {
    LCD_ENABLE = 0x80, WINDOW_MAP = 0x40, WINDOW_ENABLE = 0x20,
    UNSIGNED_TILES = 0x10, BG_MAP = 0x08, TALL_OBJ = 0x04,
    OBJ_ENABLE = 0x02, BG_ENABLE_PRIORITY = 0x01,
    BGPI = 0x68, BGPD = 0x69, OBPI = 0x6a, OBPD = 0x6b, OPRI = 0x6c,
    BG_PALETTE = 0, OBJ_PALETTE = 1, PALETTE_SIZE = 64,
    X_FLIP = 0x20, Y_FLIP = 0x40, PRIORITY = 0x80, OBJ_CLAIMED = 0x04,
    MAX_SCANLINE_OBJECTS = 10,
};

uint8_t gb_oam[GB_OAM_SIZE];
uint8_t gb_video_dirty;
static uint8_t palettes[2][PALETTE_SIZE];
static uint8_t palette_indexes[2];
static uint8_t bgp, obp0, obp1, obj_x_priority;

void gb_video_init(void)
{
    memset(palettes[BG_PALETTE], 0xff, PALETTE_SIZE);
    memset(palettes[OBJ_PALETTE], 0, PALETTE_SIZE);
    memset(palette_indexes, 0, sizeof(palette_indexes));
    memset(gb_oam, 0, sizeof(gb_oam));
    bgp = 0xfc;
    obp0 = obp1 = 0xff;
    obj_x_priority = 0;
}

uint8_t gb_video_read(uint8_t offset)
{
    switch (offset) {
    case 0x47: return bgp;
    case 0x48: return obp0;
    case 0x49: return obp1;
    case BGPI: return gb_cgb ? palette_indexes[BG_PALETTE] | 0x40 : 0xff;
    case OBPI: return gb_cgb ? palette_indexes[OBJ_PALETTE] | 0x40 : 0xff;
    case BGPD: return gb_cgb ? palettes[BG_PALETTE][palette_indexes[BG_PALETTE] & 0x3f] : 0xff;
    case OBPD: return gb_cgb ? palettes[OBJ_PALETTE][palette_indexes[OBJ_PALETTE] & 0x3f] : 0xff;
    case OPRI: return gb_cgb ? obj_x_priority | 0xfe : 0xff;
    default: return 0xff;
    }
}

static void write_palette(unsigned group, uint8_t value)
{
    uint8_t index = palette_indexes[group];
    gb_video_store(&palettes[group][index & 0x3f], value);
    if (index & 0x80) {
        palette_indexes[group] = 0x80 | ((index + 1) & 0x3f);
    }
}

void gb_video_write(uint8_t offset, uint8_t value)
{
    switch (offset) {
    case 0x47: gb_video_store(&bgp, value); return;
    case 0x48: gb_video_store(&obp0, value); return;
    case 0x49: gb_video_store(&obp1, value); return;
    default: break;
    }
    if (!gb_cgb) {
        return;
    }
    switch (offset) {
    case BGPI: palette_indexes[BG_PALETTE] = value & 0xbf; break;
    case OBPI: palette_indexes[OBJ_PALETTE] = value & 0xbf; break;
    case BGPD: write_palette(BG_PALETTE, value); break;
    case OBPD: write_palette(OBJ_PALETTE, value); break;
    case OPRI: gb_video_store(&obj_x_priority, value & 1); break;
    default: break;
    }
}

static uint16_t palette_color(unsigned group, uint8_t attributes, uint8_t color)
{
    if (gb_cgb) {
        unsigned index = ((attributes & 7) * 4 + color) * 2;
        uint16_t rgb555 = palettes[group][index] | ((uint16_t)palettes[group][index + 1] << 8);
        uint16_t green = (rgb555 >> 5) & 31;
        return ((rgb555 & 31) << 11) | (green << 6) | ((green >> 4) << 5) | ((rgb555 >> 10) & 31);
    }

    static const uint16_t shades[] = {0xffff, 0xad55, 0x52aa, 0x0000};
    uint8_t mapping = group == BG_PALETTE ? bgp : (attributes & 0x10 ? obp1 : obp0);
    return shades[(mapping >> (color * 2)) & 3];
}

static uint8_t tile_pixel(const uint8_t *row, unsigned x, uint8_t attributes)
{
    unsigned shift = (attributes & X_FLIP) ? x : 7 - x;
    return ((row[0] >> shift) & 1) | (((row[1] >> shift) & 1) << 1);
}

static void render_background(uint8_t y, uint16_t *pixels, uint8_t *background)
{
    if (!gb_cgb && !(gb_lcdc & BG_ENABLE_PRIORITY)) {
        memset(background, 0, GB_SCREEN_WIDTH);
        memset(pixels, 0xff, GB_SCREEN_WIDTH * sizeof(*pixels));
        return;
    }

    int window_x = (int)gb_wx - 7;
    bool window_enabled = (gb_lcdc & WINDOW_ENABLE) && y >= gb_wy;
    uint16_t colors[8][4];
    unsigned palette_count = gb_cgb ? 8 : 1;
    for (unsigned palette = 0; palette < palette_count; ++palette) {
        for (unsigned color = 0; color < 4; ++color) {
            colors[palette][color] = palette_color(BG_PALETTE, palette, color);
        }
    }

    for (int x = 0; x < GB_SCREEN_WIDTH;) {
        bool window = window_enabled && x >= window_x;
        /* The snapshot uses one set of registers for all scanlines. */
        uint8_t tile_x = window ? x - window_x : x + gb_scx;
        uint8_t tile_y = window ? y - gb_wy : y + gb_scy;
        unsigned map = (gb_lcdc & (window ? WINDOW_MAP : BG_MAP)) ? 0x1c00 : 0x1800;
        unsigned cell = map + (tile_y >> 3) * 32 + (tile_x >> 3);
        uint8_t tile = gb_vram[0][cell];
        uint8_t attributes = gb_cgb ? gb_vram[1][cell] : 0;
        unsigned bank = (attributes >> 3) & 1;
        unsigned row = tile_y & 7;
        if (attributes & Y_FLIP) {
            row = 7 - row;
        }
        unsigned address = (gb_lcdc & UNSIGNED_TILES) ? tile * 16 : 0x1000 + (int8_t)tile * 16;
        const uint8_t *tile_row = &gb_vram[bank][address + row * 2];
        unsigned column = tile_x & 7;
        int end = x + (int)(8 - column);
        if (window_enabled && !window && window_x < end) {
            end = window_x;
        }
        if (end > GB_SCREEN_WIDTH) {
            end = GB_SCREEN_WIDTH;
        }
        const uint16_t *palette = colors[attributes & 7];
        uint8_t priority = attributes & PRIORITY;
        bool flip = attributes & X_FLIP;
        uint8_t mask = flip ? 1 << column : 0x80 >> column;
        for (; x < end; ++x) {
            uint8_t color = ((tile_row[0] & mask) != 0) | (((tile_row[1] & mask) != 0) << 1);
            background[x] = color | priority;
            pixels[x] = palette[color];
            mask = flip ? mask << 1 : mask >> 1;
        }
    }
}

static void render_objects(uint8_t y, uint16_t *pixels, uint8_t *background)
{
    uint8_t objects[MAX_SCANLINE_OBJECTS];
    unsigned count = 0;
    int height = (gb_lcdc & TALL_OBJ) ? 16 : 8;
    bool sort_by_x = !gb_cgb || obj_x_priority;
    for (unsigned offset = 0; offset < sizeof(gb_oam) && count < MAX_SCANLINE_OBJECTS; offset += 4) {
        int row = y + 16 - gb_oam[offset];
        if (row < 0 || row >= height) {
            continue;
        }
        unsigned index = count++;
        while (sort_by_x && index && gb_oam[objects[index - 1] + 1] > gb_oam[offset + 1]) {
            objects[index] = objects[index - 1];
            --index;
        }
        objects[index] = offset;
    }

    for (unsigned index = 0; index < count; ++index) {
        const uint8_t *object = &gb_oam[objects[index]];
        int start = (int)object[1] - 8;
        uint8_t attributes = object[3];
        unsigned row = y + 16 - object[0];
        if (attributes & Y_FLIP) {
            row = height - 1 - row;
        }
        unsigned tile = (height == 16) ? (object[2] & 0xfe) : object[2];
        unsigned bank = gb_cgb ? ((attributes >> 3) & 1) : 0;
        const uint8_t *data = &gb_vram[bank][tile * 16 + row * 2];

        for (unsigned column = 0; column < 8; ++column) {
            int x = start + (int)column;
            if (x < 0 || x >= GB_SCREEN_WIDTH || (background[x] & OBJ_CLAIMED)) {
                continue;
            }
            uint8_t color = tile_pixel(data, column, attributes);
            if (!color) {
                continue;
            }
            uint8_t bg = background[x];
            background[x] |= OBJ_CLAIMED;
            bool priority = !gb_cgb || (gb_lcdc & BG_ENABLE_PRIORITY);
            if (!(priority && (bg & 3) && ((bg | attributes) & PRIORITY))) {
                pixels[x] = palette_color(OBJ_PALETTE, attributes, color);
            }
        }
    }
}

void gb_render_scanline(uint8_t y, uint16_t pixels[GB_SCREEN_WIDTH])
{
    if (!(gb_lcdc & LCD_ENABLE) || y >= GB_SCREEN_HEIGHT) {
        memset(pixels, 0xff, GB_SCREEN_WIDTH * sizeof(*pixels));
        return;
    }

    uint8_t background[GB_SCREEN_WIDTH];
    render_background(y, pixels, background);
    if (gb_lcdc & OBJ_ENABLE) {
        render_objects(y, pixels, background);
    }
}

#ifdef __TICE__
#include <sys/lcd.h>

enum {
    LCD_PIXEL_FORMAT = 7 << 1,
    LCD_PIXEL_ORDER = 7 << 8,
    LCD_RGB565 = (6 << 1) | (1 << 8),
};

static bool frame_active;
static uint24_t saved_control, saved_base;

void gb_present_frame(void)
{
    if (frame_active && !gb_video_dirty) {
        return;
    }
    if (!frame_active) {
        saved_control = lcd_Control;
        saved_base = lcd_UpBase;
        frame_active = true;
        lcd_Control = (saved_control & ~(LCD_PIXEL_FORMAT | LCD_PIXEL_ORDER)) | LCD_RGB565;
        lcd_UpBase = (uint24_t)lcd_Ram;
        memset(lcd_Ram, 0, LCD_SIZE);
    }

    uint16_t *line = (uint16_t *)lcd_Ram + (LCD_HEIGHT - GB_SCREEN_HEIGHT) / 2 * LCD_WIDTH
                                      + (LCD_WIDTH - GB_SCREEN_WIDTH) / 2;
    for (uint8_t y = 0; y < GB_SCREEN_HEIGHT; ++y) {
        gb_render_scanline(y, line);
        line += LCD_WIDTH;
    }
    gb_video_dirty = 0;
}

void gb_end_frame(void)
{
    if (frame_active) {
        lcd_UpBase = saved_base;
        lcd_Control = saved_control;
        frame_active = false;
    }
}
#endif
