    .assume ADL=1
    .section .text
    .global _gb_dispatch
    .global _gb_ret
    .global _gb_call

_gb_call:
    push hl             ; Save the target across the helper call.
    push de             ; Pass the Game Boy return address to C.
    call _gb_push_word
    pop bc
    pop hl
    or a, a
    jp z, _gb_unmapped
    jr _gb_dispatch

_gb_ret:
    call _gb_pop_word
    ld de, 0x10000
    or a, a
    sbc hl, de
    jp nc, _gb_unmapped
    add hl, de

_gb_dispatch:
    ld (_gb_pc), hl
    push hl
    call _gb_lookup
    pop bc

    ld de, 0
    or a, a
    sbc hl, de
    jp z, _gb_missing_target
    jp (hl)

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
    ld hl, 0x0040
    jp _gb_dispatch

_gb_preview_reti:
    call _gb_pop_word
    ld de, 0x10000
    or a, a
    sbc hl, de
    jp nc, _gb_unmapped
    add hl, de
    ld (_gb_pc), hl
    call _gb_timing_enable_ime
    call _gb_preview_finish
    or a, a
    jp nz, _gb_checkpoint
    ld hl, (_gb_pc)
    jp _gb_dispatch
