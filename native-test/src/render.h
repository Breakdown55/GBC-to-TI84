#ifndef GB_RENDER_H
#define GB_RENDER_H

#include <stdint.h>

enum {
    GB_SCREEN_WIDTH = 160,
    GB_SCREEN_HEIGHT = 144,
    GB_OAM_SIZE = 160,
};

extern uint8_t gb_oam[GB_OAM_SIZE];
extern uint8_t gb_video_dirty;
extern uint8_t gb_oam_dirty;

static inline void gb_video_store_dirty(uint8_t *byte, uint8_t value, uint8_t *dirty)
{
    if (*byte != value) {
        *byte = value;
        *dirty = 1;
    }
}

static inline void gb_video_store(uint8_t *byte, uint8_t value)
{
    gb_video_store_dirty(byte, value, &gb_video_dirty);
}

static inline void gb_oam_store(unsigned offset, uint8_t value)
{
    gb_video_store_dirty(&gb_oam[offset], value, &gb_oam_dirty);
}

void gb_video_init(void);
/* Call after changing a physical VRAM range, including DMA destinations. */
void gb_video_invalidate(unsigned bank, unsigned offset, unsigned length);
uint8_t gb_video_read(uint8_t offset);
void gb_video_write(uint8_t offset, uint8_t value);

/* Render a scanline from the current LCD state into RGB565 pixels. */
void gb_render_scanline(uint8_t y, uint16_t pixels[GB_SCREEN_WIDTH]);

void gb_present_frame(void);
void gb_end_frame(void);

#endif
