# Working on MacBook Audio

Read README.md and docs/architecture.md before changing the DSP or routing.
The checkout and live installation are independent. Building must write only
to build outputs; do not overwrite the installed plugin or restart audio as a
side effect of a source cleanup or offline check.

Our code is GPL-3.0-only; preserve the ISC notices in third_party/lv2/.
Apple originals, disassembly, extracted tables, impulse responses, recordings
and native bundles stay outside Git, in data/, build/ or .private/.

Run the Python checks for app changes. DSP/build changes need the offline LV2
check and the relevant private reference comparisons in docs/validation.md.
Preserve conversion points and do not enable fast math.

The old workshop is an ignored archive, not the canonical source. Do not use
its migration scripts as a fresh installer. Do not publish or push this repo
without an explicit user request.
