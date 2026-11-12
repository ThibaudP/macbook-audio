// SPDX-License-Identifier: GPL-3.0-only
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>
#include "lv2.h"
#include "apple-stack.c"

enum { STACK_PORTS=33 };
typedef struct {
    AppleStack dsp;
    float *ports[STACK_PORTS];
    float *telemetry;
    unsigned int sequence;
    int configured;
} StackPlugin;

static LV2_Handle instantiate(const LV2_Descriptor *descriptor,double rate,
    const char *bundle,const LV2_Feature *const *features)
{
    if(rate!=48000.0)return 0;
    StackPlugin *p=calloc(1,sizeof(*p));if(!p)return 0;
    apple_stack_reset(&p->dsp);
    const char *directory=getenv("XDG_RUNTIME_DIR");
    if(directory) {
        char path[512];snprintf(path,sizeof(path),"%s/macbook-apple-audio.meters",directory);
        int fd=open(path,O_RDWR|O_CREAT|O_CLOEXEC|O_NOFOLLOW,0600);
        if(fd>=0) {
            if(ftruncate(fd,128)==0) {
                void *memory=mmap(0,128,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
                if(memory!=MAP_FAILED)p->telemetry=memory;
            }
            close(fd);
        }
    }
    return p;
}
static void connect(LV2_Handle handle,uint32_t port,void *location)
{
    if(port<STACK_PORTS)((StackPlugin *)handle)->ports[port]=location;
}
static void activate(LV2_Handle handle)
{
    StackPlugin *p=handle;apple_stack_reset(&p->dsp);p->configured=0;
}
static float control(StackPlugin *p,int port,float fallback)
{
    return p->ports[port] ? *p->ports[port] : fallback;
}
static void run(LV2_Handle handle,uint32_t frames)
{
    StackPlugin *p=handle;unsigned int mask=0,k,offset;
    for(k=0;k<14;k++)if(control(p,6+k,1)>.5f)mask|=1u<<k;
    apple_stack_mask(&p->dsp,mask,p->configured);p->configured=1;
    float left=control(p,20,1),right=control(p,21,1);
    int enabled=control(p,22,1)>.5f,headphones=control(p,23,0)>.5f;
    float peaks[4]={0,0,0,0};unsigned int clipped=0;
    for(offset=0;offset<frames;offset+=256) {
        unsigned int count=frames-offset;if(count>256)count=256;
        float input[512],output[1024];unsigned int i,ch;
        for(i=0;i<count;i++)for(ch=0;ch<2;ch++)input[2*i+ch]=p->ports[ch][offset+i];
        apple_stack_process(&p->dsp,input,output,count,left,right,enabled,headphones);
        for(i=0;i<count;i++)for(ch=0;ch<4;ch++)p->ports[2+ch][offset+i]=output[4*i+ch];
        for(ch=0;ch<4;ch++)peaks[ch]=fmaxf(peaks[ch],p->dsp.peak[ch]);
        clipped+=p->dsp.clipped;
    }
    float latency=enabled && !headphones ? ((mask&2) ? 222.0f : 0)+((mask&(1u<<13)) ? 13.0f : 0) : 0;
    for(k=0;k<4;k++)if(p->ports[24+k])*p->ports[24+k]=peaks[k];
    if(p->ports[28])*p->ports[28]=(float)clipped;
    if(p->ports[29])*p->ports[29]=latency;
    if(p->ports[30])*p->ports[30]=p->dsp.compressor.last_gain;
    if(p->ports[31])*p->ports[31]=p->dsp.dual.detector[0].s[(0x4e4-0x414)/4];
    if(p->ports[32])*p->ports[32]=p->dsp.dual.detector[1].s[(0x4e4-0x414)/4];
    if(p->telemetry) {
        float *t=p->telemetry;
        t[1]=1;
        for(k=0;k<4;k++){t[2+k]=peaks[k];t[6+k]=p->dsp.limiter_states[k];}
        t[10]=p->dsp.compressor.last_gain;
        t[11]=p->dsp.dual.detector[0].s[(0x4e4-0x414)/4];
        t[12]=p->dsp.dual.detector[1].s[(0x4e4-0x414)/4];
        t[13]=(float)clipped;t[14]=left;t[15]=right;t[16]=(float)mask;
        t[17]=(float)enabled;t[18]=(float)headphones;
        *((volatile unsigned int *)t)=++p->sequence;
    }
}
static void cleanup(LV2_Handle handle)
{
    StackPlugin *p=handle;if(p->telemetry)munmap(p->telemetry,128);free(p);
}
static const LV2_Descriptor descriptor={
    "urn:macbook-audio:apple-stack-profile0",instantiate,connect,activate,run,0,cleanup,0
};
LV2_SYMBOL_EXPORT const LV2_Descriptor *lv2_descriptor(uint32_t index)
{
    return index==0 ? &descriptor : 0;
}
