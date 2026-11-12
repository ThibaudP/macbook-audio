# Audio architecture

Scope: MacBookPro10,2, Cirrus CS4206, layout 59 profile 0, stereo input at 48 kHz.

## Selected processing graph

Indices below are the source / CLI indices. The GUI shows index + 1.

| Index | Stage | Role |
|---|---|---|
| 0 | Common EQ | Eight SOS sections per channel; deep-bass rolloff and resonance cuts |
| 1 | Main Mozart compressor | +8.2 dB input, adaptive gating, RMS/peak detection, gain curves, 222-sample audio delay |
| 2 | Loudness and listening volume | Volume-dependent shelves at 700/1400 Hz; 30 ms coefficient and volume smoothing |
| 3 | Two-band Mozart compressor | Detection around 262 Hz; low/high gain control and nine dynamic shelving sections |
| 4 | Common gain | +8 dB left, +8.02554 dB right |
| 5 | Stereo duplication | Feed both speaker pairs; switching this off silences the bass feed |
| 6 | Tweeter EQ A | Eight sections per channel, high-pass near 2 kHz and resonance correction |
| 7 | Bass EQ A | Twelve left / nine right sections, protection near 100 Hz and upper correction |
| 8 | Tweeter EQ B | Eleven sections per channel, upper-mid/treble cuts |
| 9 | Bass EQ B | Sixteen sections per channel, broad midrange cuts |
| 10 | Tweeter balance | −4.5 dB |
| 11 | Bass spline limiter | Independent channel states, −2 dBFS peak ceiling |
| 12 | Tweeter spline limiter | Independent channel states, −2 dBFS peak ceiling |
| 13 | Output alignment | Delay bass by 13 samples; right-tweeter trim +0.030615 dB |

`src/stack-lv2.c` exposes two audio input ports, four output ports, fourteen
stage controls, left/right volume, master enable, headphone mode and output
meters. Internal processing uses interleaved frames; the LV2 wrapper converts
the planar ports in bounded 256-frame blocks without allocating in `run()`.
The core preserves the original float/double conversion points. Lookup tables
sample the original gain curves and loudness coefficients at 0.01 dB intervals.

Stage switches fade over 960 samples / 20 ms. Bypassed stages continue updating
their state. Master bypass retains volume and sends stereo to both pairs.
Headphone bypass sends stereo to the front pair and silences the rear pair.
Disabling protection or EQ stages can allow clipping or strong bass content.

## Hardware channel mapping

| PCM | ACP position | Physical output | ALSA mixer | Codec route |
|---|---|---|---|---|
| 0/1 | FL/FR | Tweeters | `Speaker` | DAC 0x04 → pin 0x0b |
| 2/3 | RL/RR | Bass speakers | `Bass Speaker` | DAC 0x03 → pin 0x0a |
| 0/1, headphone route | FL/FR | Headphones | Headphone route | DAC 0x02 |

The profile is `output:analog-surround-40+input:analog-stereo`. The custom ACP
speaker path maps Speaker to front-left/front-right and Bass Speaker to
rear-left/rear-right. The generic bass/LFE mapping does not express this PCM
layout correctly. They are two stereo feeds, not a mono LFE channel.

## PipeWire and volume

`stack_config.chain_config()` generates a dedicated filter-chain client
configuration. Capture node `macbook_apple_sink` is a stereo virtual output;
playback targets the physical four-channel node. The plugin is hosted in its
own process so a newly introduced LV2 URI does not depend on the main daemon's
cached plugin discovery.

`filter.graph.capture.volumes` maps user volume into `volume_l` / `volume_r`.
The gain is applied at stage 2, before the two-band compressor. It remains active
when loudness or the entire speaker correction is bypassed.

The capture converter's `channelmix.min-volume` and `channelmix.max-volume` are
both 1.0. This is deliberate: on the tested PipeWire version, repeated volume
updates or stream resume could otherwise apply converter gain as well as DSP
gain. Regression checks cover volume once, repeated volume and resume.

The controller holds the physical route's channel volumes at `volumeBase`, its
0 dB point. Master, Speaker, Bass Speaker and PCM were verified at 0 dB. The
custom path retains the earlier +6 dB range, so the physical sink can show about
79% while actually at 0 dB. This is not the listening-volume control.

## Settings and telemetry

The route controller consumes incremental `pw-dump --monitor` updates and polls
settings every 150 ms. It changes plugin controls without replacing sinks or
links. It detects `analog-output-headphones`, clears the older inline graph on
hardware-route changes and maintains neutral hardware gain.

Settings use an advisory file lock and atomic replacement. The panel uses GTK4
and standard PipeWire tools; the daemon needs only Python's standard library.
Default paths are in [operations](operations.md). The Python app honors XDG
config/data/state paths. The installer generates systemd units with explicit XDG
paths and selects the four-channel profile before starting the DSP host.

The plugin maps a 128-byte meter file once during instantiation. It reports
four digital output peaks, limiter gains, common/dual-band gains, clipping,
volume and routing state. This is diagnostic shared memory for the local GUI;
the meters do not measure acoustic distortion or verify speaker health.

The integration follows WirePlumber's [ALSA rules](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/alsa.html)
and [stream-target metadata policy](https://pipewire.pages.freedesktop.org/wireplumber/policies/linking.html).
The volume mapping uses PipeWire's [filter-chain control-port volume interface](https://pipewire.pages.freedesktop.org/pipewire/page_module_filter_chain.html).
