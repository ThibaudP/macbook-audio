# Private build and reference data

Everything here except this README is ignored by Git. No Apple input is downloaded
by the tools. Run the complete [extraction workflow](../docs/extraction.md):

```sh
.venv/bin/python tools/prepare_data.py --dsp /path/to/DspFuncLib --layout /path/to/layout59.xml.zlib
```

This regenerates both build headers, selected layout data and six reference
responses. It reads Mach-O symbols/relocations directly; no LLVM or saved
disassembly is needed. The input fingerprints must match the supported original.

| Output | Purpose |
|---|---|
| `compressor-data.h` | Main compressor state, detector coefficients, interpolation and gain curves |
| `stack-data.h` | Dual-band state/curves/filters, loudness tables and branch EQ |
| `apple/DspFuncLib` | Original executable, used only by the offline oracle |
| `apple/layout59.input` | Original supplied layout resource |
| `apple/original-layout59/selected-eq.json` | Selected fourteen-stage profile at 48 kHz |
| `common-{0,1}.wav` | Common EQ reference responses |
| `tweeter-branch-{0,1}.wav`, `bass-branch-{0,1}.wav` | Branch EQ reference responses |
| `provenance.json` | Input/output hashes, profile, rate and research package versions |

The runtime build needs only the two headers. An existing locally generated pair
can still be imported with `python3 tools/import_data.py /path/to/headers`, then
compiled with `python3 tools/build.py`. Importing headers alone does not supply
the original executable or responses needed for reference comparisons.

The original development headers remain in `.private/workshop/`. They are an
archive, not a dependency of the preparation workflow. Generated data remains
separate from our GPL code and is not included in source exports.
