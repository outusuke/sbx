# sbx

Run a normal program in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox, firejail style. No profiles to write: the system is visible but read-only, your real home is hidden, and the program gets a private home of its own.

```
sbx firefox
sbx --net=none --here python3 script.py
```

`sbx-gui` is an optional GTK4/libadwaita editor for the same settings files.

## Install

Requires Linux, Python 3 and `bubblewrap`. For the GUI: GTK 4, libadwaita 1.5+ and PyGObject. For `portals`: `xdg-dbus-proxy`.

```
sudo dnf install bubblewrap                  # or your distro's equivalent
sudo install -m755 sbx sbx-gui /usr/local/bin/
```

## What the program sees

| | |
|---|---|
| System (`/usr`, `/etc`, ...) | read-only |
| Your home | hidden. Replaced by `~/.sbx-homes/NAME`, where NAME is the program's name |
| Other users, `/root`, `/mnt`, `/media`, `/srv`, `/var/tmp` | empty |
| `/tmp`, `/run` | fresh empty tmpfs |
| `/etc/shadow`, `sudoers`, SSH host keys, `/etc/ssl/private`, saved NetworkManager connections | emptied |
| `/etc/machine-id` | random per program (`~/.config/sbx/ids/NAME`) |
| Environment | cleared; only locale, terminal and desktop variables are kept |
| Namespaces | all unshared (PID, IPC, UTS, user, cgroup); network shared unless `net=0` |
| System calls | a blocklist: `kexec_*`, `perf_event_open`, `bpf`, `syslog`, `keyctl` and friends, `mbind` and NUMA calls, `open_by_handle_at`, and all 32-bit/x32 calls |

The program starts in its private home. Use full paths, or `--here` to share the current folder.

## Usage

```
sbx [options] PROGRAM [args]
sbx preview [options] PROGRAM [args]     print the bwrap command instead of running it
```

Options go before the program name.

| Option | Effect |
|---|---|
| `--net=none` | no network |
| `--private` | private home is erased on exit |
| `--here` | share the current folder, read-write (not your whole home, not hidden folders) |
| `--whitelist=PATH` | share a file or folder, read-write; repeatable |
| `--read-only=PATH` | share a file or folder, read-only; repeatable |
| `--no-wayland` | hide the Wayland socket |
| `--x11` | share the X11 socket (weaker isolation) |
| `--no-gpu` | hide `/dev/dri` and NVIDIA devices |
| `--no-sound` | hide PipeWire/PulseAudio (this includes the microphone) |
| `--portals` | allow notifications and basic desktop portals via `xdg-dbus-proxy` |
| `--hostname` | program sees hostname `sbx` |

Shared paths must be inside your home (not the home itself), on `/mnt`, `/media` or `/run/media`, or in `/tmp`. `~/.config/sbx` and `~/.sbx-homes` can never be shared.

## Settings

Plain `key=value` files, `#` starts a comment:

- `~/.config/sbx/default.conf` applies to every program
- `~/.config/sbx/apps/NAME.conf` applies to one program and overrides the default

```
net=0
gpu=0
portals=1
whitelist=~/Projects
read_only=~/.gitconfig
```

Switches (all `0`/`1`), with defaults: `net=1`, `wayland=1`, `x11=0`, `gpu=1`, `sound=1`, `portals=0`, `private=0`, `hostname=0`. `whitelist` and `read_only` add up across the two files. Command-line flags beat the files. Flags can only change a setting in the direction shown in the table above (for example, there is no flag to turn the network back on).

`sbx-gui` edits these same files. Run `sbx-gui`, `sbx-gui NAME` or `sbx-gui --default`.

## Checking it

```
sbx ./sbx-selftest               # run inside the sandbox: every row should say PASS
python3 test_sbx.py              # runs the self-test against planted canary files, plus a preview snapshot
```

`sbx-selftest` tries to read `~/.ssh`, the real `~/.config/sbx` and `~/.sbx-homes`, `/etc/shadow` and `/root`, writes to the system, and calls blocked system calls (`bpf`, `perf_event_open`, `mbind`, `keyctl`); each must fail. Pass extra paths to check that they are hidden too. Run without `sbx` it must fail, and `test_sbx.py` checks that it does.

`sbx-preview.snap` holds the exact `bwrap` command for a few option sets. When a change to `sbx` alters the mount list, the test fails with a diff. Review it, then `UPDATE_SNAPSHOTS=1 python3 test_sbx.py` and commit the new snapshot.

## Limits

- This is a convenience sandbox, not a security boundary against a hostile program or a kernel exploit. The system-call filter is a blocklist, and nested user namespaces are still allowed.
- With `net=1`, the abstract Unix socket namespace is shared with the host, so `x11=0` does not fully keep a program off an X server that listens on an abstract socket. Only `net=0` closes that.
- `sound=1` gives access to the PipeWire/PulseAudio socket, which includes microphone and, depending on setup, camera and screen-capture nodes.
- `portals=1` lets the program ask your desktop to open URLs and files and show file choosers. Leave it off for programs you don't trust.
- 32-bit programs (Wine, Steam, old games) fail because 32-bit system calls are blocked.
- `--new-session` is on, so terminal programs have no `/dev/tty` and no job control. Prompts that read from the terminal directly (`ssh`, `sudo`, `git` passwords) won't work.
- Sharing a single file read-write can break programs that save by renaming over it. Share the folder instead.
- Each program name has one private home, so `sbx python3 a.py` and `sbx python3 b.py` share it.
- Programs on another drive (`/mnt`, `/media`, `/run/media`) can't be started; copy them elsewhere first.
- Not for root; `sbx` refuses to run as root or inside another `sbx` sandbox.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
