// SPDX-License-Identifier: GPL-3.0-only
/* Reconstruction of the selected profile's first Mozart compressor.
 * Only the MacBookPro10,2 profile-0, stereo 48000 Hz configuration is supported.
 * Coefficients/static curves come from offline emulation, never executable code.
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "compressor-data.h"

typedef struct {
    float s[69];
    double biquad[3][2][2];
    float rms_long[619][2],rms_short[15][2];
    double long_sum,short_sum;
    unsigned long long clock;
    unsigned long long low_clock;
    float gains[12],delay[222][2];
    unsigned int activity,previous_activity,hold;
    float last_gain;
} MacbookCompressor;

#define S(address) (instance->s[((address)-0x414)/4])

void macbook_compressor_reset(void *handle)
{
    MacbookCompressor *instance=(MacbookCompressor *)handle;
    memset(instance,0,sizeof(*instance));
    memcpy(instance->s,compressor_initial,sizeof(instance->s));
}
void *macbook_compressor_create(void)
{
    MacbookCompressor *instance=(MacbookCompressor *)calloc(1,sizeof(*instance));
    if (instance) macbook_compressor_reset(instance);
    return instance;
}
void macbook_compressor_free(void *instance) { free(instance); }

static float gate(MacbookCompressor *instance,float input)
{
    float upper=S(0x428),lower=S(0x434);
    if (input>S(0x440)) {
        if (input>upper) upper=(S(0x424)+1.0f)*upper;
        else if (upper>lower) upper=upper*S(0x420);
        S(0x428)=upper;
    }
    float average=S(0x41c)*S(0x414)+S(0x418)*input;
    S(0x41c)=average;
    if (input>average*S(0x438) && upper*S(0x43c)>input) {
        if (input>lower) lower=(S(0x430)+1.0f)*lower;
        else lower=lower*S(0x42c);
        S(0x434)=lower;
    }
    return lower;
}

static float activity(MacbookCompressor *instance,float input)
{
    float previous=S(0x44c);
    if (input>S(0x444)*previous) {
        S(0x44c)=input;instance->activity=1;previous=input;
    }
    if (previous*S(0x448)>input) {
        S(0x44c)=input;instance->activity=0;
    }
    if (instance->activity!=instance->previous_activity) instance->hold=155;
    else if (instance->hold) instance->hold--;
    else if (input>=S(0x440)) {
        instance->previous_activity=instance->activity;
        return 0.0f;
    }
    instance->previous_activity=instance->activity;
    return 1.0f;
}

void macbook_compressor_curve(float input,float *attack,float *release)
{
    if (input< -160.0f) input=-160.0f;
    if (input>80.0f) input=80.0f;
    float position=(input+160.0f)*100.0f;
    unsigned int index=(unsigned int)position;
    if (index>=24000) index=23999;
    float fraction=position-(float)index;
    *attack=compressor_curves[index*2]+fraction*(compressor_curves[(index+1)*2]-compressor_curves[index*2]);
    *release=compressor_curves[index*2+1]+fraction*(compressor_curves[(index+1)*2+1]-compressor_curves[index*2+1]);
}

float macbook_compressor_gain(void *handle,float rms,float peak_rms)
{
    MacbookCompressor *instance=(MacbookCompressor *)handle;
    float gated=gate(instance,rms);
    float active=activity(instance,gated);
    float db=log10f(gated)*10.0f-0.6909999847412109f;
    float attack_db,release_db;
    macbook_compressor_curve(db,&attack_db,&release_db);
    float attack=expf(attack_db*0.115129254758358f);
    float release=expf(release_db*0.115129254758358f);
    float gain=S(0x4e4);
    if (gain>release) gain+=(release-gain)*S(0x4dc);
    if (attack>gain) gain=(S(0x4d0)*active+1.0f)*gain;
    S(0x4e4)=gain;

    /* Auto-threshold peak control following the slow RMS compressor. */
    float peak=sqrtf(peak_rms)*0.9235278367996216f;
    float threshold=S(0x4f0),previous_factor=S(0x4f4),previous_gain=S(0x510);
    float slope=S(0x494)+1.0f;
    float level=log10f(gain+1.000000013351432e-10f)*-20.0f;
    level=(level*slope+S(0x4a0))/(1.0f-slope);
    level=level*S(0x514)+S(0x518);
    level=level> -40.0f ? level : -40.0f;
    float factor=expf(level*-0.115129254758358f);
    float detected=peak*gain*factor;
    if (detected>threshold) threshold+=(detected-threshold)*S(0x508);
    else {
        detected*=S(0x51c);
        if (threshold>detected) threshold+=(detected-threshold)*S(0x4fc);
    }
    float ratio=gain/previous_gain;
    if (ratio<1.0f) threshold*=ratio;
    threshold=threshold>1.0f ? threshold : 1.0f;
    factor=(3.0f-previous_factor*threshold)*0.5f;
    factor=factor>0.009999999776482582f ? factor : 0.009999999776482582f;
    factor*=previous_factor;
    S(0x4f0)=threshold;S(0x4f4)=factor;S(0x510)=gain;
    instance->last_gain=gain*factor;
    return instance->last_gain;
}

static float detector(MacbookCompressor *instance,float left,float right)
{
    unsigned int j,channel;
    float values[2]={left,right};
    for (j=0;j<3;j++) {
        const double *c=compressor_sidechain+5*j;
        for (channel=0;channel<2;channel++) {
            double input=values[channel];
            double *state=instance->biquad[j][channel];
            double output=c[0]*input+state[0];
            state[0]=c[1]*input-c[3]*output+state[1];
            state[1]=c[2]*input-c[4]*output;
            values[channel]=(float)output;
        }
    }
    if (instance->clock%31==0) {
        unsigned int long_index=(unsigned int)(instance->low_clock%619);
        unsigned int short_index=(unsigned int)(instance->low_clock%15);
        for (channel=0;channel<2;channel++) {
            float square=values[channel]*values[channel];
            instance->long_sum=(instance->long_sum-(double)instance->rms_long[long_index][channel])+(double)square;
            instance->short_sum=(instance->short_sum-(double)instance->rms_short[short_index][channel])+(double)square;
            instance->rms_long[long_index][channel]=square;
            instance->rms_short[short_index][channel]=square;
        }
        float rms=(float)((double)0.0016155089251697063f*instance->long_sum+1e-10);
        float peak=(float)((double)0.06666667014360428f*instance->short_sum+1e-10);
        float gain=macbook_compressor_gain(instance,rms,peak);
        instance->gains[instance->low_clock%12]=gain;
        instance->low_clock++;
    }
    unsigned int phase=(unsigned int)(instance->clock%31);
    unsigned long long count=instance->low_clock;
    float result=0;
    for (j=0;j<12 && j<count;j++)
        result+=compressor_interpolator[phase+31*j]*instance->gains[(count-1-j)%12];
    return result;
}

void macbook_compressor_process(void *handle,const float *input,float *output,unsigned long frames)
{
    MacbookCompressor *instance=(MacbookCompressor *)handle;
    unsigned long i;
    for (i=0;i<frames;i++) {
        float left=input[2*i],right=input[2*i+1];
        if (!isfinite(left) || !isfinite(right)) {
            macbook_compressor_reset(instance);left=right=0;
        }
        left*=2.5703957080841064f;right*=2.5703957080841064f;
        float gain=detector(instance,left,right);
        unsigned int index=(unsigned int)(instance->clock%222);
        output[2*i]=instance->delay[index][0]*gain;
        output[2*i+1]=instance->delay[index][1]*gain;
        instance->delay[index][0]=left;instance->delay[index][1]=right;
        instance->clock++;
    }
}
