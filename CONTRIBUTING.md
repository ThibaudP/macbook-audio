# Contributing

The first target is MacBookPro10,2, layout 59 profile 0, at 48 kHz. Describe the
machine, codec, routing and software versions when reporting an audio problem.
State whether distortion is visible in the digital meters or heard acoustically.

Run the Python checks and the offline plugin interface check after a build:

```sh
python3 -m compileall -q app tools tests
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tools/build.py
python3 tests/check_plugin.py
```

Installer changes also need `python3 tests/check_staged_runtime.py` on a Linux
host with PipeWire, WirePlumber, systemd-analyze and ACP templates. This uses a
private server and null output; do not replace it with a test against a developer's
working speakers. A separate physical fresh-install trial is still pending.

DSP arithmetic changes also need the private reference comparisons in
[`docs/validation.md`](docs/validation.md). Preserve float/double conversion
points and compile without fast math. Changes to volume or routing need an
isolated PipeWire-output check before a real speaker test.

Do not commit Apple input files, generated coefficients, captured audio,
compiled bundles or private reports. Keep them in `data/`, `build/` or `.private/`.
Do not repoint the running installation at build outputs. Contributions to our
code and documentation use GPL-3.0-only; retain third-party notices.
