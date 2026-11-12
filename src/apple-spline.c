// SPDX-License-Identifier: GPL-3.0-only
/* Local reconstruction of the profile-0 spline limiter's arithmetic.
 * Original Apple machine code is used only by the offline validation harness.
 * No Apple executable code is included in this plugin.
 */
#include <math.h>
#include <stdlib.h>

enum { ATTACK, RELEASE, MAX_GAIN, MIN_STEP, KNEE_START, CAP,
       ATTACK_OVER_THRESHOLD, HEADROOM, INV_HEADROOM, ATTACK_PLUS_ONE,
       QUADRATIC, THRESHOLD, NCOEFS };

void macbook_spline_coefficients(float *c, unsigned long rate,
    float threshold_db, float attack_control, float release_control,
    float max_gain_db, float max_attack_db, float aggressiveness)
{
    /* Preserve float/double conversion points of Apple's coefficient routine. */
    float attack_rate = 10.0f / attack_control;
    float release_rate = 10.0f / release_control;
    float t = expf(threshold_db * 0.115129254758358f);
    double sample_rate = (double)(long)rate;
    float exponent = (float)(-fabs((double)attack_rate) / sample_rate * 2.302585092994 / 20.0);
    c[ATTACK] = 1.0f - expf(exponent);
    exponent = (float)(fabs((double)release_rate) / sample_rate * 2.302585092994 / 20.0);
    c[RELEASE] = expf(exponent);
    c[MAX_GAIN] = expf((float)((double)max_gain_db * 2.302585092994 / 20.0));
    c[MIN_STEP] = expf((float)(fabs((double)max_attack_db) * -2.302585092994 / 20.0));
    float knee = (1.0f - aggressiveness) * t;
    c[KNEE_START] = knee > 0.0f ? knee : 0.0f;
    c[ATTACK_PLUS_ONE] = c[ATTACK] + 1.0f;
    c[ATTACK_OVER_THRESHOLD] = c[ATTACK] / t;
    c[HEADROOM] = 2.0f - c[KNEE_START] / t;
    c[INV_HEADROOM] = 1.0f / c[HEADROOM];
    c[CAP] = (t + t) - c[KNEE_START];
    float span = c[CAP] - c[KNEE_START];
    c[QUADRATIC] = 1.0f / (span + span);
    c[THRESHOLD] = t;
}

static float step(const float *c, float *state, float input)
{
    union { float f; unsigned int u; } bits, result;
    bits.f = input;
    /* Keep an invalid stream sample from contaminating future audio state. */
    if ((bits.u & 0x7f800000u) == 0x7f800000u) {
        *state = 1.0f;
        return 0.0f;
    }
    float scaled = input * c[INV_HEADROOM];
    bits.f = scaled;
    float detector = fabsf(scaled) * c[ATTACK_OVER_THRESHOLD];
    detector = detector * *state;
    detector = detector > c[ATTACK] ? detector : c[ATTACK];
    float release = *state * c[RELEASE];
    float adjustment = c[ATTACK_PLUS_ONE] - detector;
    adjustment = adjustment > c[MIN_STEP] ? adjustment : c[MIN_STEP];
    float gain = adjustment * release;
    gain = gain < c[MAX_GAIN] ? gain : c[MAX_GAIN];
    float output = scaled * gain;
    output = output * c[HEADROOM];
    float magnitude = fabsf(output);
    magnitude = magnitude < c[CAP] ? magnitude : c[CAP];
    float excess = magnitude - c[KNEE_START];
    excess = excess > 0.0f ? excess : 0.0f;
    float curve = c[QUADRATIC] * excess;
    curve = curve * excess;
    result.f = magnitude - curve;
    result.u = (result.u & 0x7fffffffu) | (bits.u & 0x80000000u);
    *state = gain;
    return result.f;
}

void macbook_spline_process(const float *coefficients, float *states,
    const float *input, float *output, unsigned long frames)
{
    unsigned long i;
    for (i=0; i<frames; i++) {
        float left=input[2*i], right=input[2*i+1];
        output[2*i]=step(coefficients,states,left);
        output[2*i+1]=step(coefficients,states+1,right);
    }
}
