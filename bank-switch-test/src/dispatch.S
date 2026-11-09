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

_gb_dispatch:
    push hl
    call _gb_lookup
    pop bc

    ld de, 0
    or a, a
    sbc hl, de
    jp z, _gb_unmapped
    jp (hl)
