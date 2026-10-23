/* No kernel or device operations. Production helpers supply layout and copies. */
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
typedef uint32_t u32;
typedef uint8_t u8;
#ifdef _WIN32
typedef long long loff_t;
#else
#include <sys/types.h>
#endif
#define PAGE_SIZE 4096
#define SZ_2M 0x200000ULL
#define SZ_512K 0x80000ULL
#define QKX_LO 0x90000000ULL
#define QKX_HI 0x93400000ULL
#define QKX_ENTRY 0x90080000ULL
#define QKX_INITRD 0x93000000ULL
#define QKX_DTB 0x93200000ULL
#define QKX_MAX_PAGES 32768
#define MAX_ORDER_NR_PAGES 1024UL
#define pageblock_nr_pages 1024UL
#define GFP_KERNEL 0
#define __GFP_ZERO 0
#define __GFP_RETRY_MAYFAIL 0
#define max_t(t,a,b) ((t)(a) > (t)(b) ? (t)(a) : (t)(b))
#define min_t(t,a,b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define ALIGN(n,a) (((n)+(a)-1)&~((a)-1))
#define PAGE_ALIGN(n) ALIGN((n),4096ULL)
#define IS_ALIGNED(n,a) (!((n)&((a)-1)))
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
static u64 destination_lo = QKX_LO, destination_hi = QKX_HI;
static u64 payload_entry = QKX_ENTRY, payload_initrd = QKX_INITRD, payload_dtb = QKX_DTB;
static unsigned long long payload_base;
static u64 get_unaligned_le64(const void *p) { const u8 *b=p; u64 v=0; int i; for(i=7;i>=0;i--)v=v*256+b[i]; return v; }
static u32 get_unaligned_be32(const void *p) { const u8 *b=p; return ((u32)b[0]<<24)|((u32)b[1]<<16)|((u32)b[2]<<8)|b[3]; }
static u64 get_unaligned_be64(const void *p) { const u8 *b=p; return ((u64)get_unaligned_be32(b)<<32)|get_unaligned_be32(b+4); }
static void le64(u8 *p,u64 n) {int i;for(i=0;i<8;i++){p[i]=n;n>>=8;}}
static void be32(u8 *p,u32 n) {int i;for(i=3;i>=0;i--){p[i]=n;n>>=8;}}
static void be64(u8 *p,u64 n) {be32(p,n>>32);be32(p+4,n);}
struct page { u64 pa; u8 data[4096]; };
static struct page pool[16], *owned_store[QKX_MAX_PAGES], **owned = owned_store;
static unsigned int nr_owned, used;
static struct page *alloc_page(unsigned int flags) {return used < 16 ? &pool[used++] : NULL;}
static u64 page_to_phys(struct page *p) {return p->pa;}
static void *page_address(struct page *p) {return p->data;}
struct file {const u8 *data; u64 size; unsigned int i_mode; int write_count;};
static int read_fail, add_fail, reads, copies;
static u64 destinations[16];
static u8 staged[16][4096];
#ifdef QKX_TEST_WRITE_RACES
static void qkx_test_read_race(struct file *f, bool after);
#endif
static int qkx_read(struct file *f,void *b,size_t n,loff_t *p)
{
    reads++;
#ifdef QKX_TEST_WRITE_RACES
    qkx_test_read_race(f, false);
#endif
    if(read_fail)return -EIO;
    memcpy(b,f->data+*p,n);
#ifdef QKX_TEST_WRITE_RACES
    qkx_test_read_race(f, true);
#endif
    *p+=n;
    return 0;
}
static int qkx_add(void *p,u64 d) {if(add_fail)return -ENOMEM;destinations[copies]=d;memcpy(staged[copies++],p,4096);return 0;}
static u32 crc32_le(u32 c,const void *p,size_t n) {return c;}
/* PRODUCTION */
#define CHECK(x) do {if(!(x)){fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 1;}}while(0)
static size_t make_dtb(u8 *b,u64 start,u64 end)
{
    /* Root, chosen, two 64-bit initrd properties, then strings. */
    size_t p=56,s;
    memset(b,0,512);be32(b,0xd00dfeed);be32(b+8,56);be32(b+16,40);be32(b+20,17);be32(b+24,16);
    be32(b+p,1);p+=8;be32(b+p,1);memcpy(b+p+4,"chosen",7);p+=12;
    be32(b+p,3);be32(b+p+4,8);be32(b+p+8,0);be64(b+p+12,start);p+=20;
    be32(b+p,3);be32(b+p+4,8);be32(b+p+8,19);be64(b+p+12,end);p+=20;
    be32(b+p,2);be32(b+p+4,2);be32(b+p+8,9);p+=12;s=p;
    memcpy(b+s,"linux,initrd-start\0linux,initrd-end\0",36);p+=36;
    be32(b+4,p);be32(b+12,s);be32(b+32,36);be32(b+36,s-56);return p;
}
int main(int argc,char **argv)
{
    const char *c=argv[1];u8 h[64]={0},b[512],data[5000];u64 old;size_t n;int i;
    memcpy(h+56,"ARM\x64",4);le64(h+8,0x80000);le64(h+16,0x1dd1000);le64(h+24,0xa);
    for(i=0;i<16;i++)pool[i].pa=0x200000000ULL+i*4096;
    payload_base=0x100000000ULL;
    if(!strcmp(c,"legacy")){payload_base=0;CHECK(qkx_payload_layout(h,64,1024,72)==0);CHECK(destination_lo==QKX_LO&&destination_hi==QKX_HI&&payload_entry==QKX_ENTRY&&payload_initrd==QKX_INITRD&&payload_dtb==QKX_DTB);return 0;}
    CHECK(qkx_payload_layout(h,64,2676670,72)==0);
    if(!strcmp(c,"compact")){CHECK(destination_lo==0x100000000ULL&&destination_hi==0x102400000ULL);CHECK(payload_entry==0x100080000ULL&&payload_initrd==0x101e51000ULL&&payload_dtb==0x1020df000ULL);return 0;}
    if(!strcmp(c,"bad_layout")){
        const u64 flags[]={0xb,0xc,2,0x1a};old=destination_hi;
        for(i=0;i<4;i++){le64(h+24,flags[i]);CHECK(qkx_payload_layout(h,64,2676670,72)==-EINVAL);}
        le64(h+24,0xa);le64(h+8,3);CHECK(qkx_payload_layout(h,64,1,72)==-EINVAL);
        le64(h+8,0x80000);le64(h+16,~0ULL);CHECK(qkx_payload_layout(h,64,1,72)==-EINVAL);
        le64(h+16,0x1dd1000);payload_base=~0ULL-0x3fffff;CHECK(qkx_payload_layout(h,64,1,72)==-EINVAL);
        payload_base=0x100000000ULL;CHECK(qkx_payload_layout(h,64,0,72)==-EINVAL);CHECK(qkx_payload_layout(h,64,0x4000000,72)==-EINVAL);
        CHECK(destination_hi==old);return 0;
    }
    n=make_dtb(b,payload_initrd,payload_initrd+2676670);
    if(!strcmp(c,"dtb_chosen_alias")){
        memmove(b+84,b+64,n-64);be32(b+64,1);memset(b+68,0,12);memcpy(b+68,"chosen@0",9);be32(b+80,2);
        be32(b+4,n+20);be32(b+12,get_unaligned_be32(b+12)+20);be32(b+36,get_unaligned_be32(b+36)+20);
        CHECK(qkx_check_dtb(b,n+20,2676670)<0);return 0;
    }
    if(!strcmp(c,"dtb_property_order")){
        memmove(b+88,b+76,n-76);be32(b+76,1);memcpy(b+80,"sub",4);be32(b+84,2);
        be32(b+4,n+12);be32(b+12,get_unaligned_be32(b+12)+12);be32(b+36,get_unaligned_be32(b+36)+12);
        CHECK(qkx_check_dtb(b,n+12,2676670)<0);return 0;
    }
    if(!strcmp(c,"dtb")){CHECK(qkx_check_dtb(b,n,2676670)==0);be64(b+88,payload_initrd+1);CHECK(qkx_check_dtb(b,n,2676670)<0);make_dtb(b,payload_initrd,payload_initrd+1);CHECK(qkx_check_dtb(b,n,2676670)<0);return 0;}
    if(!strcmp(c,"bad_dtb")){
        CHECK(qkx_check_dtb(b,n-1,2676670)<0);be32(b+8,0xfffffff0);CHECK(qkx_check_dtb(b,n,2676670)<0);
        make_dtb(b,payload_initrd,payload_initrd+2676670);be32(b+80,0xffffffff);CHECK(qkx_check_dtb(b,n,2676670)<0);
        make_dtb(b,payload_initrd,payload_initrd+2676670);be32(b+84,0xffffffff);CHECK(qkx_check_dtb(b,n,2676670)<0);
        make_dtb(b,payload_initrd,payload_initrd+2676670);be32(b+56,42);CHECK(qkx_check_dtb(b,n,2676670)<0);return 0;
    }
    if(!strcmp(c,"pages")){pool[0].pa=destination_lo;pool[1].pa=destination_hi-4096;pool[2].pa=QKX_LO;CHECK(qkx_page()==pool[2].data&&nr_owned==3);return 0;}
    memset(data,0xa5,sizeof(data));
    if(!strcmp(c,"stage_buffer")){struct file f={NULL};read_fail=1;CHECK(qkx_stage(&f,payload_dtb,72,4096,b)==0);CHECK(!reads&&copies==1&&!memcmp(staged[0],b,72));return 0;}
    if(!strcmp(c,"stage_fail")){struct file f={data};read_fail=1;CHECK(qkx_stage(&f,payload_entry,5000,8192,NULL)==-EIO&&!copies);read_fail=0;add_fail=1;CHECK(qkx_stage(&f,payload_entry,5000,8192,NULL)==-ENOMEM&&!copies);return 0;}
    if(!strcmp(c,"stage")){struct file f={data};CHECK(qkx_stage(&f,payload_entry,5000,12288,NULL)==0&&copies==3);CHECK(destinations[0]==payload_entry&&destinations[2]==payload_entry+8192);CHECK(!memcmp(staged[0],data,4096)&&!memcmp(staged[1],data+4096,904));for(i=904;i<4096;i++)CHECK(!staged[1][i]);for(i=0;i<4096;i++)CHECK(!staged[2][i]);return 0;}
    return 1;
}
