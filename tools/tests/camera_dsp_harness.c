/* Host doubles only: no headset, kernel, or hardware operations. */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#include "diag_profile.h"
typedef uint64_t u64;
typedef unsigned char u8;
struct mutex { int held; char padding[28]; };
struct work_struct { char data[48]; };
struct list_head { struct list_head *next, *prev; };
struct device_driver { const char *name; char pad[160]; };
struct device { char pad[128]; struct mutex mutex; char gap[8]; struct device_driver *driver; char tail[776]; };
static const char *device_name="soc:qcom,cam-req-mgr";
static const char *dev_name(struct device *d) { return device_name; }
static void *busy_lock, *fault_address;
static int held, tries, reads, busy_calls;
static int mutex_trylock(struct mutex *m) { tries++; if (m==busy_lock) return 0; if(m->held) abort(); m->held=1;held++; return 1; }
static void mutex_unlock(struct mutex *m) { if(!m->held) abort();m->held=0;held--; }
static int device_trylock(struct device *d) { return mutex_trylock(&d->mutex); }
static void device_unlock(struct device *d) { mutex_unlock(&d->mutex); }
static int probe_kernel_read(void *to,const void *from,size_t size)
{ reads++; if(from==fault_address || !from) return -EFAULT;memcpy(to,from,size);return 0; }
static unsigned int work_busy(struct work_struct *w) { busy_calls++;return 2; }
static bool observe;
static struct { struct { char release[80]; } name; } init_uts_ns;
static void *of_root=(void *)1;
static const char *model="Hollywood (DVT)";
static int compatible=1;
static int of_property_read_string(void *n,const char *key,const char **v) { *v=model;return model?0:-EINVAL; }
static int of_machine_is_compatible(const char *name) { return compatible; }
/* PRODUCTION */
static union { u64 alignment; u8 data[QCD_CAM_SIZE]; } cam;
static union { u64 alignment; u8 data[QCD_CORE_SIZE]; } core;
static union { u64 alignment; u8 data[QCD_SESSION_SIZE]; } session;
static union { u64 alignment; u8 data[QCD_LINK_SIZE]; } link;
static union { u64 alignment; u8 data[QCD_WQ_SIZE]; } wq;
static union { u64 alignment; u8 data[QCD_QUEUE_SIZE]; } queue;
static void pointer(void *p, void *value) { memcpy(p,&value,sizeof(value)); }
static void integer(void *p, int value) { memcpy(p,&value,4); }
#define CHECK(x) do { if(!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x);return 1; } } while(0)
int main(int argc,char **argv)
{
    struct device dev={0};struct device_driver driver={"cam_req_mgr"};
    struct qcd_snapshot s={0};void *core_ptr=core.data;
    struct list_head *head=(void *)core.data,*entry=(void *)(session.data+QCD_SESSION_ENTRY);
    const char *name=argv[1];int before,i;
    observe=true;memcpy(init_uts_ns.name.release,QCD_RELEASE,sizeof(QCD_RELEASE));dev.driver=&driver;
    s.cam_address=cam.data;s.core_slot=&core_ptr;
    head->next=entry;head->prev=entry;entry->next=head;entry->prev=head;
    pointer(session.data+QCD_SESSION_LINKS+6*sizeof(void *),link.data);
    integer(session.data+QCD_SESSION_COUNT,1);
    pointer(link.data+QCD_LINK_WORKQ,wq.data);
    pointer(link.data+QCD_LINK_REQUEST+QCD_REQUEST_QUEUE,queue.data);
    integer(cam.data+QCD_CAM_OPEN,3);integer(cam.data+QCD_CAM_SUBDEVS,17);
    integer(wq.data+QCD_WQ_PENDING,2);integer(wq.data+QCD_WQ_FREE,7);integer(wq.data+QCD_WQ_TASKS,9);
    integer(link.data+QCD_LINK_OPEN,5);integer(queue.data+QCD_QUEUE_COUNT,48);
    integer(queue.data+QCD_QUEUE_READ,2);integer(queue.data+QCD_QUEUE_WRITE,4);
    if(!strcmp(name,"guards")) {
        const u8 code[68]="\xfd\x7b\xbe\xa9\xf4\x4f\x01\xa9\xfd\x03\x00\x91\xd4\x96\x00\xb0\x94\x52\x05\x91\x93\x12\x00\x91\xe0\x03\x13\xaa\x8a\xd3\x13\x94\x88\x02\x40\xb9\xe0\x03\x13\xaa\x1f\x01\x00\x71\xf4\x07\x9f\x1a\x98\xd3\x13\x94\xe0\x03\x14\x2a\xf4\x4f\x41\xa9\xfd\x7b\xc2\xa8\xc0\x03\x5f\xd6";
        CHECK(qcd_anchor(code)==0);
        CHECK(qcd_target()==0);observe=false;CHECK(qcd_target()!=0);observe=true;
        model="Other";CHECK(qcd_target()!=0);model="Hollywood (DVT)";
        compatible=0;CHECK(qcd_target()!=0);compatible=1;
        init_uts_ns.name.release[0]='x';CHECK(qcd_target()!=0);
        CHECK(qcd_hash((u8 *)"hello",5)==0xa430d84680aabd0bULL);
        before=reads;CHECK(qcd_anchor((void *)(uintptr_t)cam.data)!=0);CHECK(reads==before+1);
        CHECK(tries==0);return 0;
    }
    if(!strcmp(name,"device_busy")) busy_lock=&dev.mutex;
    if(!strcmp(name,"driver")) driver.name="different";
    if(!strcmp(name,"camera_busy")) busy_lock=cam.data+QCD_CAM_LOCK;
    if(!strcmp(name,"core_busy")) busy_lock=core.data+QCD_CORE_LOCK;
    if(!strcmp(name,"link_busy")) busy_lock=link.data+QCD_LINK_LOCK;
    if(!strcmp(name,"request_busy")) busy_lock=link.data+QCD_LINK_REQUEST+QCD_REQUEST_LOCK;
    if(!strcmp(name,"fault")) fault_address=session.data+QCD_SESSION_LINKS;
    if(!strcmp(name,"cycle")) entry->next=entry;
    if(!strcmp(name,"dsp")) {
        union {u64 align;u8 data[QCD_DSP_SIZE];} dsp={0};
        memcpy(dsp.data+QCD_DSP_NAME,"ssr(adsp)",sizeof("ssr(adsp)"));
        integer(dsp.data+QCD_DSP_COUNT,4);integer(dsp.data+QCD_DSP_TRACK+QCD_TRACK_STATE,2);
        pointer(dsp.data+QCD_DSP_GROUP,(void *)1);
        CHECK(qcd_dsp((void *)(dsp.data+QCD_DSP_DEV),&s)==0);
        CHECK(s.dsp_count==1 && s.dsps[0].refs==4 && s.dsps[0].state==2);
        CHECK(s.dsps[0].group && s.dsps[0].work==2 && busy_calls==2 && tries==0);
        CHECK(!strcmp(s.dsps[0].name,"ssr(adsp)"));
        for(i=1;i<QCD_MAX_DSP;i++) CHECK(qcd_dsp((void *)(dsp.data+QCD_DSP_DEV),&s)==0);
        CHECK(qcd_dsp((void *)(dsp.data+QCD_DSP_DEV),&s)==1 && s.dsp_truncated);
        CHECK(held==0);return 0;
    }
    CHECK(qcd_camera(&dev,&s)==1);CHECK(held==0);
    if(!strcmp(name,"device_busy") || !strcmp(name,"driver")) { CHECK(reads==0);return 0; }
    if(!strcmp(name,"camera_busy")) { CHECK(reads==0 && tries==2);return 0; }
    if(!strcmp(name,"core_busy")) { CHECK(s.cam_open==3 && tries==3);return 0; }
    if(!strcmp(name,"fault")) { CHECK(s.camera_fault);return 0; }
    if(!strcmp(name,"cycle")) { CHECK(s.sessions==QCD_MAX_SESSIONS && s.camera_truncated);return 0; }
    CHECK(s.sessions==1 && s.link_count==1 && s.links[0].slot==6);
    if(!strcmp(name,"link_busy")) { CHECK(s.links[0].busy && !busy_calls);return 0; }
    CHECK(s.links[0].pending==2 && s.links[0].free==7 && s.links[0].work==2);
    if(!strcmp(name,"request_busy")) { CHECK(s.links[0].request_busy);return 0; }
    CHECK(!strcmp(name,"camera"));CHECK(s.cam_open==3 && s.cam_subdevs==17);
    CHECK(s.links[0].open==5 && s.links[0].slots==48 && s.links[0].rd==2 && s.links[0].wr==4);
    CHECK(!s.camera_fault && !s.camera_truncated);return 0;
}
