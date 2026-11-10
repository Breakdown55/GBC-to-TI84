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

static inline void gb_video_store(uint8_t *byte, uint8_t value)
{
    if (*byte != value) {
        *byte = value;
        gb_video_dirty = 1;
    }
}

void gb_video_init(void);
uint8_t gb_video_read(uint8_t offset);
void gb_video_write(uint8_t offset, uint8_t value);

/* Render a scanline from the current LCD state into RGB565 pixels. */
void gb_render_scanline(uint8_t y, uint16_t pixels[GB_SCREEN_WIDTH]);

void gb_present_frame(void);
void gb_end_frame(void);

#endif
