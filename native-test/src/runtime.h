#ifndef GB_RUNTIME_H
#define GB_RUNTIME_H

#include <stdint.h>

enum {
    GB_VRAM_BASE = 0x8000, GB_VRAM_SIZE = 0x2000,
    GB_WRAM_BASE = 0xc000, GB_WRAM_SIZE = 0x1000,
    GB_HRAM_BASE = 0xff80, GB_HRAM_SIZE = 0x7f,
};

typedef void (*GbNativeBlock)(void);

typedef struct {
    uint16_t address;
    GbNativeBlock entry;
} GbTarget;

typedef struct {
    uint16_t bank;
    unsigned first;
    unsigned count;
} GbTargetBank;

typedef struct {
    uint16_t bank;
    uint16_t address;
    uint16_t length;
    uint24_t offset;
} GbRomSpan;

extern const GbTarget gb_targets[]; /* Sorted by guest address within each bank. */
extern const GbTargetBank gb_target_banks[];
extern const unsigned gb_target_bank_count;
extern const GbRomSpan *gb_rom_data;
extern const uint8_t *gb_rom_payload;
extern unsigned gb_rom_data_count;

extern volatile uint16_t gb_rom_bank;
extern uint16_t gb_rom_bank_mask;
extern uint8_t gb_mbc5;
extern uint8_t gb_ram_enabled;
extern uint24_t gb_sram_size;
extern uint8_t gb_cgb;
extern volatile uint24_t gb_pc;
extern volatile uint24_t gb_hl; /* Zero-extended Game Boy HL for ADL code. */
extern volatile uint24_t gb_bc;
extern volatile uint24_t gb_de;
extern volatile uint16_t gb_sp;
extern volatile uint8_t gb_a;
extern volatile uint8_t gb_f;
extern volatile uint8_t gb_ime;
extern uint8_t gb_lcdc;
extern uint8_t gb_ly;
extern uint8_t gb_stat;
extern uint8_t gb_scx;
extern uint8_t gb_scy;
extern uint8_t gb_wx;
extern uint8_t gb_wy;
extern uint8_t gb_lyc;
extern uint8_t gb_tma;
extern uint8_t gb_tac;
extern uint8_t gb_ie;
extern uint8_t gb_if;
extern uint8_t gb_key1;
extern uint8_t gb_vbk;
extern uint8_t gb_svbk;
extern uint8_t gb_sc;
extern uint8_t gb_nr50;
extern uint8_t gb_nr51;
extern uint8_t gb_nr52;
extern uint8_t gb_vram[2][GB_VRAM_SIZE];
extern uint8_t gb_wram[GB_WRAM_SIZE];
extern uint8_t gb_wram_banks[7][GB_WRAM_SIZE];
extern uint8_t gb_hram[GB_HRAM_SIZE];

GbNativeBlock gb_lookup(uint24_t address);
void gb_cp_a(uint8_t value);
void gb_inc_a(void);
void gb_dec_b(void);
void gb_inc_c(void);
void gb_dec_c(void);
void gb_sub_l(void);
void gb_srl_a(void);
void gb_add_hl_de(void);
void gb_add_hl(uint24_t value);
void gb_ld_hl_sp(uint8_t offset);
void gb_add_sp(uint8_t offset);
uint8_t gb_step_byte(uint8_t value, uint8_t decrement);
uint8_t gb_step_hl(uint8_t decrement);
void gb_alu(uint8_t operation, uint8_t operand);
uint8_t gb_alu_hl(uint8_t operation);
uint8_t gb_read_high(uint8_t offset);
uint8_t gb_write_high(uint8_t offset);
uint8_t gb_watch_ram(uint16_t address);
uint8_t gb_stop(void);
uint8_t gb_load_hli(void);
uint8_t gb_load_hld(void);
uint8_t gb_load_a(uint24_t address);
uint8_t gb_store_hli(void);
uint8_t gb_store_hld(void);
uint8_t gb_store_hl(void);
uint8_t gb_set_hl_bit(uint8_t bit);
uint8_t gb_store_a(uint24_t address);
uint24_t gb_read_byte(uint24_t address);
uint8_t gb_write_byte(uint24_t address, uint8_t value);
uint8_t gb_push_word(uint24_t value);
uint24_t gb_pop_word(void);
void gb_unmapped(void);

#endif
