#ifndef GB_VRAM_DMA_H
#define GB_VRAM_DMA_H

#include <stdint.h>

/* FF51–FF55. Returns zero when the requested transfer is unsupported. */
uint8_t gb_vram_dma_write(uint8_t offset, uint8_t value);

#endif
