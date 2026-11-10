#include "render.h"
#include "runtime.h"

#include <stdbool.h>
#include <string.h>

#ifdef __TICE__
#include <sys/lcd.h>
#endif

/* Indexed display uses only the first half of LCD RAM. Keep reusable graphics
 * in the second half, outside the program's scarce code/data allocation. */
enum { TILE_COUNT = 768, TILE_PIXELS = 64, BACKGROUND_BYTES = 160 * 144,
       CACHE_BYTES = TILE_COUNT * TILE_PIXELS + BACKGROUND_BYTES + TILE_COUNT * 2 + 2048 };
#ifdef __TICE__
_Static_assert(CACHE_BYTES <= LCD_SIZE / 2, "graphics cache exceeds spare LCD RAM");
#define graphics_cache ((uint8_t *)lcd_Ram + LCD_SIZE / 2)
#else
static uint8_t graphics_cache[CACHE_BYTES];
#endif
#define background_pixels (graphics_cache + TILE_COUNT * TILE_PIXELS)
#define tile_valid (background_pixels + BACKGROUND_BYTES)
#define tile_changed (tile_valid + TILE_COUNT)
#define map_changed (tile_changed + TILE_COUNT)
static bool background_valid;
static bool incremental_background;
static uint8_t background_start, background_end;
static bool background_vram_dirty;

void gb_video_invalidate(unsigned bank, unsigned offset, unsigned length)
{
    /* All CPU stores and DMA writes invalidate the physical VRAM bank. Map
     * writes affect placement but do not invalidate decoded tile graphics. */
    gb_video_dirty = 1;
    if (length) background_vram_dirty = true;
    if (length && offset < 0x1800) {
        unsigned end = offset + length;
        if (end > 0x1800) end = 0x1800;
        unsigned first = offset / 16;
        memset(tile_valid + bank * 384 + first, 0, (end - 1) / 16 - first + 1);
        memset(tile_changed + bank * 384 + first, 1, (end - 1) / 16 - first + 1);
    }
    unsigned end = offset + length;
    if (end > 0x1800) {
        unsigned first = offset < 0x1800 ? 0x1800 : offset;
        memset(map_changed + first - 0x1800, 1, end - first);
    }
}

static const uint8_t *decoded_tile(unsigned bank, unsigned tile)
{
    unsigned index = bank * 384 + tile;
    uint8_t *pixels = graphics_cache + index * TILE_PIXELS;
    if (!tile_valid[index]) {
        const uint8_t *source = &gb_vram[bank][tile * 16];
        for (unsigned row = 0; row < 8; ++row) {
            uint8_t low = *source++, high = *source++;
            for (unsigned x = 0; x < 8; ++x) {
                pixels[row * 8 + 7 - x] = (low & 1) | ((high & 1) << 1);
                low >>= 1;
                high >>= 1;
            }
        }
        tile_valid[index] = 1;
    }
    return pixels;
}

enum {
    LCD_ENABLE = 0x80, WINDOW_MAP = 0x40, WINDOW_ENABLE = 0x20,
    UNSIGNED_TILES = 0x10, BG_MAP = 0x08, TALL_OBJ = 0x04,
    OBJ_ENABLE = 0x02, BG_ENABLE_PRIORITY = 0x01,
    BGPI = 0x68, BGPD = 0x69, OBPI = 0x6a, OBPD = 0x6b, OPRI = 0x6c,
    BG_PALETTE = 0, OBJ_PALETTE = 1, PALETTE_SIZE = 64,
    X_FLIP = 0x20, Y_FLIP = 0x40, PRIORITY = 0x80,
    OBJ_INDEX = 32, BLACK_INDEX = 64, WHITE_INDEX = 68, COLOR_COUNT = WHITE_INDEX + 1,
    MAX_SCANLINE_OBJECTS = 10,
};

uint8_t gb_oam[GB_OAM_SIZE];
uint8_t gb_video_dirty;
uint8_t gb_oam_dirty;
static uint8_t palettes[2][PALETTE_SIZE];
static uint8_t palette_indexes[2];
static uint8_t bgp, obp0, obp1, obj_x_priority;
static uint8_t palette_dirty;

void gb_video_init(void)
{
    memset(tile_valid, 0, TILE_COUNT);
    memset(tile_changed, 1, TILE_COUNT);
    memset(map_changed, 1, 2048);
    background_valid = false;
    incremental_background = false;
    background_vram_dirty = true;
    memset(palettes[BG_PALETTE], 0xff, PALETTE_SIZE);
    memset(palettes[OBJ_PALETTE], 0, PALETTE_SIZE);
    memset(palette_indexes, 0, sizeof(palette_indexes));
    memset(gb_oam, 0, sizeof(gb_oam));
    bgp = 0xfc;
    obp0 = obp1 = 0xff;
    obj_x_priority = 0;
    gb_video_dirty = palette_dirty = 1;
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
    gb_video_store_dirty(&palettes[group][index & 0x3f], value, &palette_dirty);
    if (index & 0x80) {
        palette_indexes[group] = 0x80 | ((index + 1) & 0x3f);
    }
}

void gb_video_write(uint8_t offset, uint8_t value)
{
    switch (offset) {
    case 0x47: gb_video_store_dirty(&bgp, value, &palette_dirty); return;
    case 0x48: gb_video_store_dirty(&obp0, value, &palette_dirty); return;
    case 0x49: gb_video_store_dirty(&obp1, value, &palette_dirty); return;
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

enum {
    ROW_CACHE_SIZE = 32, TILE_WIDTH = 8,
    OBJECT_BAND_HEIGHT = 8, OBJECT_BANDS = GB_SCREEN_HEIGHT / OBJECT_BAND_HEIGHT,
    OAM_OBJECTS = GB_OAM_SIZE / 4,
};

typedef struct {
    uint8_t pixels[TILE_WIDTH];
    uint8_t attributes, low, high;
    uint8_t padding[5]; /* Keep the cache stride a power of two on eZ80. */
} TileRow;

static TileRow row_cache[ROW_CACHE_SIZE];
static uint8_t band_objects[OBJECT_BANDS][OAM_OBJECTS];
static uint8_t band_counts[OBJECT_BANDS];

static void prepare_objects(void)
{
    memset(band_counts, 0, sizeof(band_counts));
    int height = gb_lcdc & TALL_OBJ ? 16 : 8;
    /* Preserve OAM order so each scanline still selects its first ten objects. */
    for (unsigned offset = 0; offset < GB_OAM_SIZE; offset += 4) {
        int first = gb_oam[offset] - 16;
        int last = first + height - 1;
        if (last < 0 || first >= GB_SCREEN_HEIGHT) {
            continue;
        }
        unsigned first_band = (first < 0 ? 0 : first) / OBJECT_BAND_HEIGHT;
        unsigned last_band = (last >= GB_SCREEN_HEIGHT ? GB_SCREEN_HEIGHT - 1 : last) / OBJECT_BAND_HEIGHT;
        for (unsigned band = first_band; band <= last_band; ++band) {
            band_objects[band][band_counts[band]++] = offset;
        }
    }
}

static void build_colors(uint16_t colors[COLOR_COUNT])
{
    for (unsigned palette = 0; palette < 8; ++palette) {
        for (unsigned color = 0; color < 4; ++color) {
            colors[palette * 4 + color] = palette_color(BG_PALETTE, palette, color);
            colors[OBJ_INDEX + palette * 4 + color] =
                palette_color(OBJ_PALETTE, gb_cgb ? palette : palette << 4, color);
        }
    }
    memset(colors + BLACK_INDEX, 0, (COLOR_COUNT - BLACK_INDEX) * sizeof(*colors));
    colors[WHITE_INDEX] = 0xffff;
}

static const TileRow *background_row(unsigned bank, unsigned offset, uint8_t attributes)
{
    const uint8_t *planes = &gb_vram[bank][offset];
    uint8_t low = planes[0], high = planes[1];
    uint8_t key = (attributes & (PRIORITY | X_FLIP | 7)) | 0x10;
    unsigned slot = (low ^ (low >> 5) ^ high ^ (high >> 3) ^ key ^ (key >> 4)) & (ROW_CACHE_SIZE - 1);
    TileRow *row = &row_cache[slot];
    if (row->attributes != key || row->low != low || row->high != high) {
        row->attributes = key;
        row->low = low;
        row->high = high;
        uint8_t base = ((attributes & 7) << 2) | (attributes & PRIORITY);
        bool flip = attributes & X_FLIP;
        const uint8_t *decoded = decoded_tile(bank, offset / 16) + (offset & 14) * 4;
        for (unsigned i = 0; i < TILE_WIDTH; ++i) {
            row->pixels[i] = base | decoded[flip ? 7 - i : i];
        }
    }
    return row;
}

static void render_bg_span(uint8_t tile_x, uint8_t tile_y, unsigned map, unsigned remaining,
                           uint8_t *pixels)
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
        unsigned column = tile_x & 7;
        unsigned count = TILE_WIDTH - column;
        if (count > remaining) {
            count = remaining;
        }
        if (!incremental_background || tile_changed[bank * 384 + offset / 16] ||
            map_changed[map_row + cell - 0x1800]) {
            const TileRow *row = background_row(bank, offset, attributes);
            memcpy(pixels, row->pixels + column, count);
        }
        pixels += count;
        tile_x += count;
        remaining -= count;
    }
}

static void render_background(uint8_t y, uint8_t *pixels)
{
    if (!gb_cgb && !(gb_lcdc & BG_ENABLE_PRIORITY)) {
        memset(pixels, WHITE_INDEX, GB_SCREEN_WIDTH);
        return;
    }

    int window_x = (int)gb_wx - 7;
    bool window = (gb_lcdc & WINDOW_ENABLE) && y >= gb_wy && window_x < GB_SCREEN_WIDTH;
    unsigned bg_width = window ? (window_x < 0 ? 0 : window_x) : GB_SCREEN_WIDTH;
    render_bg_span(gb_scx, y + gb_scy, (gb_lcdc & BG_MAP) ? 0x1c00 : 0x1800,
                   bg_width, pixels);
    if (window) {
        render_bg_span(bg_width - window_x, y - gb_wy, (gb_lcdc & WINDOW_MAP) ? 0x1c00 : 0x1800,
                       GB_SCREEN_WIDTH - bg_width, pixels + bg_width);
    }
}

static unsigned select_objects(uint8_t y, int height, uint8_t objects[MAX_SCANLINE_OBJECTS])
{
    unsigned count = 0;
    bool sort_by_x = !gb_cgb || obj_x_priority;
    unsigned band = y / OBJECT_BAND_HEIGHT;
    const uint8_t *candidates = band_objects[band];
    unsigned candidate_count = band_counts[band];
    for (unsigned i = 0; i < candidate_count && count < MAX_SCANLINE_OBJECTS; ++i) {
        unsigned offset = candidates[i];
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
    return count;
}

static void render_objects(uint8_t y, uint8_t *pixels)
{
    uint8_t objects[MAX_SCANLINE_OBJECTS];
    int height = (gb_lcdc & TALL_OBJ) ? 16 : 8;
    unsigned count = select_objects(y, height, objects);
    if (!count) {
        return;
    }
    uint8_t claimed[GB_SCREEN_WIDTH] = {0};
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
        const uint8_t *data = decoded_tile(bank, tile + row / 8) + (row & 7) * 8;

        uint8_t base = OBJ_INDEX + ((gb_cgb ? attributes & 7 : (attributes >> 4) & 1) << 2);
        bool priority = !gb_cgb || (gb_lcdc & BG_ENABLE_PRIORITY);
        bool flip = attributes & X_FLIP;
        unsigned column = flip ? 7 : 0;
        for (int x = start; x < start + TILE_WIDTH; ++x) {
            uint8_t color = data[column];
            column += flip ? -1 : 1;
            if (!color || x < 0 || x >= GB_SCREEN_WIDTH || claimed[x]) {
                continue;
            }
            uint8_t bg = pixels[x];
            claimed[x] = 1;
            if (!(priority && (bg & 3) && ((bg | attributes) & PRIORITY))) {
                pixels[x] = base | color;
            }
        }
    }
}

static void render_scanline(uint8_t y, uint8_t pixels[GB_SCREEN_WIDTH])
{
    if (!(gb_lcdc & LCD_ENABLE) || y >= GB_SCREEN_HEIGHT) {
        memset(pixels, WHITE_INDEX, GB_SCREEN_WIDTH);
        return;
    }

    render_background(y, pixels);
    if (gb_lcdc & OBJ_ENABLE) {
        render_objects(y, pixels);
    }
}

void gb_render_scanline(uint8_t y, uint16_t pixels[GB_SCREEN_WIDTH])
{
    uint16_t colors[COLOR_COUNT];
    uint8_t indices[GB_SCREEN_WIDTH];
    build_colors(colors);
    if (gb_lcdc & OBJ_ENABLE) {
        prepare_objects();
    }
    render_scanline(y, indices);
    for (unsigned x = 0; x < GB_SCREEN_WIDTH; ++x) {
        pixels[x] = colors[indices[x] & ~PRIORITY];
    }
}

#ifdef __TICE__

enum {
    LCD_PIXEL_FORMAT = 7 << 1,
    LCD_PIXEL_ORDER = 7 << 8,
    LCD_INDEXED8 = (3 << 1) | (1 << 8),
};

static bool frame_active;
static uint24_t saved_control, saved_base;
static uint8_t previous_object_start, previous_object_end;
static uint16_t saved_colors[COLOR_COUNT], saved_priority_colors[OBJ_INDEX];
static uint8_t background_state[6];

/* Compose in reverse priority order using the retained background as the
 * underlay. A winning sprite hidden by the BG erases any lower sprite there. */
static void compose_objects(uint8_t y, uint8_t *pixels, const uint8_t *background)
{
    uint8_t objects[MAX_SCANLINE_OBJECTS];
    int height = gb_lcdc & TALL_OBJ ? 16 : 8;
    unsigned count = select_objects(y, height, objects);
    bool priority = !gb_cgb || (gb_lcdc & BG_ENABLE_PRIORITY);
    while (count) {
        const uint8_t *object = &gb_oam[objects[--count]];
        int start = (int)object[1] - TILE_WIDTH;
        if (start <= -TILE_WIDTH || start >= GB_SCREEN_WIDTH) continue;
        unsigned first = start < 0 ? -start : 0;
        unsigned end = start + TILE_WIDTH > GB_SCREEN_WIDTH ? GB_SCREEN_WIDTH - start : TILE_WIDTH;
        uint8_t attributes = object[3];
        unsigned row = y + 16 - object[0];
        if (attributes & Y_FLIP) row = height - 1 - row;
        unsigned tile = height == 16 ? object[2] & 0xfe : object[2];
        unsigned bank = gb_cgb ? (attributes >> 3) & 1 : 0;
        const uint8_t *source = decoded_tile(bank, tile + row / 8) + (row & 7) * TILE_WIDTH;
        uint8_t base = OBJ_INDEX + ((gb_cgb ? attributes & 7 : (attributes >> 4) & 1) << 2);
        bool flip = attributes & X_FLIP;
        unsigned column = flip ? 7 - first : first;
        int step = flip ? -1 : 1;
        uint8_t *destination = pixels + (start + first);
        const uint8_t *underlay = background + (start + first);
        for (unsigned x = first; x < end; ++x) {
            uint8_t color = source[column];
            column += step;
            if (color) {
                uint8_t bg = *underlay;
                *destination = priority && (bg & 3) && ((bg | attributes) & PRIORITY)
                    ? bg : base | color;
            }
            ++destination;
            ++underlay;
        }
    }
}

enum { BACKGROUND_TILE_SLOTS = 16 };
static uint16_t background_tile_keys[BACKGROUND_TILE_SLOTS];
#define background_tile_cache (graphics_cache + CACHE_BYTES)
_Static_assert(CACHE_BYTES + BACKGROUND_TILE_SLOTS * TILE_PIXELS <= LCD_SIZE / 2,
               "background tile cache exceeds spare LCD RAM");

/* A rebuild reads stable VRAM. Cache palette and flip variants for that
 * rebuild, so rectangles sharing a tile can copy its rows directly. */
static const uint8_t *background_tile(unsigned bank, unsigned tile, uint8_t attributes)
{
    uint8_t variant = attributes & (PRIORITY | X_FLIP | 7);
    if (!variant) {
        return decoded_tile(bank, tile);
    }
    unsigned slot = (tile ^ (tile >> 4) ^ variant ^ (variant >> 4)) & (BACKGROUND_TILE_SLOTS - 1);
    uint16_t key = (tile + bank * 384) | ((uint16_t)(variant & 7) << 10)
                 | ((uint16_t)(variant & X_FLIP) << 8) | ((uint16_t)(variant & PRIORITY) << 7);
    uint8_t *pixels = background_tile_cache + slot * TILE_PIXELS;
    if (background_tile_keys[slot] != key) {
        const uint8_t *source = decoded_tile(bank, tile);
        uint8_t base = ((variant & 7) << 2) | (variant & PRIORITY);
        for (unsigned row = 0; row < TILE_PIXELS; row += TILE_WIDTH) {
            if (variant & X_FLIP) {
                for (unsigned column = 0; column < TILE_WIDTH; ++column) {
                    pixels[row + column] = base | source[row + TILE_WIDTH - 1 - column];
                }
            } else {
                for (unsigned column = 0; column < TILE_WIDTH; ++column) {
                    pixels[row + column] = base | source[row + column];
                }
            }
        }
        background_tile_keys[slot] = key;
    }
    return pixels;
}

/* Resolve map entries once per visible tile rectangle, rather than once per
 * scanline. Partial tiles at scroll/window edges still use the same pixels. */
static void background_region(unsigned x, unsigned y, unsigned width, unsigned height,
                              uint8_t source_x, uint8_t source_y, unsigned map)
{
    while (height) {
        unsigned rows = 8 - (source_y & 7);
        if (rows > height) rows = height;
        unsigned remaining = width, destination_x = x;
        uint8_t sx = source_x;
        unsigned map_row = map + (source_y >> 3) * 32;
        bool changed = false;
        while (remaining) {
            unsigned cell = map_row + (sx >> 3);
            uint8_t number = gb_vram[0][cell];
            uint8_t attributes = gb_cgb ? gb_vram[1][cell] : 0;
            unsigned bank = (attributes >> 3) & 1;
            unsigned tile = gb_lcdc & UNSIGNED_TILES ? number : 256 + (int8_t)number;
            unsigned columns = 8 - (sx & 7);
            if (columns > remaining) columns = remaining;
            if (!incremental_background || tile_changed[bank * 384 + tile] || map_changed[cell - 0x1800]) {
                changed = true;
                uint8_t *destination = background_pixels + y * GB_SCREEN_WIDTH + destination_x;
                const uint8_t *pixels = background_tile(bank, tile, attributes);
                for (unsigned row = 0; row < rows; ++row) {
                    unsigned tile_y = (source_y & 7) + row;
                    if (attributes & Y_FLIP) tile_y = 7 - tile_y;
                    memcpy(destination, pixels + tile_y * TILE_WIDTH + (sx & 7), columns);
                    destination += GB_SCREEN_WIDTH;
                }
            }
            destination_x += columns; sx += columns; remaining -= columns;
        }
        if (changed && incremental_background) {
            if (y < background_start) background_start = y;
            if (y + rows > background_end) background_end = y + rows;
        }
        y += rows; source_y += rows; height -= rows;
    }
}

static void rebuild_background(void)
{
    if (!(gb_lcdc & LCD_ENABLE) || (!gb_cgb && !(gb_lcdc & BG_ENABLE_PRIORITY))) {
        memset(background_pixels, WHITE_INDEX, BACKGROUND_BYTES);
        return;
    }
    memset(background_tile_keys, 0xff, sizeof background_tile_keys);
    int window_x = (int)gb_wx - 7;
    bool window = (gb_lcdc & WINDOW_ENABLE) && gb_wy < GB_SCREEN_HEIGHT && window_x < GB_SCREEN_WIDTH;
    unsigned top = window ? gb_wy : GB_SCREEN_HEIGHT;
    unsigned map = gb_lcdc & BG_MAP ? 0x1c00 : 0x1800;
    background_region(0, 0, GB_SCREEN_WIDTH, top, gb_scx, gb_scy, map);
    if (window) {
        unsigned left = window_x < 0 ? 0 : window_x;
        background_region(0, top, left, GB_SCREEN_HEIGHT - top, gb_scx, gb_scy + top, map);
        background_region(left, top, GB_SCREEN_WIDTH - left, GB_SCREEN_HEIGHT - top,
                          left - window_x, 0, gb_lcdc & WINDOW_MAP ? 0x1c00 : 0x1800);
    }
}

/* On small scroll changes, shift the retained pixels and decode only the
 * newly visible strips. Any VRAM edits still refresh their visible tiles. */
static bool scroll_background(const uint8_t state[6])
{
    if (!background_valid || state[0] != background_state[0] ||
        state[1] != background_state[1] || state[4] != background_state[4] ||
        state[5] != background_state[5] ||
        !(gb_lcdc & LCD_ENABLE) || (!gb_cgb && !(gb_lcdc & BG_ENABLE_PRIORITY)) ||
        ((gb_lcdc & WINDOW_ENABLE) && gb_wy < GB_SCREEN_HEIGHT && gb_wx < GB_SCREEN_WIDTH + 7)) {
        return false;
    }
    int dx = (int8_t)(gb_scx - background_state[2]);
    int dy = (int8_t)(gb_scy - background_state[3]);
    if ((!dx && !dy) || dx <= -TILE_WIDTH || dx >= TILE_WIDTH ||
        dy <= -TILE_WIDTH || dy >= TILE_WIDTH) return false;

    unsigned x = dx < 0 ? -dx : 0, y = dy < 0 ? -dy : 0;
    unsigned source_x = dx > 0 ? dx : 0, source_y = dy > 0 ? dy : 0;
    unsigned width = GB_SCREEN_WIDTH - x - source_x;
    unsigned height = GB_SCREEN_HEIGHT - y - source_y;
    for (unsigned i = 0; i < height; ++i) {
        unsigned row = dy < 0 ? height - 1 - i : i;
        memmove(background_pixels + (y + row) * GB_SCREEN_WIDTH + x,
                background_pixels + (source_y + row) * GB_SCREEN_WIDTH + source_x, width);
    }
    unsigned map = gb_lcdc & BG_MAP ? 0x1c00 : 0x1800;
    memset(background_tile_keys, 0xff, sizeof background_tile_keys);
    if (background_vram_dirty) {
        incremental_background = true;
        background_region(x, y, width, height, gb_scx + x, gb_scy + y, map);
        incremental_background = false;
    }
    if (dx) {
        unsigned strip_x = dx < 0 ? 0 : width;
        background_region(strip_x, y, GB_SCREEN_WIDTH - width, height,
                          gb_scx + strip_x, gb_scy + y, map);
    }
    if (dy) {
        unsigned strip_y = dy < 0 ? 0 : height;
        background_region(0, strip_y, GB_SCREEN_WIDTH, GB_SCREEN_HEIGHT - height,
                          gb_scx, gb_scy + strip_y, map);
    }
    return true;
}

static void refresh_palette(void)
{
    uint16_t colors[COLOR_COUNT];
    volatile uint16_t *palette = lcd_Palette;
    build_colors(colors);
    for (unsigned i = 0; i < COLOR_COUNT; ++i) {
        uint16_t rgb565 = colors[i];
        uint16_t rgb1555 = (rgb565 & 31) | ((rgb565 >> 1) & 0x7fe0) | ((rgb565 & 0x20) << 10);
        palette[i] = rgb1555;
        if (i < OBJ_INDEX) {
            palette[i | PRIORITY] = rgb1555;
        }
    }
    palette_dirty = 0;
}


void gb_present_frame(void)
{
    if (frame_active && palette_dirty) {
        refresh_palette();
    }
    if (frame_active && !gb_video_dirty && !gb_oam_dirty) {
        return;
    }
    if (!frame_active) {
        /* TI-OS or another screen owner may have reused the entire LCD RAM. */
        memset(tile_valid, 0, TILE_COUNT);
        background_valid = false;
        saved_control = lcd_Control;
        saved_base = lcd_UpBase;
        volatile uint16_t *palette = lcd_Palette;
        for (unsigned i = 0; i < COLOR_COUNT; ++i) {
            saved_colors[i] = palette[i];
        }
        for (unsigned i = 0; i < OBJ_INDEX; ++i) {
            saved_priority_colors[i] = palette[i | PRIORITY];
        }
        refresh_palette();
        memset(lcd_Ram, BLACK_INDEX, LCD_WIDTH * LCD_HEIGHT);
        lcd_UpBase = (uint24_t)lcd_Ram;
        lcd_Control = (saved_control & ~(LCD_PIXEL_FORMAT | LCD_PIXEL_ORDER)) | LCD_INDEXED8;
        frame_active = true;
    }

    background_start = GB_SCREEN_HEIGHT;
    background_end = 0;
    if (gb_video_dirty || !background_valid) {
        uint8_t state[] = {gb_cgb, gb_lcdc, gb_scx, gb_scy, gb_wx, gb_wy};
        bool scrolled = scroll_background(state);
        if (!scrolled) {
            incremental_background = background_valid && !memcmp(state, background_state, sizeof state);
            rebuild_background();
        }
        if (!incremental_background) {
            background_start = 0;
            background_end = GB_SCREEN_HEIGHT;
        }
        incremental_background = false;
        memcpy(background_state, state, sizeof state);
        background_valid = true;
        background_vram_dirty = false;
        memset(tile_changed, 0, TILE_COUNT);
        memset(map_changed, 0, 2048);
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
    if (background_start < first) first = background_start;
    if (background_end > end) end = background_end;
    if (first < end) {
        uint8_t *line = (uint8_t *)lcd_Ram + ((LCD_HEIGHT - GB_SCREEN_HEIGHT) / 2 + first) * LCD_WIDTH
                                          + (LCD_WIDTH - GB_SCREEN_WIDTH) / 2;
        if (gb_lcdc & OBJ_ENABLE) {
            prepare_objects();
        }
        for (uint8_t y = first; y < end; ++y) {
            const uint8_t *background = background_pixels + (unsigned)y * GB_SCREEN_WIDTH;
            memcpy(line, background, GB_SCREEN_WIDTH);
            if ((gb_lcdc & (LCD_ENABLE | OBJ_ENABLE)) == (LCD_ENABLE | OBJ_ENABLE)) {
                compose_objects(y, line, background);
            }
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
        volatile uint16_t *palette = lcd_Palette;
        for (unsigned i = 0; i < COLOR_COUNT; ++i) {
            palette[i] = saved_colors[i];
        }
        for (unsigned i = 0; i < OBJ_INDEX; ++i) {
            palette[i | PRIORITY] = saved_priority_colors[i];
        }
        frame_active = false;
        background_valid = false;
        memset(tile_valid, 0, TILE_COUNT);
    }
}
#endif
