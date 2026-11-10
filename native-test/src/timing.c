#include "timing.h"
#include "runtime.h"

enum {
    IRQ_VBLANK = 1, IRQ_STAT = 2, IRQ_TIMER = 4, IRQ_MASK = 0x1f,
    LCD_ENABLED = 0x80, VISIBLE_LINES = 144, TOTAL_LINES = 154,
    MODE_2_END = 80, MODE_3_END = 252, LINE_DOTS = 456,
    HALT_WAIT_LIMIT = LINE_DOTS * TOTAL_LINES * 4,
    HALT_MAX_STEP = 252,
    DISPATCH_NONE = 0, DISPATCH_FAULT = 1, DISPATCH_HALTED = 2,
};

uint8_t gb_timing_enabled;
uint8_t gb_timing_extra;
uint24_t gb_frame_count;

/* The native boundary charges the last instruction before starting another. */
static uint8_t staged_cycles;
static uint8_t staged_double_speed;
static uint8_t ime_delay;

static uint16_t divider;
static uint8_t tima;
static uint8_t reload_cycles;

static uint16_t dot;
static uint8_t line;
static uint8_t mode;
static uint8_t stat_signal;

static uint8_t double_speed(void)
{
    return (gb_key1 & 0x80) != 0;
}

static uint16_t timer_period(void)
{
    static const uint16_t periods[] = {1024, 16, 64, 256};
    return periods[gb_tac & 3];
}

static void timer_tick(void)
{
    if (++tima == 0) {
        reload_cycles = 4;
    }
}

static void advance_timer(uint8_t cycles)
{
    if (!(gb_tac & 4) && !reload_cycles) {
        divider += cycles;
        return;
    }
    uint16_t period = timer_period();
    while (cycles) {
        uint16_t tick = period - (divider & (period - 1));
        uint8_t elapsed = cycles;
        if ((gb_tac & 4) && tick < elapsed) {
            elapsed = tick;
        }
        if (reload_cycles && reload_cycles < elapsed) {
            elapsed = reload_cycles;
        }
        cycles -= elapsed;
        divider += elapsed;
        if (reload_cycles) {
            reload_cycles -= elapsed;
            if (!reload_cycles) {
                tima = gb_tma;
                gb_if |= IRQ_TIMER;
            }
        }
        if ((gb_tac & 4) && elapsed == tick) {
            timer_tick();
        }
    }
}

static void update_stat(void)
{
    uint8_t signal = (gb_lcdc & LCD_ENABLED) &&
                     ((mode != 3 && (gb_stat & (8 << mode))) ||
                      ((gb_stat & 0x40) && gb_ly == gb_lyc));
    if (signal && !stat_signal) {
        gb_if |= IRQ_STAT;
    }
    stat_signal = signal;
}

static uint16_t next_lcd_dot(void)
{
    if (mode == 2) {
        return MODE_2_END;
    }
    if (mode == 3) {
        return MODE_3_END;
    }
    if (line == TOTAL_LINES - 1 && dot < 4) {
        return 4;
    }
    return LINE_DOTS;
}

static void advance_lcd(uint8_t dots)
{
    if (!(gb_lcdc & LCD_ENABLED)) {
        return;
    }
    while (dots) {
        uint16_t end = next_lcd_dot();
        uint16_t remaining = end - dot;
        if (dots < remaining) {
            dot += dots;
            return;
        }
        dots -= remaining;
        dot = end;
        if (end == LINE_DOTS) {
            dot = 0;
            if (++line == TOTAL_LINES) {
                line = 0;
            }
            gb_ly = line;
            mode = line < VISIBLE_LINES ? 2 : 1;
            if (line == VISIBLE_LINES) {
                ++gb_frame_count;
                gb_if |= IRQ_VBLANK;
            }
        } else if (line == TOTAL_LINES - 1) {
            gb_ly = 0;
        } else {
            mode = mode == 2 ? 3 : 0;
        }
        update_stat();
    }
}

static void advance(uint8_t cycles, uint8_t fast)
{
    advance_timer(cycles);
    advance_lcd(fast ? cycles >> 1 : cycles);
}

void gb_timing_init(void)
{
    staged_cycles = 0;
    staged_double_speed = 0;
    gb_timing_extra = 0;
    gb_frame_count = 0;
    gb_di();
    divider = 0;
    tima = 0;
    reload_cycles = 0;
    dot = 0;
    line = 0;
    gb_ly = 0;
    mode = (gb_lcdc & LCD_ENABLED) ? 2 : 0;
    stat_signal = 0;
    update_stat();
}

uint8_t gb_timing_read(uint8_t offset)
{
    switch (offset) {
    case 0x04:
        return divider >> 8;
    case 0x05:
        return tima;
    case 0x06:
        return gb_tma;
    case 0x07:
        return gb_tac | 0xf8;
    case 0x40:
        return gb_lcdc;
    case 0x41:
        return 0x80 | gb_stat | (gb_ly == gb_lyc ? 4 : 0) | mode;
    case 0x44:
        return gb_ly;
    case 0x45:
        return gb_lyc;
    default:
        return 0xff;
    }
}

void gb_timing_write(uint8_t offset, uint8_t value)
{
    switch (offset) {
    case 0x04:
        if ((gb_tac & 4) && (divider & (timer_period() >> 1))) {
            timer_tick();
        }
        divider = 0;
        break;
    case 0x05:
        tima = value;
        reload_cycles = 0;
        break;
    case 0x06:
        gb_tma = value;
        break;
    case 0x07: {
        uint8_t old = gb_tac;
        uint8_t was_high = divider & (timer_period() >> 1) ? 1 : 0;
        gb_tac = value & 7;
        if ((old & 4) && was_high &&
            ((gb_tac & 4) ? !(divider & (timer_period() >> 1)) : !gb_cgb)) {
            timer_tick();
        }
        break;
    }
    case 0x40: {
        uint8_t previous = gb_lcdc;
        gb_lcdc = value;
        if ((previous ^ value) & LCD_ENABLED) {
            dot = 0;
            line = 0;
            gb_ly = 0;
            mode = (value & LCD_ENABLED) ? 2 : 0;
            update_stat();
        }
        break;
    }
    case 0x41:
        gb_stat = value & 0x78;
        update_stat();
        break;
    case 0x45:
        gb_lyc = value;
        update_stat();
        break;
    }
}

void gb_di(void)
{
    gb_ime = 0;
    ime_delay = 0;
}

void gb_ei(void)
{
    if (!gb_ime && !ime_delay) {
        ime_delay = 2;
    }
}

void gb_timing_enable_ime(void)
{
    gb_ime = 1;
    ime_delay = 0;
}

void gb_timing_flush(void)
{
    uint8_t cycles = staged_cycles + gb_timing_extra;
    staged_cycles = 0;
    gb_timing_extra = 0;
    if (cycles) {
        advance(cycles, staged_double_speed);
        if (ime_delay && !--ime_delay) {
            gb_ime = 1;
        }
    }
}

static uint8_t pending_interrupts(void)
{
    return gb_if & gb_ie & IRQ_MASK;
}

static uint24_t service_interrupt(uint24_t pc, uint8_t pending)
{
    uint8_t vector = 0x40, mask = 1;
    while (!(pending & mask)) {
        mask <<= 1;
        vector += 8;
    }
    gb_if &= ~mask;
    gb_di();
    if (!gb_push_word(pc)) {
        return DISPATCH_FAULT;
    }
    advance(20, double_speed());
    return vector;
}

uint24_t gb_timing_boundary(uint24_t pc, uint24_t cycles)
{
    gb_pc = pc;
    gb_timing_flush();
    uint8_t pending = pending_interrupts();
    if (gb_ime && pending) {
        return service_interrupt(pc, pending);
    }
    staged_cycles = cycles;
    staged_double_speed = double_speed();
    return DISPATCH_NONE;
}

static uint8_t halt_step(uint8_t sources, uint8_t fast)
{
    uint16_t cycles = HALT_MAX_STEP;
    if (sources & (IRQ_VBLANK | IRQ_STAT)) {
        uint16_t lcd = (next_lcd_dot() - dot) << fast;
        if (lcd < cycles) {
            cycles = lcd;
        }
    }
    if (sources & IRQ_TIMER) {
        uint16_t period = timer_period();
        uint16_t tick = period - (divider & (period - 1));
        if ((gb_tac & 4) && tick < cycles) {
            cycles = tick;
        }
        if (reload_cycles && reload_cycles < cycles) {
            cycles = reload_cycles;
        }
    }
    /* HALT resumes at a CPU machine-cycle boundary. */
    return (cycles + 3) & ~3;
}

uint24_t gb_timing_halt(uint24_t resume)
{
    uint8_t pending = pending_interrupts();
    if (pending && !gb_ime) {
        if (ime_delay == 1) {
            /* EI followed by HALT returns to the HALT after the handler. */
            gb_timing_flush();
            return service_interrupt((uint16_t)(resume - 1), pending);
        }
        /* The duplicated-fetch HALT bug needs a separate decoder path. */
        return DISPATCH_FAULT;
    }
    gb_timing_flush();
    uint8_t sources = 0;
    if (gb_lcdc & LCD_ENABLED) {
        sources |= IRQ_VBLANK;
        if ((gb_stat & 0x38) || ((gb_stat & 0x40) && gb_lyc < TOTAL_LINES)) {
            sources |= IRQ_STAT;
        }
    }
    if ((gb_tac & 4) || reload_cycles) {
        sources |= IRQ_TIMER;
    }
    sources &= gb_ie;
    pending = pending_interrupts();
    if (!pending && !sources) {
        return DISPATCH_HALTED;
    }
    uint8_t fast = double_speed();
    /* Two double-speed LCD frames also exceed the longest timer overflow. */
    for (uint24_t elapsed = 0; !pending && elapsed < HALT_WAIT_LIMIT;) {
        uint8_t cycles = halt_step(sources, fast);
        advance(cycles, fast);
        elapsed += cycles;
        pending = pending_interrupts();
    }
    if (!pending) {
        return DISPATCH_HALTED;
    }
    return gb_ime ? service_interrupt(resume, pending) : DISPATCH_NONE;
}
