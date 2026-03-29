# vol-osd

Per-app volume OSD for Wayland (niri + PipeWire). Styled like waybar/Catppuccin Mocha.

```text
╭──────────────────────────────────────────╮
│ 󰕾  Firefox          ████████░░  80%      │  ← focused (mauve)
│   ↪ Built-in Audio Analog Stereo         │  ← its current output
│ 󰖀  Spotify          ██████░░░░  60%      │
│ 󰖀  mpv              ████░░░░░░  40%      │
╰──────────────────────────────────────────╯
```

## System dependencies

```bash
sudo apt install libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0 gir1.2-gtk-4.0
```

> PyGObject (`gi`) links against system GTK4 libraries and cannot be fully
> isolated in a virtualenv. `uv tool install` will install it, but it still
> needs the system headers/libs above to actually work.

## Install

```bash
# one-liner
uv tool install .

# systemd autostart
mkdir -p ~/.config/systemd/user
cp vol-osd.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now vol-osd.service
```

This installs two commands: `vol-osd` (daemon) and `vol-ctl` (controller).

```
we should add

vol-ctl install system d

or whatever instead of manually doing that if manual
```


## niri keybinds

```kdl
binds {
    // knob — adjust focused app
    XF86AudioRaiseVolume { spawn-sh "vol-ctl raise"; }
    XF86AudioLowerVolume { spawn-sh "vol-ctl lower"; }
    XF86AudioMute        { spawn-sh "vol-ctl mute"; }

    // Mod+knob — adjust default sink (master volume)
    Mod+XF86AudioRaiseVolume { spawn-sh "vol-ctl sink-raise"; }
    Mod+XF86AudioLowerVolume { spawn-sh "vol-ctl sink-lower"; }
    Mod+XF86AudioMute        { spawn-sh "vol-ctl sink-mute"; }

    // Mod+Shift+knob — cycle between apps
    Mod+Shift+XF86AudioRaiseVolume { spawn-sh "vol-ctl cycle-next"; }
    Mod+Shift+XF86AudioLowerVolume { spawn-sh "vol-ctl cycle-prev"; }

    // Ctrl+Mod+knob — move focused app to next/prev output device
    Ctrl+Mod+XF86AudioRaiseVolume { spawn-sh "vol-ctl sink-next"; }
    Ctrl+Mod+XF86AudioLowerVolume { spawn-sh "vol-ctl sink-prev"; }
}
```

## vol-ctl commands

| Command       | Effect                                      |
| ------------- | ------------------------------------------ |
| `raise`       | +5% focused app                             |
| `lower`       | -5% focused app                             |
| `mute`        | toggle mute                                 |
| `cycle-next`  | select next stream                          |
| `cycle-prev`  | select previous stream                      |
| `sink-next`   | move focused app to next output device      |
| `sink-prev`   | move focused app to previous output device  |
| `sink-raise`  | +5% default sink (master)                  |
| `sink-lower`  | -5% default sink (master)                  |
| `sink-mute`   | toggle default sink mute                    |
| `show`        | show OSD only                               |
| `start`       | start daemon manually                       |
| `kill`       | stop daemon                                |

## Upgrade

```bash
uv tool upgrade vol-osd
```
