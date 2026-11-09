#ifndef GB_RUNTIME_H
#define GB_RUNTIME_H

#include <stdint.h>

enum {
    GB_WRAM_BASE = 0xc000, GB_WRAM_SIZE = 0x1000,
    GB_HRAM_BASE = 0xff80, GB_HRAM_SIZE = 0x7f,
};

typedef void (*GbNativeBlock)(void);

typedef struct {
    uint16_t bank;
    uint16_t address;
    GbNativeBlock entry;
} GbTarget;

extern const GbTarget gb_targets[];
extern const unsigned gb_target_count;

extern volatile uint16_t gb_rom_bank;
extern volatile uint24_t gb_hl; /* Zero-extended Game Boy HL for ADL code. */
extern volatile uint24_t gb_bc;
extern volatile uint16_t gb_sp;
extern volatile uint8_t gb_a;
extern volatile uint8_t gb_f;
extern uint8_t gb_lcdc;
extern uint8_t gb_ly;
extern uint8_t gb_ie;
extern uint8_t gb_if;
extern uint8_t gb_wram[GB_WRAM_SIZE];
extern uint8_t gb_hram[GB_HRAM_SIZE];

GbNativeBlock gb_lookup(uint24_t address);
void gb_cp_a(uint8_t value);
void gb_inc_a(void);
void gb_dec_c(void);
uint8_t gb_read_high(uint8_t offset);
void gb_write_high(uint8_t offset);
uint8_t gb_store_hli(void);
uint8_t gb_push_word(uint24_t value);
uint24_t gb_pop_word(void);
void gb_unmapped(void);

#endif
