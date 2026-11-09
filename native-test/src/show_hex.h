#ifndef SHOW_HEX_H
#define SHOW_HEX_H

#include <ti/screen.h>

static void show_hex(unsigned value, unsigned width)
{
    static const char digits[] = "0123456789ABCDEF";
    char text[5];
    text[width] = '\0';
    while (width) {
        text[--width] = digits[value & 0x0f];
        value >>= 4;
    }
    os_PutStrFull(text);
}

#endif
