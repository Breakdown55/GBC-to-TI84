#ifndef GB_TIMING_H
#define GB_TIMING_H

#include <stdint.h>

extern uint8_t gb_timing_enabled;
extern uint8_t gb_timing_extra;
extern uint24_t gb_frame_count;
extern void (*gb_host_frame)(void);
extern uint8_t gb_host_quit;

void gb_timing_init(void);
uint8_t gb_timing_flush(void);
uint8_t gb_timing_read(uint8_t offset);
void gb_timing_write(uint8_t offset, uint8_t value);
uint8_t gb_timing_claim_hblank(void);
void gb_timing_stall(uint16_t dots);
void gb_ei(void);
void gb_di(void);

/* Entry points for the native dispatch stubs. */
uint24_t gb_timing_boundary(uint24_t pc, uint24_t cycles);
uint24_t gb_timing_halt(uint24_t resume);
void gb_timing_enable_ime(void);

#endif
