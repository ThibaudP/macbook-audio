// SPDX-License-Identifier: GPL-3.0-only
/* Profile-0 loudness and dual-band reconstruction, fixed at 48000 Hz. */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "stack-data.h"

static float stack_biquad(const double *c,double *s,float input)
{
    double x=input,y=c[0]*x+s[0];
    s[0]=c[1]*x-c[3]*y+s[1];s[1]=c[2]*x-c[4]*y;
    return (float)y;
}
static float stack_cascade(const double *c,unsigned int count,double *state,float input)
{
    unsigned int i;
    for (i=0;i<count;i++) {
        const double *b=c+5*i;double *s=state+2*i;
        double x=input,y=b[0]*x+s[0];
        s[0]=b[1]*x-b[3]*y+s[1];s[1]=b[2]*x-b[4]*y;
        input=(float)y;
    }
    return input;
}
static void stack_curve(const float *curve,float db,float *attack,float *release)
{
    db=fminf(80.0f,fmaxf(-160.0f,db));
    float position=(db+160.0f)*100.0f;
    unsigned int i=(unsigned int)position;if(i>=24000)i=23999;
    float t=position-(float)i;
    *attack=curve[2*i]+t*(curve[2*i+2]-curve[2*i]);
    *release=curve[2*i+1]+t*(curve[2*i+3]-curve[2*i+1]);
}
typedef struct {
    float s[69],rms[2];
} DualGain;
typedef struct {
    DualGain detector[2];
    double filters[2][2][10],poles[9][2][2];
    float gain_history[4],color_history[4];
    unsigned long long clock,low_clock;
} AppleDual;

void apple_dual_reset(void *handle)
{
    AppleDual *d=(AppleDual *)handle;memset(d,0,sizeof(*d));
    memcpy(d->detector[0].s,dual_initial_0,sizeof(d->detector[0].s));
    memcpy(d->detector[1].s,dual_initial_1,sizeof(d->detector[1].s));
}
void *apple_dual_create(void)
{
    AppleDual *d=calloc(1,sizeof(*d));if(d)apple_dual_reset(d);return d;
}
void apple_dual_free(void *d) {free(d);}

#define DS(address) (d->s[((address)-0x414)/4])
static float dual_gain(DualGain *d,int band,float left,float right)
{
    const float *r=band ? dual_rms_1 : dual_rms_0;
    d->rms[0]=r[0]*d->rms[0]+left*left;
    d->rms[1]=r[0]*d->rms[1]+right*right;
    float rms=r[1]*d->rms[0]+r[2]*d->rms[1]+1.000000013351432e-10f;
    float db=log10f(rms)*10.0f-0.6909999847412109f;
    float attack,release;
    stack_curve(band ? dual_curves_1 : dual_curves_0,db,&attack,&release);
    attack=expf(attack*0.115129254758358f);
    release=expf(release*0.115129254758358f);
    float gain=DS(0x4e4);
    if(gain>release)gain+=(release-gain)*DS(0x4dc);
    if(attack>gain)gain=(DS(0x4d0)+1.0f)*gain;
    DS(0x4e4)=gain;
    return gain;
}
#undef DS

void apple_dual_process(void *handle,const float *input,float *output,unsigned long frames)
{
    AppleDual *d=(AppleDual *)handle;unsigned long i;
    for(i=0;i<frames;i++) {
        float detector_input[2][2];unsigned int band,ch;
        for(band=0;band<2;band++)for(ch=0;ch<2;ch++)
            detector_input[band][ch]=stack_cascade(band ? dual_filter_1 : dual_filter_0,5,
                                                 d->filters[band][ch],input[2*i+ch]);
        if(d->clock%31==0) {
            float low=dual_gain(d->detector,0,detector_input[0][0],detector_input[0][1]);
            float high=dual_gain(d->detector+1,1,detector_input[1][0],detector_input[1][1]);
            float logarithm=log10f(low/high);
            float exponent=(float)((double)logarithm*2.302585092994/9.0);
            float color=1.0f-expf(exponent);
            d->gain_history[d->low_clock%4]=high;
            d->color_history[d->low_clock%4]=color;
            d->low_clock++;
        }
        unsigned int phase=(unsigned int)(d->clock%31),j;
        float high=0,color=0;
        for(j=0;j<4 && j<d->low_clock;j++) {
            float weight=dual_interpolator[phase+31*j];
            unsigned int index=(unsigned int)((d->low_clock-1-j)%4);
            high+=weight*d->gain_history[index];color+=weight*d->color_history[index];
        }
        for(ch=0;ch<2;ch++) {
            float x=input[2*i+ch]*high;
            for(j=0;j<9;j++) {
                float filtered=stack_biquad(dual_filter_2,d->poles[j][ch],x*color);
                x-=filtered;
            }
            output[2*i+ch]=x;
        }
        d->clock++;
    }
}

typedef struct {
    float current[10],target[10],states[2][2][2];
    float volume[2],last_master;
    int configured;
} AppleLoudness;

void apple_loudness_reset(void *handle)
{
    AppleLoudness *l=(AppleLoudness *)handle;memset(l,0,sizeof(*l));
    l->current[0]=l->current[5]=l->target[0]=l->target[5]=1.0f;
    l->last_master=-1;
}
void *apple_loudness_create(void)
{
    AppleLoudness *l=calloc(1,sizeof(*l));if(l)apple_loudness_reset(l);return l;
}
void apple_loudness_free(void *l){free(l);}
void apple_loudness_target(void *handle,float master)
{
    AppleLoudness *l=(AppleLoudness *)handle;
    float db=master>0 ? 20.0f*log10f(master) : -100.0f;
    db=fminf(6.0f,fmaxf(-100.0f,db));
    float position=(db+100.0f)*100.0f;
    unsigned int i=(unsigned int)position;if(i>=10600)i=10599;
    float t=position-(float)i;unsigned int k;
    for(k=0;k<10;k++)l->target[k]=loudness_coefficients[i*10+k]+t*(loudness_coefficients[(i+1)*10+k]-loudness_coefficients[i*10+k]);
    if(!l->configured){memcpy(l->current,l->target,sizeof(l->current));l->configured=1;}
    l->last_master=master;
}
void apple_loudness_coefficients(void *handle,float *output)
{
    memcpy(output,((AppleLoudness *)handle)->target,40);
}
void apple_loudness_process(void *handle,const float *input,float *output,unsigned long frames,
                            float left_volume,float right_volume,int enabled)
{
    AppleLoudness *l=(AppleLoudness *)handle;unsigned long i;unsigned int ch,section,k;
    float master=fmaxf(left_volume,right_volume);
    if(master!=l->last_master)apple_loudness_target(l,master);
    float a=loudness_smoothing[0],b=1.0f-a;
    for(i=0;i<frames;i++) {
        for(ch=0;ch<2;ch++) {
            float x=input[2*i+ch];
            for(section=0;section<2;section++) {
                float *c=l->current+section*5,*s=l->states[section][ch];
                float y=x*c[0]+s[0];
                s[0]=(x*c[1]-y*c[3])+s[1];s[1]=x*c[2]-y*c[4];x=y;
            }
            float target=ch ? right_volume : left_volume;
            l->volume[ch]=a*l->volume[ch]+b*target;
            output[2*i+ch]=(enabled ? x : input[2*i+ch])*l->volume[ch];
        }
        for(k=0;k<10;k++)l->current[k]=a*l->current[k]+b*l->target[k];
    }
}
