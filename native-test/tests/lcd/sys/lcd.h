/* Host-only LCD model for renderer regression tests. */
#include <stdint.h>
#define LCD_WIDTH 320
#define LCD_HEIGHT 240
#define LCD_SIZE (320 * 240 * 2)
extern uint8_t test_lcd[LCD_SIZE + 16];
extern uint16_t test_palette[256];
extern uintptr_t test_control, test_base;
#define lcd_Ram test_lcd
#define lcd_Palette test_palette
#define lcd_Control test_control
#define lcd_UpBase test_base
