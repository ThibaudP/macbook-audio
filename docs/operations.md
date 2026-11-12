# Operation and recovery

## Verified installation

Verified on 2026-11-12 on MacBookPro10,2 / A1425 with CS4206, Ubuntu 26.10,
PipeWire 1.6.8, WirePlumber 0.5.17, EasyEffects 8.2.8. The sample rate is fixed
to 48 kHz. Other models, distributions and hardware replacements are untested.

The installed system is independent from this checkout. Building does not
deploy or restart any service. Do not overwrite a loaded shared object in place.
Update a stopped processor using an atomic file replacement after validating a
candidate bundle; the current repo deliberately has no automatic deployment.

| Installed path | Purpose |
|---|---|
| `~/.local/share/macbook-apple-audio/` | Plugin, `stack.conf`, panel and controller copies |
| `~/.lv2/macbook-apple-stack.lv2/` | LV2 metadata and library symlink |
| `~/.config/macbook-apple-audio/settings.json` | Master enable and fourteen switches |
| `~/.config/systemd/user/macbook-apple-{stack,audio}.service` | DSP host and route controller |
| `~/.config/wireplumber/wireplumber.conf.d/65-macbook-apple-audio.conf` | Hardware profile/rate integration |
| `~/.local/state/macbook-audio/apple-integration/` | Reports and recovery snapshots |
| `$XDG_RUNTIME_DIR/macbook-apple-audio.meters` | Digital meters |

On the original installation `controller.py` dispatches to the full-stack or
legacy controller. The new service template calls `stack-controller.py` directly.
Both installed services are enabled at login. This was checked, but a complete
reboot with the final stack has not yet been observed.

```sh
systemctl --user status macbook-apple-stack macbook-apple-audio
journalctl --user -u macbook-apple-stack -u macbook-apple-audio
macbook-eq status
wpctl status
```

Select **MacBook audio · Apple** as the normal output, or use **Use MacBook audio**
in the panel. In the present setup EasyEffects follows that default and has a
Flat preset. The plugin's playback stream uses EasyEffects' application ID and
DSP media role to avoid being recaptured into EasyEffects.

## Controls

```sh
macbook-audio
macbook-eq on
macbook-eq off
macbook-eq toggle
macbook-eq all-on
macbook-eq stage 3 off
macbook-eq compressor off    # stage 1
macbook-eq output-stage off  # stage 13
```

The digital ceiling with both limiters and output alignment enabled is −2 dBFS
except the right tweeter at approximately −1.969385 dBFS. Digital headroom does
not guarantee clean acoustic output from replacement speakers. Compare stages at
a moderate volume. Turning off tweeter/bass EQ or limiters removes protection.

## Existing recovery commands

These commands exist on the original machine. They depend on its private
backups and are not included as a generic uninstaller in this source repo.

```sh
macbook-eq restore-stack  # previous kept EQ + first-compressor pipeline
macbook-eq restore        # original stereo / EasyEffects setup
```

The first restore uses
`~/.local/state/macbook-audio/apple-integration/before-full-stack/` and stops the
new DSP host. The complete restore then invokes the preserved original rollback.
Keep the backup directory and runtime legacy assets until a replacement recovery
procedure has been tested.

## Fresh installation remains to be packaged

The two service files are templates, not a complete installer. A fresh setup must
also provide the model-specific four-channel ACP profile and mixer mapping,
48 kHz hardware rule, LV2 bundle, generated `stack.conf`, initial settings,
launchers, default-output integration and a pre-install rollback snapshot.
It must remove any earlier inline DSP to avoid processing twice.

The historical migration script assumed our previous partial pipeline and its
backups. It is preserved in `.private/workshop/install-stack.py`; do not run it
on a fresh machine or rerun it against the current full-stack installation.
An installer should verify DMI / codec identity before touching routing, stage
files before changing the output, and provide a tested undo path.
