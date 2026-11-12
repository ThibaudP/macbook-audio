# Local coefficient data

This directory is excluded from Git except for this file. A build requires:

- `compressor-data.h`: initial compressor state, detector coefficients,
  interpolation kernel and attack/release lookup curves;
- `stack-data.h`: dual-band state/curves/filters, loudness tables and branch EQs.

These are extracted or derived from the selected Apple profile. They are kept
separate from our GPL code. The build does not download Apple software or data.

## Existing local headers

```sh
python3 tools/import_data.py /path/to/generated-headers
python3 tools/build.py
```

On the original development machine the previous copies are preserved in
`.private/workshop/`. Their import is reproducible with:

```sh
python3 tools/import_data.py .private/workshop
```

## Offline regeneration

The research harness uses NumPy and Unicorn. Provide them in a local Python
environment. It also accepts the original research environment under
`$APPLE_INPUT_DIR/tools/python/` if present.

Required private inputs:

| Location | Required files |
|---|---|
| `$APPLE_INPUT_DIR` | `DspFuncLib`, `original-layout59/selected-eq.json` |
| `$APPLE_ANALYSIS_DIR` | `apple-compressor-relocations.txt`, `apple-compressor-setters.asm`, `apple-dualband-wrapper.asm` |

The analysis files are relocation / demangled disassembly output from the local
extraction session. They contain Apple material and are not shipped here.
Generating those files from an original system image is not yet packaged as a
standalone workflow. This is a known reproducibility gap for a new contributor.
The harness rejects binaries whose SHA-256 does not match the supported source.

```sh
export APPLE_INPUT_DIR=/path/to/private-extraction
export APPLE_ANALYSIS_DIR=/path/to/private-analysis
python3 tools/research/build-compressor-data.py
python3 tools/research/build-stack-data.py
python3 tools/build.py
```

Defaults: `APPLE_INPUT_DIR=data/apple`, `APPLE_ANALYSIS_DIR=.private/analysis`,
`MACBOOK_DATA_DIR=data`. Data generators write into `MACBOOK_DATA_DIR`.

The whole-graph reference comparison additionally uses six private float32 WAV
impulse responses: `common-{0,1}.wav`, `tweeter-branch-{0,1}.wav` and
`bass-branch-{0,1}.wav`. They were exported during the original reconstruction;
keep them in `MACBOOK_DATA_DIR`. Their export workflow remains in the private
workshop. The ordinary plugin interface test needs only the compiled plugin.
