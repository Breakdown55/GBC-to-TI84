#include "runtime.h"
#include "render.h"
#include "timing.h"
#include <assert.h>
#include <string.h>
uint8_t gb_timing_enabled;
uint8_t gb_oam[GB_OAM_SIZE], gb_oam_dirty, gb_video_dirty;
const GbTarget gb_targets[] = {{0, 0}};
const GbTargetPage gb_target_pages[] = {{0, 0, 0}};
const unsigned gb_target_page_count = 0;
uint8_t gb_timing_read(uint8_t offset) { (void)offset; assert(0); return 0; }
void gb_timing_write(uint8_t offset, uint8_t value) { (void)offset; (void)value; assert(0); }
uint8_t gb_video_read(uint8_t offset) { (void)offset; assert(0); return 0; }
void gb_video_write(uint8_t offset, uint8_t value) { (void)offset; (void)value; assert(0); }
uint8_t gb_vram_dma_read(uint8_t offset) { (void)offset; assert(0); return 0; }
uint8_t gb_vram_dma_write(uint8_t offset, uint8_t value) { (void)offset; (void)value; assert(0); return 0; }
void gb_unmapped(void) { assert(0); }
static unsigned invalidations, bank_seen, offset_seen, length_seen;
void gb_video_invalidate(unsigned bank, unsigned offset, unsigned length)
{
    ++invalidations; bank_seen = bank; offset_seen = offset; length_seen = length;
}
int main(void)
{
    const unsigned addresses[] = {0x8000,0x9fff,0xc000,0xcfff,0xd000,0xdfff,0xe000,0xefff,0xf000,0xfdff,0xff80,0xfffe};
    const unsigned counts[] = {0,1,2,255};
    gb_cgb = 1; gb_a = 7; gb_vbk = 1;
    assert(gb_write_high(0x70));
    for(unsigned use_b=0;use_b<2;use_b++)
    for(unsigned i=0;i<sizeof addresses/sizeof *addresses;i++)
    for(unsigned j=0;j<sizeof counts/sizeof *counts;j++) {
        unsigned address=addresses[i], n=counts[j]?counts[j]:256, canonical=address;
        if(canonical>=0xe000&&canonical<0xfe00)canonical-=0x2000;
        uint8_t *bytes=0;
        if(canonical>=0x8000&&canonical+n<=0xa000)bytes=&gb_vram[1][canonical-0x8000];
        else if(canonical>=0xc000&&canonical<0xe000&&n<=0x1000-(canonical&4095))
            bytes=canonical<0xd000?&gb_wram[canonical&4095]:&gb_wram_banks[6][canonical&4095];
        else if(canonical>=0xff80&&canonical+n<=0xffff)bytes=&gb_hram[canonical-0xff80];
        if(address>=0xe000&&address<0xfe00&&address+n>0xfe00)bytes=0;
        memset(gb_vram,0,sizeof gb_vram);memset(gb_wram,0,sizeof gb_wram);
        memset(gb_wram_banks,0,sizeof gb_wram_banks);memset(gb_hram,0,sizeof gb_hram);
        gb_hl=address;gb_bc=use_b?(counts[j]<<8)|0x53:0x5300|counts[j];gb_a=0xa5;gb_f=0x30;
        unsigned previous=gb_bc;invalidations=0;
        assert(gb_fill_hl(use_b)==(bytes!=0));
        if(bytes) {
            for(unsigned k=0;k<n;k++)assert(bytes[k]==0xa5);
            assert(gb_hl==address+n && gb_bc==(use_b?0x53:0x5300) && gb_a==0xa5 && gb_f==0xd0);
            if(canonical<0xa000)assert(invalidations==1&&bank_seen==1&&offset_seen==canonical-0x8000&&length_seen==n);
            else assert(!invalidations);
        } else assert(gb_hl==address&&gb_bc==previous&&gb_f==0x30&&!invalidations);
    }
    const unsigned rejected[]={0,0x7fff,0xa000,0xfe00,0xff00,0xffff};
    for(unsigned i=0;i<sizeof rejected/sizeof *rejected;i++) {
        gb_hl=rejected[i];gb_bc=1;assert(!gb_fill_hl(0));assert(gb_hl==rejected[i]&&gb_bc==1);
    }
    gb_hl=0x8000;gb_bc=1;gb_a=gb_vram[1][0];invalidations=0;
    assert(gb_fill_hl(0)&&!invalidations);

    gb_hl = 0x8ffc; gb_bc = 0x0235; gb_a = 0x79; gb_f = 0;
    assert(gb_fill_hl(7));
    assert(!memcmp(&gb_vram[1][0xffc], "yyyyyyyy", 8));
    assert(gb_hl == 0x9004 && gb_bc == 0x35 && gb_f == 0xc0);
    assert(invalidations == 1 && offset_seen == 0xffc && length_seen == 8);
    gb_hl = 0xcff9; gb_bc = 2;
    assert(!gb_fill_hl(6) && gb_hl == 0xcff9 && gb_bc == 2);

    for (unsigned i = 0; i < 32; ++i) gb_wram_banks[6][i] = i + 1;
    gb_hl = 0xd000; gb_de = 0x8ff0; gb_bc = 0x5302; gb_f = 0x30;
    invalidations = 0;
    assert(gb_copy_hl_de(0x40 | 16));
    assert(!memcmp(&gb_vram[1][0xff0], gb_wram_banks[6], 32));
    assert(gb_hl == 0xd020 && gb_de == 0x9010 && gb_bc == 0x5300 && gb_a == 32 && gb_f == 0xd0);
    assert(invalidations == 1 && bank_seen == 1 && offset_seen == 0xff0 && length_seen == 32);
    gb_hl = 0xd000; gb_de = 0x8ff0; gb_bc = 2; invalidations = 0;
    assert(gb_copy_hl_de(0x40 | 16) && !invalidations);
    gb_hl = 0xd000; gb_de = 0x8ff1; gb_bc = 2;
    assert(!gb_copy_hl_de(0x40 | 16) && gb_de == 0x8ff1 && gb_bc == 2 && !invalidations);
    gb_hl = 0xc000; gb_de = 0xe001; gb_bc = 2;
    assert(!gb_copy_hl_de(1) && gb_hl == 0xc000 && gb_de == 0xe001);
    gb_hl = 0xd000; gb_de = 0xfdff; gb_bc = 2;
    assert(!gb_copy_hl_de(1) && gb_de == 0xfdff);

    gb_timing_enabled=1;gb_hl=0xc000;gb_bc=1;assert(!gb_fill_hl(0));assert(gb_bc==1);
    gb_de = 0xc100;
    assert(!gb_copy_hl_de(1));

    gb_timing_enabled = 0;
    gb_wram_banks[0][0] = 0x12;
    gb_wram_banks[6][0] = 0x34;
    assert(gb_watch_ram(0xd000) == 0x34 && gb_watch_ram(0xf000) == 0x34);
    gb_a = 0;
    assert(gb_write_high(0x70));
    assert(gb_watch_ram(0xd000) == 0x12 && gb_watch_ram(0xf000) == 0x12);
}
