#ifndef GB_ROM_STORAGE_H
#define GB_ROM_STORAGE_H
#include <stdint.h>
#if GB_ROM_CHUNKS
extern char gb_archive_name[9];
uint8_t gb_archive_init(void);
const uint8_t *gb_archive_pointer(uint16_t address, unsigned length);
const uint8_t *gb_archive_window(uint16_t address, unsigned *length);
uint16_t gb_archive_read(uint16_t address);
#endif
#endif
