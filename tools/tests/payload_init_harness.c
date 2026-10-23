/* Appended to the layout harness prelude; full qkx_init is compiled unchanged. */
#include <stdlib.h>
#ifndef EOPNOTSUPP
#define EOPNOTSUPP 95
#endif
#define __init
#define PAGE_SHIFT 12
#define pr_emerg(...) ((void)0)
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define IS_ERR(p) ((intptr_t)(p)<0)
#define file_inode(f) (f)
#define O_RDONLY 0
#define O_NOFOLLOW 0x20000
#define S_ISREG(mode) (((mode)&0170000)==0100000)
#ifndef ETXTBSY
#define ETXTBSY 26
#endif
enum write_race_phase { RACE_SIZE, RACE_BEFORE_READ, RACE_AFTER_READ, RACE_STAGE, RACE_PHASES };
static void qkx_test_write_race(struct file *f, unsigned int phase);
static u64 i_size_read(struct file *f) {qkx_test_write_race(f,RACE_SIZE);return f->size;}
static bool execute,validate_ram,ram_diagnose,preserve_watchdog,watchdog_recovery,watchdog_control;
static bool test_transition,test_full_copy,post_shutdown_probe,warm_reset_test,flush_rpmh,suspend_syncboss,disconnect_qmp;
static unsigned int test_cpus,phase_delay_ms,preflight_only,core_hang_control,secure_watchdog_control;
static unsigned long long ram_base,ram_bytes;
static const char *image="image",*initrd="initrd",*dtb="dtb",*model="Hollywood";
static struct file files[3];
static int opens,closes,stage_calls,fail_stage,claims,releases,executes;
static int deny_calls,allow_calls,blocked_writes,walk_ret,open_fail;
static bool check_write_locks,no_ram;
static bool check_write_races;
static unsigned int race_calls[RACE_PHASES],race_files[RACE_PHASES],race_failures;
static unsigned int writer_gets,writer_puts,writer_denied;
static bool destination_owned,ram_found;
static void *first_desc,*last_desc;
static void (*release_destination)(unsigned long,unsigned int);
struct resource {u64 start,end;};
static void *of_root;
static int of_property_read_string(void *n,const char *p,const char **v) {*v=model;return model?0:-EINVAL;}
static int of_machine_is_compatible(const char *s) {return 1;}
static void *vzalloc(size_t n) {return calloc(1,n);}
static void vfree(void *p) {free(p);}
static void __free_page(struct page *p) { }
static struct file *filp_open(const char *p,int flags,int mode) {opens++;if(opens==open_fail)return ERR_PTR(-EACCES);return &files[p==image?0:p==initrd?1:2];}
static int deny_write_access(struct file *f) {deny_calls++;if(f->write_count>0)return -ETXTBSY;f->write_count--;return 0;}
static void allow_write_access(struct file *f) {if(f->write_count>=0)abort();f->write_count++;allow_calls++;}
/* Pinned 6bde5fc include/linux/fs.h:2924-2935: get_write_access uses
 * atomic_inc_unless_negative; put_write_access decrements an acquired writer.
 * These deterministic interleavings model that counter protocol, not threads
 * or a live filesystem. A successful writer actually mutates fixture storage.
 */
static int get_write_access(struct file *f)
{
    writer_gets++;
    if (f->write_count < 0) {
        writer_denied++;
        return -ETXTBSY;
    }
    f->write_count++;
    return 0;
}
static void put_write_access(struct file *f)
{
    if (f->write_count <= 0) abort();
    f->write_count--;
    writer_puts++;
}
static int competing_mutation(struct file *f, bool truncate)
{
    int ret = get_write_access(f);
    if (ret) return ret;
    if (truncate) f->size = 0;
    else ((u8 *)f->data)[0] ^= 0xff;
    put_write_access(f);
    return 0;
}
static void qkx_test_write_race(struct file *f, unsigned int phase)
{
    u8 before;
    u64 size;
    int write_ret,truncate_ret;
    if (!check_write_races) return;
    before = f->data[0];
    size = f->size;
    race_calls[phase]++;
    race_files[phase] |= 1U << (f-files);
    write_ret = competing_mutation(f,false);
    truncate_ret = competing_mutation(f,true);
    if (write_ret != -ETXTBSY || truncate_ret != -ETXTBSY ||
        f->data[0] != before || f->size != size) {
        race_failures++;
        fprintf(stderr,"competing writer admitted at phase %u\n",phase);
    }
}
static void qkx_test_read_race(struct file *f, bool after)
{ qkx_test_write_race(f,after ? RACE_AFTER_READ : RACE_BEFORE_READ); }
static void filp_close(struct file *f,void *unused) {closes++;}
static int qkx_resolve(void) {return 0;}
static int walk_ram(u64 lo,u64 hi,void *p,int (*cb)(struct resource *,void *)) {struct resource r={0x80000000,0x1ffffffff};return walk_ret?walk_ret:no_ram?0:cb(&r,p);}
static uintptr_t lookup(const char *s) {return !strcmp(s,"walk_system_ram_res")?(uintptr_t)walk_ram:0;}
static int qkx_validate_ram(void) {return -ENOSYS;}
static void free_range(unsigned long p,unsigned int n) {releases++;if(p!=(destination_lo>>12)||n!=(destination_hi-destination_lo)/4096)abort();}
static int qkx_claim_destination(void) {claims++;destination_owned=true;release_destination=free_range;return 0;}
static int qkx_stage(struct file *f,u64 dest,u64 size,u64 memory,const void *buffer)
{int i=stage_calls++;qkx_test_write_race(f,RACE_STAGE);destinations[i]=dest;if(check_write_locks&&f->write_count<0)blocked_writes++;if(i==2&&payload_base&&(!buffer||memcmp(buffer,f->data,size)))return -EINVAL;return stage_calls==fail_stage?-EIO:0;}
static int qkx_identity_map(void) {return 0;}
static int qkx_core_hang_configure(void) {return -ENOSYS;}
static int qkx_secure_watchdog_configure(void) {return -ENOSYS;}
static int qkx_test_transition(void) {return -ENOSYS;}
static int qkx_test_full_copy(void) {return -ENOSYS;}
static int qkx_test_cpus(void) {return -ENOSYS;}
static int qkx_execute(void) {executes++;return 0;}
static void qkx_phase(int n,const char *s,bool b) { }
#define msleep(n) ((void)0)
#define WRITE_ONCE(x,v) ((x)=(v))
/* PRODUCTION */
#define CHECK(x) do {if(!(x)){fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 1;}}while(0)
/* FIXTURE */
int main(int argc,char **argv)
{
    const char *c=argv[1];u8 h[64]={0},b[512],rd=0xa5,original[3];u64 sizes[3];size_t n;int ret,i;
    memcpy(h+56,"ARM\x64",4);le64(h+8,0x80000);le64(h+16,0x1dd1000);le64(h+24,0xa);
    payload_base=0x100000000ULL;n=make_dtb(b,0x101e51000ULL,0x1020de7beULL);
    image="/data/local/tmp/image";initrd="/data/local/tmp/initrd";dtb="/data/local/tmp/dtb";
    owned=NULL;
    files[0]=(struct file){h,64,0100000};files[1]=(struct file){NULL,2676670,0100000};files[2]=(struct file){b,n,0100000};
    if(!strcmp(c,"init_bad_dtb"))be64(b+88,0x93000000);
    if(!strcmp(c,"init_gate"))execute=true;
    if(!strcmp(c,"init_cleanup")){payload_base=0;model="Seacliff";execute=true;fail_stage=2;files[1].size=1024;}
    if(!strcmp(c,"init_locked_staging"))check_write_locks=true;
    if(!strcmp(c,"init_writer"))files[0].write_count=1;
    if(!strcmp(c,"init_later_writer"))files[2].write_count=1;
    if(!strcmp(c,"init_open_failure"))open_fail=2;
    if(!strcmp(c,"init_nonregular"))files[0].i_mode=0040000;
    if(!strcmp(c,"init_walk_error"))walk_ret=-EACCES;
    if(!strcmp(c,"init_no_ram"))no_ram=true;
    if(!strcmp(c,"init_write_races")||!strcmp(c,"init_write_races_cleanup")) {
        check_write_races=true;
        files[1].data=&rd;
        if(!strcmp(c,"init_write_races_cleanup"))fail_stage=2;
        for(i=0;i<3;i++){original[i]=files[i].data[0];sizes[i]=files[i].size;}
    }
    ret=qkx_init();
    if(check_write_races) {
        CHECK(!race_failures);
        CHECK(ret==(fail_stage ? -EIO : 0));
        CHECK(stage_calls==(fail_stage ? 2 : 3));
        CHECK(deny_calls==3&&allow_calls==3&&closes==3);
        CHECK(race_calls[RACE_SIZE]==3&&race_files[RACE_SIZE]==7);
        CHECK(race_calls[RACE_BEFORE_READ]==3&&race_files[RACE_BEFORE_READ]==5);
        CHECK(race_calls[RACE_AFTER_READ]==3&&race_files[RACE_AFTER_READ]==5);
        CHECK(race_calls[RACE_STAGE]==(unsigned int)stage_calls);
        CHECK(race_files[RACE_STAGE]==(fail_stage ? 3U : 7U));
        CHECK(writer_gets==2U*(9+stage_calls)&&writer_denied==writer_gets&&!writer_puts);
        for(i=0;i<3;i++) {
            CHECK(files[i].data[0]==original[i]&&files[i].size==sizes[i]&&!files[i].write_count);
            CHECK(competing_mutation(&files[i],false)==0);
            CHECK(files[i].data[0]==(u8)(original[i]^0xff));
            CHECK(competing_mutation(&files[i],true)==0);
            CHECK(!files[i].size&&!files[i].write_count);
        }
        CHECK(writer_puts==6&&writer_gets==writer_denied+6);
        qkx_release();
    }
    else if(!strcmp(c,"init_compact")){CHECK(!ret&&stage_calls==3&&!claims);CHECK(destinations[0]==0x100080000ULL&&destinations[1]==0x101e51000ULL&&destinations[2]==0x1020df000ULL);CHECK(closes==3);qkx_release();}
    else if(!strcmp(c,"init_bad_dtb")){CHECK(ret==-EINVAL&&!stage_calls&&!claims&&closes==3&&!owned);}
    else if(!strcmp(c,"init_gate")){CHECK(ret==-EOPNOTSUPP&&!opens&&!stage_calls&&!claims&&!executes);}
    else if(!strcmp(c,"init_cleanup")){CHECK(ret==-EIO&&claims==1&&releases==1&&!destination_owned&&!owned&&closes==3&&!executes);}
    else if(!strcmp(c,"init_locked_staging")){CHECK(!ret&&blocked_writes==3&&deny_calls==3&&allow_calls==3&&closes==3);CHECK(!files[0].write_count&&!files[1].write_count&&!files[2].write_count);qkx_release();}
    else if(!strcmp(c,"init_writer")){CHECK(ret==-ETXTBSY&&opens==1&&closes==1&&deny_calls==1&&!allow_calls&&!stage_calls&&files[0].write_count==1);}
    else if(!strcmp(c,"init_later_writer")){CHECK(ret==-ETXTBSY&&opens==3&&closes==3&&deny_calls==3&&allow_calls==2&&!stage_calls);CHECK(!files[0].write_count&&!files[1].write_count&&files[2].write_count==1);}
    else if(!strcmp(c,"init_open_failure")){CHECK(ret==-EACCES&&opens==2&&closes==1&&deny_calls==1&&allow_calls==1&&!files[0].write_count&&!stage_calls);}
    else if(!strcmp(c,"init_nonregular")){CHECK(ret==-EINVAL&&opens==1&&closes==1&&!deny_calls&&!allow_calls&&!stage_calls);}
    else if(!strcmp(c,"init_walk_error")){CHECK(ret==-EACCES&&opens==3&&closes==3&&allow_calls==3&&!stage_calls&&!claims);}
    else if(!strcmp(c,"init_no_ram")){CHECK(ret==-ERANGE&&closes==3&&allow_calls==3&&!stage_calls&&!claims);}
    else return 1;
    return 0;
}
