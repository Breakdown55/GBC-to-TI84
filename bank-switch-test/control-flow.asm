; Game Boy input for the native test.
jump_from_gb_hl:
    jp hl
jump_to_gb_4412:
    jp $4412
jump_to_gb_0175:
    jp Jump_000_0175
return_from_bank_4:
    ret
gb_000_0200:
    call $4412
gb_000_2760:
    or a
    jr nz, jr_000_2773

; Mario Tennis bank 0 startup, through the first CALL and its nested clear routine.
gb_000_0100:
    nop
    jp Jump_000_0150
gb_000_0150:
    jp Jump_000_2578
gb_000_2578:
    and a
    cp $11
    ld a, $00
    jr nz, jr_000_2580
gb_000_257f:
    inc a
gb_000_2580:
    ldh [$fffe], a
    ld sp, $d000
gb_000_2585:
    call Call_000_0346

gb_000_0346:
    ldh a, [rLCDC]
    bit 7, a
    jr z, jr_000_0367
gb_000_034c:
    ldh a, [rIE]
    ldh [$ff9f], a
    res 0, a
    ldh [rIE], a
gb_000_0354:
    ldh a, [rLY]
    cp $91
    jr nz, jr_000_0354
gb_000_035a:
    ldh a, [rLCDC]
    and $7f
    ldh [rLCDC], a
    xor a
    ldh [rIF], a
    ldh a, [$ff9f]
    ldh [rIE], a
gb_000_0367:
    push hl
    push bc
    call Call_000_0465
gb_000_036c:
    xor a
    ld hl, $ffb8
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    pop bc
    pop hl
    ret

gb_000_0465:
    ld hl, $c0a0
    ld c, $05
    jp Jump_000_03af
gb_000_03af:
    xor a
gb_000_03b0:
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    ld [hl+], a
    dec c
    jr nz, jr_000_03b0
gb_000_03c3:
    ret
