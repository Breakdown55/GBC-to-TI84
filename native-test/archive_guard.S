    .assume ADL=1
    .text
    .global guard

    .equ flags,          0xd00080
    .equ op1,            0xd005f8
    .equ mov9_to_op1,    0x020320
    .equ find_symbol,    0x02050c
    .equ check_in_ram,   0x021f98
    .equ free_memory,    0x0204fc
    .equ clear_screen,   0x020814
    .equ home_up,        0x020828
    .equ put_string,     0x0207c0
    .equ get_key,        0x020d8c

guard:
    push iy
    ld iy, flags
    ld hl, (op1)
    push hl
    ld hl, (op1 + 3)
    push hl
    ld hl, (op1 + 6)
    push hl
    ld hl, .Lprogram_name
    call mov9_to_op1
    call find_symbol
    jr c, .Larchive_failed
    call check_in_ram
    jr z, .Larchive_failed
    or a, a
    jr .Lrestore_op1
.Larchive_failed:
    scf
.Lrestore_op1:
    pop hl
    ld (op1 + 6), hl
    pop hl
    ld (op1 + 3), hl
    pop hl
    ld (op1), hl
    jr c, .Lneed_archive

    call free_memory
    ld de, guard_required
    or a, a
    sbc hl, de
    jr c, .Lneed_memory
    pop iy
    ld hl, guard_source
    jp guard_resume

.Lneed_archive:
    ld hl, .Larchive_message
    jr .Lshow_message
.Lneed_memory:
    ld hl, .Lmemory_message
.Lshow_message:
    push hl
    call clear_screen
    call home_up
    pop hl
    call put_string
    call get_key
    pop iy
    ret

.Larchive_message:
    .asciz "Send program to Archive"
.Lmemory_message:
    .asciz "Free more calculator RAM"
.Lprogram_name:
    /* The packager appends the TI variable type and its eight-byte name. */
