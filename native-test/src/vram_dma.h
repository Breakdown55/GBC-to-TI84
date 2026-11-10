#ifndef GB_VRAM_DMA_H
#define GB_VRAM_DMA_H

#include <stdint.h>

enum {
    GB_VRAM_DMA_IDLE,
    GB_VRAM_DMA_COPIED,
    GB_VRAM_DMA_FAULT,
    GB_VRAM_DMA_BLOCK_DOTS = 32,
};

/* FF51–FF55. Returns zero when the requested transfer is unsupported. */
uint8_t gb_vram_dma_read(uint8_t offset);
uint8_t gb_vram_dma_write(uint8_t offset, uint8_t value);
void gb_vram_dma_reset(void);
uint8_t gb_vram_dma_hblank(void);

#endif
