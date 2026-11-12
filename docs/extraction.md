# Reproduce the private data

The preparation workflow takes two local original files and generates everything
needed to build and run the numerical reference suite. It does not download,
install or modify Apple software. No disassembly from the earlier workshop is
required.

## Inputs

Use the supported High Sierra reference `DspFuncLib` and layout 59 resource.
Fingerprints are checked before emulation or publication of outputs:

| Input | SHA-256 |
|---|---|
| Thin x86-64 `DspFuncLib` executable | `07abb2dbf7968fd6f46e0242edb00d752503b92815474a163a7603d7066863de` |
| Uncompressed original layout 59 XML | `5ed766c138e65d4231a60086317ed4b8bef1b212c0d5b59201f98387cae89c26` |

For the original compressed `layout59.xml.zlib`, the compressed hash is
`6debf60ebced58faffe2f4e76ce2425499e7ed519bf1c66ff872e40026db9ccf`.
The workflow accepts the compressed resource or its original decompressed bytes.
Reformatted XML will not match the fingerprint. Other binaries, layouts and
profiles are rejected because the oracle uses version-specific addresses.

In a matching local `AppleHDA.kext`, the expected paths are:

```text
Contents/PlugIns/DspFuncLib.kext/Contents/MacOS/DspFuncLib
Contents/Resources/layout59.xml.zlib
```

The original research used High Sierra 10.13.6 files archived by
[Dortania at commit b0a1fa26](https://github.com/dortania/PatcherSupportPkg/tree/b0a1fa26db9ba6d64ac3f8c42174176e5a8a7e0f/Universal-Binaries/10.13.6/System/Library/Extensions/AppleHDA.kext/Contents).
That identifies the bytes studied; it does not change their licensing. Supply
your own local inputs. The preparation tool deliberately has no download mode.

## Prepare, build and compare

Research dependencies are pinned in `requirements-research.txt` and require
Python 3.12+. They are not runtime dependencies. Python 3.14, NumPy 2.5.4 and
Unicorn 2.1.4 were used for the reproducibility check.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-research.txt
.venv/bin/python tools/prepare_data.py --applehda /path/to/AppleHDA.kext
```

Or use explicitly located files:

```sh
.venv/bin/python tools/prepare_data.py --dsp /path/to/DspFuncLib --layout /path/to/layout59.xml.zlib
python3 tools/build.py
python3 tests/check_plugin.py
```

The output defaults to ignored `data/`. Preparation validates inputs, decodes
profile 0, reads the binary's symbol and external relocation tables, samples the
original dynamics/loudness routines in Unicorn, and renders the static reference
responses. Temporary Apple inputs stay inside the output data tree. Results are
published after generation succeeds, with `provenance.json` written last.

```sh
APPLE_INPUT_DIR="$PWD/data/apple" .venv/bin/python tools/research/validate-stack.py
APPLE_INPUT_DIR="$PWD/data/apple" .venv/bin/python tools/research/validate-compressor.py
APPLE_INPUT_DIR="$PWD/data/apple" .venv/bin/python tools/research/validate-remaining.py
APPLE_INPUT_DIR="$PWD/data/apple" .venv/bin/python tools/research/validate-limiter.py
```

Use `--output build/reproduced-data` to preserve an existing data set. Pass that
directory to `tools/build.py --data-dir ...`; for the reference suite set
`APPLE_INPUT_DIR` to its `apple/` child, `MACBOOK_DATA_DIR` to the data directory,
and `MACBOOK_PLUGIN` to the newly built library.

## Reproducibility evidence

On 2026-11-12, regeneration from only the two original input files reproduced
`compressor-data.h`, `stack-data.h` and all six reference WAVs **byte-for-byte**
against the earlier research artifacts. A new plugin built from the regenerated
headers passed the LV2 interface checks and the complete-graph reference test:
maximum sample error 2.66e-6, RMS 6.30e-7.

The whole graph is fixed to layout 59/profile 0 at 48 kHz. The workflow does not
probe speaker vendor GPIOs or infer a different profile for replacement speakers.
It reproduces the selection already studied, without requiring the extraction
machine's firmware dump or live ALSA pin map.
