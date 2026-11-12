# Installation, operation and recovery

## Supported installation

Target: MacBookPro10,2 / A1425 with CS4206 codec `0x10134206`, subsystem
`0x106b3b00`, at 48 kHz. The reference machine runs Ubuntu 26.10, PipeWire 1.6.8,
WirePlumber 0.5.17 and EasyEffects 8.2.8, verified 2026-11-12. Other hardware is
not supported by the installer.

Build the plugin first using the [data preparation workflow](extraction.md).
Runtime prerequisites: Python 3.10+, PipeWire 1.6+, WirePlumber 0.5+, systemd
user services, `pw-dump`, `pw-cli`, `pw-metadata`, `wpctl`, `alsactl`, ACP templates
at `/usr/share/alsa-card-profile/mixer`, and GTK4 Python bindings (`python3-gi`
and `gir1.2-gtk-4.0` on Ubuntu). NumPy/Unicorn are only for offline research.
The installer does not install system packages. EasyEffects is optional.

Run as the logged-in desktop user, without sudo, in the standard PipeWire
session. An existing unmanaged MacBook Audio installation or conflicting files
are refused. This is a fresh installer, not a migration of the original workshop.

```sh
python3 tools/install.py plan
python3 tools/install.py stage --output build/install-stage
python3 tools/install.py install
```

`plan` lists files and conflicts. `stage` writes reviewable files and `plan.json`
to a new directory; it does not touch installed paths, routing or services.
Its destination must not already exist. XDG config/data/state paths are honored;
the systemd user manager must use the same XDG configuration directory. ACP
mixer paths cannot contain whitespace, quotes or backslashes.

`install` verifies the machine and tools, saves ALSA state, output profile,
routes, default output and affected stream targets, backs up managed files,
and arms a 90-second recovery timer. It deploys a locally generated ACP profile
and mixer path, preserving the upstream LGPL notices. It then restarts
WirePlumber, selects the four-channel profile and starts the DSP/controller.
The main PipeWire server is not restarted. Existing stream nodes are retained;
only pre-existing feeds to the internal hardware are moved into the processor.
The processor's own playback is never included in that move list.

The virtual output starts muted for handoff, receives a 50% listening-volume
setting, becomes the default, then unmutes when the controller is ready. The
normal startup configuration is written before committing the transaction.
The DSP service selects the four-channel profile on subsequent startups.

## Installed files and controls

| Path | Purpose |
|---|---|
| `~/.local/share/macbook-apple-audio/` | `stack.conf`, panel, controller, setup/recovery code and licenses |
| `~/.lv2/macbook-apple-stack.lv2/` | LV2 metadata and shared library |
| `~/.config/macbook-apple-audio/settings.json` | Master enable and fourteen switches |
| `~/.config/systemd/user/macbook-apple-{stack,audio}.service` | DSP host and controller |
| `~/.config/wireplumber/wireplumber.conf.d/65-macbook-apple-audio.conf` | Model-specific profile/rate integration |
| `~/.local/state/macbook-audio/install/` | Transaction manifest, original state and standalone recovery copies |
| `~/.local/state/macbook-audio/apple-integration/` | Controller status and earlier research reports |
| `$XDG_RUNTIME_DIR/macbook-apple-audio.meters` | Four digital output meters |

```sh
macbook-audio
macbook-eq status
macbook-eq on
macbook-eq off
macbook-eq stage 3 off
macbook-eq all-on
systemctl --user status macbook-apple-stack macbook-apple-audio
journalctl --user -u macbook-apple-stack -u macbook-apple-audio
```

Use **MacBook audio · Apple** for listening volume. Physical mixer gains stay at
0 dB. EasyEffects can sit upstream; use a Flat preset if the goal is the selected
Apple graph alone. The Processing switch bypasses our speaker correction, while
EasyEffects' bypass affects only upstream effects. The DSP playback identifies
itself to EasyEffects as an internal DSP stream to avoid recapture.

Both services are enabled at login. Physical jack insertion and a complete reboot
with the final installation remain to be checked. The peak ceilings are -2 dBFS,
except the aligned right tweeter at about -1.969385 dBFS. Digital headroom does
not establish the mechanical limits of replacement speakers. Disabling EQ or
limiters can allow clipping or strong low-frequency content.

## Uninstall and recovery

```sh
macbook-audio-setup status
macbook-audio-setup uninstall
```

Uninstall stops/disables our services, restores managed files, reloads
WirePlumber and restores the saved profile, routes, default output and ALSA
controls. Surviving stream targets use object serials to avoid changing a new
application that reused an old numeric ID. Numeric target IDs are translated to
the recreated output. Listening settings are retained for a later reinstall.
Backups and the manifest also remain available.

Edited installed files are left untouched unless `uninstall --force` is used.
The forced path saves those edits in the transaction backup before restoring.
Uninstall is idempotent. Source-checkout equivalents are:

```sh
python3 tools/install.py uninstall
python3 tools/install.py recover
```

An activation error rolls back automatically. A transient user timer retries
recovery after process interruption in the same login session. `recover` leaves
a committed installation alone and restores an unfinished transaction. If
recovery itself fails, the manifest stays in `restoring` and the command can be
retried. The error prints the standalone backup recovery command, which works
without the checkout or installed runtime files.

The timer does not survive reboot. After a power loss or logout during an
unfinished installation, run `recover` in the desktop session with PipeWire and
WirePlumber running. The manifest is in
`~/.local/state/macbook-audio/install/manifest.json` (or the matching XDG state
directory). Backups contain private system state and remain outside Git.

## Original development installation

The working installation predates this fresh installer and is deliberately left
unchanged during its development. Its runtime has independent copies, a library
symlink, and `controller.py` dispatching between legacy and full-stack backends.
Its original recovery commands remain:

```sh
macbook-eq restore-stack  # previous kept EQ + first-compressor pipeline
macbook-eq restore        # original stereo / EasyEffects setup
```

These depend on its private backups under
`~/.local/state/macbook-audio/apple-integration/`, including `before-full-stack/`.
The fresh installer's uninstall command does not manage this older installation.
The historical migration script in `.private/workshop/install-stack.py` is not
an installer for a new machine and must not be replayed against the current stack.

## Validation boundary

File install/uninstall, failed startup, interrupted installation, modified-file
preservation and recovery retry are exercised against temporary directories and
a fake host. Staged production files also run on a private PipeWire server and
WirePlumber policy with a null four-channel output; the actual controller receives
a synthetic ALSA Device record. Live controls, native links and generated systemd
unit syntax pass. No physical ALSA device or real systemd audio service is used by
that test. A real fresh-install/uninstall trial remains before a public release.
