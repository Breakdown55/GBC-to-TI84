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
uint8_t gb_oam_dirty;
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

enum { ROW_CACHE_SIZE = 32, TILE_WIDTH = 8 };

typedef struct {
    uint16_t pixels[TILE_WIDTH];
    uint8_t background[TILE_WIDTH];
    uint8_t attributes, low, high;
    uint8_t padding[5]; /* Keep the cache stride a power of two on eZ80. */
} TileRow;

static TileRow row_cache[ROW_CACHE_SIZE];
static uint16_t bg_colors[8][4];
static uint16_t obj_colors[8][4];

static void prepare_frame(void)
{
    for (unsigned i = 0; i < ROW_CACHE_SIZE; ++i) {
        row_cache[i].attributes = 0;
    }
    unsigned count = gb_cgb ? 8 : 1;
    for (unsigned palette = 0; palette < count; ++palette) {
        for (unsigned color = 0; color < 4; ++color) {
            bg_colors[palette][color] = palette_color(BG_PALETTE, palette, color);
        }
    }
    if (gb_lcdc & OBJ_ENABLE) {
        count = gb_cgb ? 8 : 2;
        for (unsigned palette = 0; palette < count; ++palette) {
            for (unsigned color = 1; color < 4; ++color) {
                obj_colors[palette][color] = palette_color(OBJ_PALETTE, gb_cgb ? palette : palette << 4, color);
            }
        }
    }
}

static const TileRow *background_row(const uint8_t *planes, uint8_t attributes)
{
    uint8_t low = planes[0], high = planes[1];
    uint8_t key = (attributes & (PRIORITY | X_FLIP | 7)) | 0x10;
    unsigned slot = (low ^ (low >> 5) ^ high ^ (high >> 3) ^ key ^ (key >> 4)) & (ROW_CACHE_SIZE - 1);
    TileRow *row = &row_cache[slot];
    if (row->attributes != key || row->low != low || row->high != high) {
        row->attributes = key;
        row->low = low;
        row->high = high;
        const uint16_t *palette = bg_colors[attributes & 7];
        uint8_t priority = attributes & PRIORITY;
        bool flip = attributes & X_FLIP;
        unsigned column = flip ? 0 : 7;
        int step = flip ? 1 : -1;
        for (unsigned i = 0; i < TILE_WIDTH; ++i) {
            uint8_t color = (low & 1) | ((high & 1) << 1);
            row->background[column] = color | priority;
            row->pixels[column] = palette[color];
            low >>= 1;
            high >>= 1;
            column += step;
        }
    }
    return row;
}

static void render_bg_span(uint8_t tile_x, uint8_t tile_y, unsigned map, unsigned remaining,
                           uint16_t *pixels, uint8_t *background)
{
    unsigned map_row = map + (tile_y >> 3) * 32;
    const uint8_t *tiles = &gb_vram[0][map_row];
    const uint8_t *attrs = &gb_vram[1][map_row];
    unsigned y_offset = (tile_y & 7) * 2;
    bool unsigned_tiles = gb_lcdc & UNSIGNED_TILES;
    while (remaining) {
        unsigned cell = tile_x >> 3;
        uint8_t tile = tiles[cell];
        uint8_t attributes = gb_cgb ? attrs[cell] : 0;
        unsigned bank = (attributes >> 3) & 1;
        unsigned offset = unsigned_tiles ? tile * 16 : 0x1000 + (int8_t)tile * 16;
        offset += attributes & Y_FLIP ? 14 - y_offset : y_offset;
        const TileRow *row = background_row(&gb_vram[bank][offset], attributes);
        unsigned column = tile_x & 7;
        unsigned count = TILE_WIDTH - column;
        if (count > remaining) {
            count = remaining;
        }
        memcpy(pixels, row->pixels + column, count * sizeof(*pixels));
        if (background) {
            memcpy(background, row->background + column, count);
            background += count;
        }
        pixels += count;
        tile_x += count;
        remaining -= count;
    }
}

static void render_background(uint8_t y, uint16_t *pixels, uint8_t *background)
{
    if (!gb_cgb && !(gb_lcdc & BG_ENABLE_PRIORITY)) {
        if (background) {
            memset(background, 0, GB_SCREEN_WIDTH);
        }
        memset(pixels, 0xff, GB_SCREEN_WIDTH * sizeof(*pixels));
        return;
    }

    int window_x = (int)gb_wx - 7;
    bool window = (gb_lcdc & WINDOW_ENABLE) && y >= gb_wy && window_x < GB_SCREEN_WIDTH;
    unsigned bg_width = window ? (window_x < 0 ? 0 : window_x) : GB_SCREEN_WIDTH;
    render_bg_span(gb_scx, y + gb_scy, (gb_lcdc & BG_MAP) ? 0x1c00 : 0x1800,
                   bg_width, pixels, background);
    if (window) {
        render_bg_span(bg_width - window_x, y - gb_wy, (gb_lcdc & WINDOW_MAP) ? 0x1c00 : 0x1800,
                       GB_SCREEN_WIDTH - bg_width, pixels + bg_width,
                       background ? background + bg_width : NULL);
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

        const uint16_t *palette = obj_colors[gb_cgb ? (attributes & 7) : ((attributes >> 4) & 1)];
        bool priority = !gb_cgb || (gb_lcdc & BG_ENABLE_PRIORITY);
        bool flip = attributes & X_FLIP;
        uint8_t mask = flip ? 1 : 0x80;
        for (int x = start; x < start + TILE_WIDTH; ++x) {
            uint8_t color = ((data[0] & mask) != 0) | (((data[1] & mask) != 0) << 1);
            mask = flip ? mask << 1 : mask >> 1;
            if (!color || x < 0 || x >= GB_SCREEN_WIDTH || (background[x] & OBJ_CLAIMED)) {
                continue;
            }
            uint8_t bg = background[x];
            background[x] |= OBJ_CLAIMED;
            if (!(priority && (bg & 3) && ((bg | attributes) & PRIORITY))) {
                pixels[x] = palette[color];
            }
        }
    }
}

static void render_scanline(uint8_t y, uint16_t pixels[GB_SCREEN_WIDTH])
{
    if (!(gb_lcdc & LCD_ENABLE) || y >= GB_SCREEN_HEIGHT) {
        memset(pixels, 0xff, GB_SCREEN_WIDTH * sizeof(*pixels));
        return;
    }

    uint8_t background[GB_SCREEN_WIDTH];
    bool objects = gb_lcdc & OBJ_ENABLE;
    render_background(y, pixels, objects ? background : NULL);
    if (objects) {
        render_objects(y, pixels, background);
    }
}

void gb_render_scanline(uint8_t y, uint16_t pixels[GB_SCREEN_WIDTH])
{
    prepare_frame();
    render_scanline(y, pixels);
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
static uint8_t previous_object_start, previous_object_end;

void gb_present_frame(void)
{
    if (frame_active && !gb_video_dirty && !gb_oam_dirty) {
        return;
    }
    bool full_redraw = !frame_active || gb_video_dirty;
    if (!frame_active) {
        saved_control = lcd_Control;
        saved_base = lcd_UpBase;
        frame_active = true;
        lcd_Control = (saved_control & ~(LCD_PIXEL_FORMAT | LCD_PIXEL_ORDER)) | LCD_RGB565;
        lcd_UpBase = (uint24_t)lcd_Ram;
        memset(lcd_Ram, 0, LCD_SIZE);
    }

    uint8_t object_start = GB_SCREEN_HEIGHT, object_end = 0;
    if ((gb_lcdc & (LCD_ENABLE | OBJ_ENABLE)) == (LCD_ENABLE | OBJ_ENABLE)) {
        int height = gb_lcdc & TALL_OBJ ? 16 : 8;
        for (unsigned offset = 0; offset < GB_OAM_SIZE; offset += 4) {
            int top = gb_oam[offset] - 16;
            int bottom = top + height;
            if (bottom > 0 && top < GB_SCREEN_HEIGHT) {
                if (top < object_start) {
                    object_start = top < 0 ? 0 : top;
                }
                if (bottom > object_end) {
                    object_end = bottom > GB_SCREEN_HEIGHT ? GB_SCREEN_HEIGHT : bottom;
                }
            }
        }
    }
    /* Restore the previous sprite rows as well as drawing the current sprites. */
    uint8_t first = object_start < previous_object_start ? object_start : previous_object_start;
    uint8_t end = object_end > previous_object_end ? object_end : previous_object_end;
    previous_object_start = object_start;
    previous_object_end = object_end;
    if (full_redraw) {
        first = 0;
        end = GB_SCREEN_HEIGHT;
    }
    if (first < end) {
        uint16_t *line = (uint16_t *)lcd_Ram + ((LCD_HEIGHT - GB_SCREEN_HEIGHT) / 2 + first) * LCD_WIDTH
                                          + (LCD_WIDTH - GB_SCREEN_WIDTH) / 2;
        prepare_frame();
        for (uint8_t y = first; y < end; ++y) {
            render_scanline(y, line);
            line += LCD_WIDTH;
        }
    }
    gb_oam_dirty = 0;
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
