# Validation

Measured on 2026-11-12, MacBookPro10,2, 48 kHz. Numerical results below were
rechecked against the reorganized source and local build during repository
preparation. Reports stay in ignored `build/reports/`.

## Offline checks

| Check | Coverage | Observed maximum absolute error |
|---|---|---|
| Main compressor | 484,096 audio frames; silence, tones, noise, level steps, varying block sizes | 9.54e-7 |
| Main gain controller | 18,432 gain steps | 4.77e-7 |
| Loudness coefficients | 200 arbitrary listening levels | 1.32e-6 |
| Loudness scalar loop | Five volume changes with coefficient slew | 4.66e-10 |
| Two-band compressor | 292,096 frames; silence, multitone, level-step noise | 7.16e-7 |
| Spline limiter | 21 cases, 357,312 frames, several rates, hot signals and variable blocks | Sample-exact against the optimized original loop |
| Selected full graph | 48,000 stereo input frames, four output channels | 2.66e-6, RMS 6.30e-7 |

The limiter arithmetic has separate tests at 44.1/48/96 kHz; the **full plugin
accepts only 48 kHz**. The full-graph oracle combines emulated original common
dynamics, extracted EQ impulse responses, and the independently validated
limiter/output model. It is not one unmodified macOS driver running end to end.
Emulation replaces kernel allocation, libm and Accelerate primitives with host
operations. These tests establish numerical agreement for the tested cases.

The LV2 interface check covers all fourteen controls, unsupported-rate rejection,
volume-preserving bypass, asymmetric left/right volume, headphone rear silence
and overload peak ceilings. Its meter mapping is isolated from the running GUI.
The whole-graph test also measures CPU use: about 15% of one core on this Mac.

```sh
python3 -m compileall -q app tools tests
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tools/build.py
python3 tests/check_plugin.py
```

With the private inputs described in [`data/README.md`](../data/README.md):

```sh
export APPLE_INPUT_DIR=/path/to/private-extraction
export APPLE_ANALYSIS_DIR=/path/to/private-analysis
python3 tools/research/validate-compressor.py
python3 tools/research/validate-remaining.py
python3 tools/research/validate-limiter.py
python3 tools/research/validate-stack.py
```

Set `MACBOOK_DATA_DIR` for headers and impulse responses outside `data/`, and
`MACBOOK_PLUGIN` to check another candidate library. None of these commands
installs a library, switches routes or restarts the active audio services.

## PipeWire integration evidence

Before repository cleanup, isolated four-channel virtual-output tests checked:

- listening gain applied once, including repeated volume requests and resume;
- front-only headphone bypass;
- overload peak limits with the complete graph;
- all fourteen controls changing without replacing the sink or host process.

Installation checks on the real machine preserved the main PipeWire, Pulse and
WirePlumber processes, EasyEffects sink and existing application streams. Explicit
headphone/speaker route changes passed; hardware gains were verified at 0 dB.
A final silent-playback check confirmed four-channel PCM at 48 kHz, no duplicate
legacy DSP and controls matching saved settings.

Those original reports are kept locally under
`~/.local/state/macbook-audio/apple-integration/`:
`stack-native-validation.json`, `stack-live-validation.json` and
`stack-final-check.json`. The historical integration test scripts remain in
`.private/workshop/`. They assume that machine's previous installation and are
not part of the portable offline suite.

No acoustic calibration, physical jack insertion or full reboot with the final
stack has been verified in this session. Digital peak limits do not establish
the mechanical limits or frequency response of replacement speakers.
