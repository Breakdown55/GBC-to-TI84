; Synthetic Game Boy blocks for the native regression test.
jump_from_gb_hl:
    jp hl
jump_to_gb_4800:
    jp $4800
jump_to_gb_0321:
    jp Jump_000_0321
return_from_selected_bank:
    ret
gb_000_0800:
    call $4800
gb_000_0900:
    or a
    jr nz, gb_000_0910

; Test an HRAM store, LCD polling, a nested call, and a bounded WRAM clear.
gb_000_1000:
    ld sp, $d000
    ld a, $04
    ldh [$ffb0], a
    call gb_000_1100
gb_000_1100:
    push hl
    push bc
    ldh a, [rLCDC]
    bit 7, a
    jr z, gb_000_1120
gb_000_1108:
    ldh a, [rLY]
    cp $92
    jr nz, gb_000_1108
gb_000_110e:
    ldh a, [rLCDC]
    and $7f
    ldh [rLCDC], a
    jp gb_000_1120
gb_000_1120:
    call gb_000_1140
gb_000_1123:
    pop bc
    pop hl
    ret
gb_000_1140:
    ld hl, $c210
    ld c, $04
    xor a
gb_000_1146:
    ld [hl+], a
    dec c
    jr nz, gb_000_1146
gb_000_114a:
    ret
