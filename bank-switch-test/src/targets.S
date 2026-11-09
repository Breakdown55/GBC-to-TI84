    .assume ADL=1
    .section .text
    .global _bank_3_4412
    .global _bank_4_4412
    .global _bank_0_0175
    .global _bank_0_0203

_bank_3_4412:
    ld a, 0x33
    ld (_jump_marker), a
    ret

_bank_4_4412:
    ld a, 0x44
    ld (_jump_marker), a
    ld a, (_return_enabled)
    or a, a
    ret z
    jp _return_from_bank_4

_bank_0_0175:
    ld a, 0x75
    ld (_return_marker), a
    ret

_bank_0_0203:
    ld a, 0x23
    ld (_return_marker), a
    ret
