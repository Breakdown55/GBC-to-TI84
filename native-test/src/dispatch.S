    .assume ADL=1
    .section .text.gb_fast_ram
    .macro fast_hl name, fallback, store, step
    .global _gb_fast_\name
_gb_fast_\name:
    ld hl, (_gb_hl)
    call .Lplain_ram
    jp c, _gb_\fallback
    .if \store
    ld a, (_gb_a)
    ld (hl), a
    .else
    ld a, (hl)
    ld (_gb_a), a
    .endif
    .if \step
    ld hl, (_gb_hl)
    .if \step > 0
    inc hl
    .else
    dec hl
    .endif
    ld (_gb_hl), hl
    xor a, a
    ld (_gb_hl+2), a
    .endif
    ld a, 1
    ret
    .endm

    fast_hl load_hli, load_hli, 0, 1
    fast_hl load_hld, load_hld, 0, -1
    fast_hl store_hl, store_hl, 1, 0
    fast_hl store_hli, store_hli, 1, 1
    fast_hl store_hld, store_hld, 1, -1

/* The translated code passes the guest address in HL and the store value in A. */
    .global _gb_read_indirect
_gb_read_indirect:
    call .Lplain_ram
    jr c, .Lread_bus
    ld a, (hl)
    ret
.Lread_bus:
    push hl
    call _gb_read_byte
    pop bc
    ld a, h
    neg                 ; Carry indicates the 0x100 unmapped sentinel.
    ld a, l
    ret

    .global _gb_write_indirect
_gb_write_indirect:
    ld c, a
    call .Lplain_ram
    jr c, .Lwrite_bus
    ld (hl), c
    ld a, 1
    or a, a
    ret
.Lwrite_bus:
    ld de, 0
    ld e, c
    push de
    push hl
    call _gb_write_byte
    pop bc
    pop bc
    or a, a
    ret

/* Only plain WRAM, its echo, and HRAM bypass the general memory bus.
 * Rejection sets carry and leaves HL and BC intact for the checked path. */
.Lplain_ram:
    ld a, h
    cp a, 0xc0
    ret c
    cp a, 0xfe
    jr nc, .Lhigh_ram
    and a, 0x1f
    cp a, 0x10
    jr nc, .Lbanked_ram
    ld h, a
    ld de, _gb_wram
    add hl, de
    or a, a
    ret
.Lbanked_ram:
    and a, 0x0f
    ld h, a
    ld de, (_gb_wram_pages+3)
    add hl, de
    or a, a
    ret
.Lhigh_ram:
    cp a, 0xff
    jr nz, .Lnot_plain_ram
    ld a, l
    sub a, 0x80
    ret c
    cp a, 0x7f
    jr z, .Lnot_plain_ram
    ld hl, _gb_hram
    ld de, 0
    ld e, a
    add hl, de
    or a, a
    ret
.Lnot_plain_ram:
    scf
    ret

    .section .text.gb_native_flags
    .global _gb_native_flags
    .global _gb_native_step_flags
_gb_native_flags:
    push af
    pop de
    ld a, e
    ld hl, .Lflag_map
    ld l, a
    ld a, (hl)
    ld (_gb_f), a
    ld a, d
    ret

_gb_native_step_flags:
    push af
    pop de
    ld a, e
    ld hl, .Lflag_map
    ld l, a
    ld a, (hl)
    and a, 0xe0
    ld c, a
    ld a, (_gb_f)
    and a, 0x10
    or a, c
    ld (_gb_f), a
    ld a, d
    ret

    .global _gb_native_add16_flags
_gb_native_add16_flags:
    push af
    pop bc
    ld de, .Lflag_map
    ld e, c
    ld a, (de)
    and a, 0x30
    ld c, a
    ld a, (_gb_f)
    and a, 0x80
    or a, c
    ld (_gb_f), a
    ld a, b
    ret

    .section .rodata.gb_native_flags
    .balign 256
.Lflag_map:
    .set .Lflags, 0
    .rept 256
    .byte ((.Lflags & 0x40) << 1) | ((.Lflags & 0x10) << 1) | ((.Lflags & 2) << 5) | ((.Lflags & 1) << 4)
    .set .Lflags, .Lflags + 1
    .endr

    .section .text
    .global _gb_dispatch
    .global _gb_ret
    .global _gb_call
    .global _gb_call_native

_gb_call_native:
    push hl
    call .Lpush_guest_de
    pop hl
    or a, a
    jp z, _gb_unmapped
    jp (hl)

_gb_call:
    push hl
    call .Lpush_guest_de
    pop hl
    or a, a
    jp z, _gb_unmapped
    jr _gb_dispatch

_gb_ret:
    call _gb_fast_pop
    jp c, _gb_unmapped

_gb_dispatch:
    ld (_gb_pc), hl
    ld d, h
    ld e, l
    ld bc, 0
    ld a, h
    and a, 0xc0
    cp a, 0x40
    jr nz, .Lcache_bank_ready
    ld bc, (_gb_rom_bank)
.Lcache_bank_ready:
    ld a, e
    xor a, d
    xor a, c
    and a, 31
    add a, a
    add a, a
    add a, a
    ld hl, _gb_lookup_cache
    ld l, a
    ld a, (hl)
    cp a, e
    jr nz, .Llookup_miss
    inc hl
    ld a, (hl)
    cp a, d
    jr nz, .Llookup_miss
    inc hl
    ld a, (hl)
    cp a, c
    jr nz, .Llookup_miss
    inc hl
    ld a, (hl)
    cp a, b
    jr nz, .Llookup_miss
    inc hl
    ld hl, (hl)
    ld de, 0
    or a, a
    sbc hl, de
    jr z, .Llookup_miss
    jp (hl)
.Llookup_miss:
    ld hl, (_gb_pc)
    push hl
    call _gb_lookup
    pop bc

    ld de, 0
    or a, a
    sbc hl, de
    jp z, _gb_missing_target
    jp (hl)

    .section .bss.gb_return_cache,"aw",@nobits
    .balign 256
.Lreturn_cache:
    .space 128

    .section .text.gb_return_prediction
    .global _gb_call_predict
    .global _gb_call_predict_native
    .global _gb_ret_predict

/* The native CALL supplies the address of the immediately following code.
 * Entries are indexed by guest SP after return and also store PC and bank. */
_gb_call_predict:
    pop bc
    call .Lremember_call
    jp z, _gb_unmapped
    jp _gb_dispatch
_gb_call_predict_native:
    pop bc
    call .Lremember_call
    jp z, _gb_unmapped
    jp (hl)
.Lremember_call:
    push hl
    push bc
    push de
    call .Lpush_guest_de
    pop de
    pop bc
    or a, a
    jr z, .Lremember_done
    ld a, (_gb_sp)
    add a, 2
    and a, 0x1e
    rlca
    rlca
    ld hl, .Lreturn_cache
    ld l, a
    ld (hl), bc
    inc hl
    inc hl
    inc hl
    ld (hl), e
    inc hl
    ld (hl), d
    inc hl
    ld a, d
    and a, 0xc0
    cp a, 0x40
    ld de, 0
    jr nz, .Lremember_bank
    ld de, (_gb_rom_bank)
.Lremember_bank:
    ld (hl), e
    inc hl
    ld (hl), d
    ld a, 1
    or a, a
.Lremember_done:
    pop hl
    ret

_gb_ret_predict:
    call _gb_fast_pop
    jp c, _gb_unmapped
    ld (_gb_pc), hl
    ld d, h
    ld e, l
    ld a, (_gb_sp)
    and a, 0x1e
    rlca
    rlca
    ld hl, .Lreturn_cache
    ld l, a
    ld bc, (hl)
    inc hl
    inc hl
    inc hl
    ld a, (hl)
    cp a, e
    jr nz, .Lreturn_miss
    inc hl
    ld a, (hl)
    cp a, d
    jr nz, .Lreturn_miss
    ld a, d
    and a, 0xc0
    cp a, 0x40
    jr nz, .Lreturn_valid_bank
    inc hl
    ld a, (_gb_rom_bank)
    cp a, (hl)
    jr nz, .Lreturn_miss
    inc hl
    ld a, (_gb_rom_bank+1)
    cp a, (hl)
    jr nz, .Lreturn_miss
.Lreturn_valid_bank:
    push bc
    pop hl
    ld bc, 0
    or a, a
    sbc hl, bc
    jr z, .Lreturn_miss
    jp (hl)
.Lreturn_miss:
    ld hl, (_gb_pc)
    jp _gb_dispatch

    .section .text.gb_fast_stack
    .global _gb_fast_push
    .global _gb_fast_pop
/* Only fixed WRAM uses these paths. Other regions, including a stack word
 * split across banks, use the checked C helpers. */
_gb_fast_push:
    ex de, hl
.Lpush_guest_de:
    ld hl, (_gb_sp)
    ld bc, 0xc002
    or a, a
    sbc hl, bc
    ld bc, 0x0fff
    or a, a
    sbc hl, bc
    jr nc, .Lpush_bus
    add hl, bc
    ld bc, _gb_wram
    add hl, bc
    ld (hl), e
    inc hl
    ld (hl), d
    ld hl, (_gb_sp)
    dec hl
    dec hl
    ld (_gb_sp), hl
    ld a, 1
    ret
.Lpush_bus:
    push de
    call _gb_push_word
    pop bc
    ret

_gb_fast_pop:
    ld hl, (_gb_sp)
    ld de, 0xc000
    or a, a
    sbc hl, de
    ld de, 0x0fff
    or a, a
    sbc hl, de
    jr nc, .Lpop_bus
    add hl, de
    ld de, _gb_wram
    add hl, de
    ld de, 0
    ld e, (hl)
    inc hl
    ld d, (hl)
    ld hl, (_gb_sp)
    inc hl
    inc hl
    ld (_gb_sp), hl
    ex de, hl
    ret
.Lpop_bus:
    call _gb_pop_word
    ld de, 0x10000
    or a, a
    sbc hl, de
    ccf
    ret c
    add hl, de
    or a, a
    ret

    .section .text.gb_timing_dispatch
    .global _gb_boundary
    .global _gb_reti
    .global _gb_timed_halt

_gb_boundary:
    push de             ; Staged T-cycles, then guest PC.
    push hl
    call _gb_timing_boundary
    pop de
    pop de
    ld a, l             ; Zero, fault (1), or a guest IRQ vector.
    or a, a
    ret z
    pop de              ; Drop the translated instruction's boundary return.
    dec a
    jp z, _gb_unmapped
    cp a, 2
    jp z, _gb_user_exit
    jp _gb_dispatch

_gb_reti:
    call _gb_timing_enable_ime
    jp _gb_ret

_gb_timed_halt:
    push hl
    call _gb_timing_halt
    pop de              ; Resume PC if HALT woke without servicing an IRQ.
    ld a, l
    or a, a
    jr z, .Lresume
    dec a
    jp z, _gb_unmapped
    dec a
    jp z, _gb_halt
    dec a
    jp z, _gb_user_exit
    jp _gb_dispatch
.Lresume:
    ex de, hl
    jp _gb_dispatch

    .section .text.gb_preview_dispatch
    .global _gb_preview_vblank
    .global _gb_preview_reti

_gb_preview_vblank:
    push hl
    call _gb_preview_begin
    pop de
    or a, a
    jp z, _gb_unmapped
    dec a
    jp nz, _gb_user_exit
    ld hl, 0x0040
    jp _gb_dispatch

_gb_preview_reti:
    call _gb_fast_pop
    jp c, _gb_unmapped
    ld (_gb_pc), hl
    call _gb_timing_enable_ime
    call _gb_preview_finish
    or a, a
    jp nz, _gb_checkpoint
    ld hl, (_gb_pc)
    jp _gb_dispatch
