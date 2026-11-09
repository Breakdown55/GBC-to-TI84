    .assume ADL=1
    .section .text
    .global _bank_3_4800
    .global _bank_4_4800
    .global _bank_0_0321
    .global _bank_0_0803

_bank_3_4800:
    ld a, 0x33
    ld (_jump_marker), a
    ret

_bank_4_4800:
    ld a, 0x44
    ld (_jump_marker), a
    ld a, (_return_enabled)
    or a, a
    ret z
    jp _return_from_selected_bank

_bank_0_0321:
    ld a, 0x21
    ld (_return_marker), a
    ret

_bank_0_0803:
    ld a, 0x03
    ld (_return_marker), a
    ret
