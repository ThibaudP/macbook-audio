// SPDX-License-Identifier: GPL-3.0-only
/* Complete selected speaker graph, with independent bypass of instances 0..13.
 * All executable DSP here is a local reconstruction, fixed at 48000 Hz.
 */
#include "apple-compressor.c"
#include "apple-remaining.c"
#include "apple-spline.c"

typedef struct {
    MacbookCompressor compressor;
    AppleDual dual;
    AppleLoudness loudness;
    double eq_states[5][2][32];
    float limiter_coefficients[NCOEFS],limiter_states[4];
    float bass_delay[13][2];
    unsigned long long clock;
    float weights[14],targets[14];
    float master_weight,peak[4];
    unsigned int clipped;
} AppleStack;

void apple_stack_reset(void *handle)
{
    AppleStack *s=(AppleStack *)handle;memset(s,0,sizeof(*s));
    macbook_compressor_reset(&s->compressor);apple_dual_reset(&s->dual);
    apple_loudness_reset(&s->loudness);
    macbook_spline_coefficients(s->limiter_coefficients,48000,-2.0f,
        3.3036599159240723f,3.3079700469970703f,0,10,0.063510000705719f);
    unsigned int i;for(i=0;i<4;i++)s->limiter_states[i]=1;
    for(i=0;i<14;i++)s->weights[i]=s->targets[i]=1;
    s->master_weight=1;
}
void *apple_stack_create(void)
{
    AppleStack *s=calloc(1,sizeof(*s));if(s)apple_stack_reset(s);return s;
}
void apple_stack_free(void *s){free(s);}
void apple_stack_mask(void *handle,unsigned int mask,int smooth)
{
    AppleStack *s=(AppleStack *)handle;unsigned int i;
    for(i=0;i<14;i++) {
        s->targets[i]=(mask&(1u<<i)) ? 1.0f : 0.0f;
        if(!smooth)s->weights[i]=s->targets[i];
    }
}
static float blend(float dry,float wet,float weight){return dry+(wet-dry)*weight;}

static void stack_eq(AppleStack *s,int index,int stage,float *audio,
                      const double *left,int left_count,const double *right,int right_count)
{
    float wet=stack_cascade(left,left_count,s->eq_states[index][0],audio[0]);
    audio[0]=blend(audio[0],wet,s->weights[stage]);
    wet=stack_cascade(right,right_count,s->eq_states[index][1],audio[1]);
    audio[1]=blend(audio[1],wet,s->weights[stage]);
}

void apple_stack_process(void *handle,const float *input,float *output,unsigned long frames,
                         float left_volume,float right_volume,int master_enabled,int headphones)
{
    AppleStack *s=(AppleStack *)handle;unsigned long i;unsigned int k;
    memset(s->peak,0,sizeof(s->peak));s->clipped=0;
    left_volume=fmaxf(0,fminf(4,left_volume));right_volume=fmaxf(0,fminf(4,right_volume));
    for(i=0;i<frames;i++) {
        float dry[2]={input[2*i],input[2*i+1]},a[2],wet[2],result[4];
        if(!isfinite(dry[0]) || !isfinite(dry[1])) {dry[0]=dry[1]=0;}
        for(k=0;k<14;k++) {
            float delta=s->targets[k]-s->weights[k];
            s->weights[k]+=fmaxf(-1.0f/960.0f,fminf(1.0f/960.0f,delta));
        }
        float target=(master_enabled && !headphones) ? 1.0f : 0.0f;
        s->master_weight+=fmaxf(-1.0f/960.0f,fminf(1.0f/960.0f,target-s->master_weight));
        a[0]=dry[0];a[1]=dry[1];
        stack_eq(s,0,0,a,eq_0_0,8,eq_0_1,8);
        macbook_compressor_process(&s->compressor,a,wet,1);
        for(k=0;k<2;k++)a[k]=blend(a[k],wet[k],s->weights[1]);
        apple_loudness_process(&s->loudness,a,wet,1,left_volume,right_volume,1);
        for(k=0;k<2;k++)a[k]=blend(a[k]*s->loudness.volume[k],wet[k],s->weights[2]);
        apple_dual_process(&s->dual,a,wet,1);
        for(k=0;k<2;k++)a[k]=blend(a[k],wet[k],s->weights[3]);
        a[0]*=blend(1.0f,2.5118863582611084f,s->weights[4]);
        a[1]*=blend(1.0f,2.5192835330963135f,s->weights[4]);
        float bass[2]={a[0]*s->weights[5],a[1]*s->weights[5]};
        stack_eq(s,1,6,a,eq_6_0,8,eq_6_1,8);
        stack_eq(s,2,7,bass,eq_7_0,12,eq_7_1,9);
        stack_eq(s,3,8,a,eq_8_0,11,eq_8_1,11);
        stack_eq(s,4,9,bass,eq_9_0,16,eq_9_1,16);
        for(k=0;k<2;k++) {
            a[k]*=blend(1.0f,0.5956621170043945f,s->weights[10]);
            a[k]=blend(a[k],step(s->limiter_coefficients,s->limiter_states+k,a[k]),s->weights[12]);
            bass[k]=blend(bass[k],step(s->limiter_coefficients,s->limiter_states+2+k,bass[k]),s->weights[11]);
            unsigned int at=(unsigned int)(s->clock%13);
            float delayed=s->bass_delay[at][k];s->bass_delay[at][k]=bass[k];
            result[2+k]=blend(bass[k],delayed,s->weights[13]);
            result[k]=a[k];
        }
        result[1]*=blend(1.0f,1.0035309791564941f,s->weights[13]);
        for(k=0;k<4;k++) {
            float passthrough=(headphones && k>1) ? 0 : dry[k%2]*s->loudness.volume[k%2];
            float value=blend(passthrough,result[k],s->master_weight);
            if(!isfinite(value))value=0;
            output[4*i+k]=value;
            s->peak[k]=fmaxf(s->peak[k],fabsf(value));
            if(fabsf(value)>1.0f)s->clipped++;
        }
        s->clock++;
    }
}
