# Provenance and licensing boundaries

Our locally reconstructed C, Python tools and documentation are licensed
GPL-3.0-only. Copyright 2026 Thibaud Poncin. The complete license is in
[`LICENSE`](../LICENSE). The vendored LV2 API header retains its upstream ISC
notice and [`third_party/lv2/LICENSE`](../third_party/lv2/LICENSE).

## Private reference material

The reconstruction studied the High Sierra `DspFuncLib` executable and selected
layout 59, profile 0 for MacBookPro10,2. Supported executable SHA-256:

```text
07abb2dbf7968fd6f46e0242edb00d752503b92815474a163a7603d7066863de
```

Apple machine code runs only in the offline Unicorn harness. The runtime plugin
contains our C implementation plus locally generated numerical tables. It does
not execute Apple code or load Apple's library.

Apple system images, executables, disassembly, relocations, profile XML/JSON,
coefficient headers, impulse responses and lookup data are excluded from Git.
Generated native plugins embed that numerical data and are also excluded.
The project's GPL grant covers our work; it does not relicense Apple materials
or assert permission to redistribute extracted datasets. No such materials are
published by this preparation step.

The research harness contains our emulation glue and version-specific addresses.
It requires private analysis files and validates the executable fingerprint.
The source-input preparation still depends on the original extraction session;
the missing reproducible preparation recipe is documented in
[`data/README.md`](../data/README.md).

## Other development material

The original workshop contained an isolated Ubuntu Tiny C Compiler, official
PipeWire source snippets, a LADSPA header and older wrapper experiments. Those
files are retained only in ignored `.private/workshop/`, with their existing
notices. They are not part of the tracked runtime source or vendored dependencies.
The current build can use a normal system C compiler. Its local compiler fallback
does not download or bundle a compiler in the Git repository.

The custom installed ACP files derive from PulseAudio's LGPL-2.1-or-later
configuration files. They remain installed locally and are not copied into this
repo. A future installer should generate a local variant and preserve upstream
notices, rather than treat those files as our GPL-only code.
