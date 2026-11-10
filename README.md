# sbx

Run a normal program in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox, firejail style. No profiles to write: the system is visible but read-only, your real home is hidden, and the program gets a private home of its own.

```
sbx firefox
sbx --net=none --here python3 script.py
```

`sbx-gui` is an optional GTK4/libadwaita editor for the same settings files.

## Install

Requires Linux, Python 3.8+ and `bubblewrap` (0.8+ for `userns=0`). For the GUI: GTK 4, libadwaita 1.5+ and PyGObject. For `portals`: `xdg-dbus-proxy`. For memory/task/CPU limits: `systemd-run` (systemd).

```
sudo apt install bubblewrap         # or dnf, pacman, ... your distro's equivalent
sudo install -m755 sbx sbx-gui sbx-selftest /usr/local/bin/
sudo install -Dm644 sbx.1 /usr/local/share/man/man1/sbx.1                                  # optional: man sbx
sudo install -Dm644 completions/sbx.bash /usr/local/share/bash-completion/completions/sbx  # optional
sbx doctor                          # checks that everything works on this machine
```

`sbx doctor` also explains the usual surprise on Ubuntu 24.04+, where AppArmor blocks the user namespaces bubblewrap needs.

## What the program sees

| | |
|---|---|
| System (`/usr`, `/etc`, ...) | read-only |
| Your home | hidden. Replaced by `~/.sbx-homes/NAME`, where NAME is the program's name (or `--name`) |
| Other users, `/root`, `/mnt`, `/media`, `/srv`, `/var/tmp`, `/var/log`, mail spools | empty |
| `/tmp`, `/run` | fresh empty tmpfs |
| `/etc/shadow`, `sudoers`, SSH host keys, `/etc/ssl/private`, saved NetworkManager connections | emptied |
| `/etc/machine-id`, boot id | random per program (`~/.config/sbx/ids/NAME`) / random per run |
| Hardware identity | `/sys/devices/virtual/dmi/id` emptied; with `net=0` also `/sys/class/net` (MAC addresses) |
| Environment | cleared; only locale, terminal and desktop variables are kept, plus what you pass with `env=` / `setenv=` |
| Namespaces | all unshared (PID, IPC, UTS, user, cgroup); network shared unless `net=0`; nested user namespaces blocked with `userns=0` |
| System calls | a blocklist: `kexec_*`, `perf_event_open`, `bpf`, `syslog`, `keyctl` and friends, `mbind` and NUMA calls, `open_by_handle_at`, `io_uring_*`, `userfaultfd`, `process_vm_readv/writev`, `kcmp`, `pidfd_getfd`, and all 32-bit/x32 calls |
| Wayland | through a [security context](https://wayland.app/protocols/security-context-v1) when the compositor supports it (no screen capture or input injection), otherwise the full socket with a warning |

The program starts in its private home. Use full paths, or `--here` to share the current folder. If an argument names something that exists on your side but isn't visible inside, sbx says so.

## Usage

```
sbx [options] PROGRAM [args]
sbx preview [options] PROGRAM [args]     print the command instead of running it
sbx show [options] NAME                  the settings that apply, and which file or flag each came from
sbx doctor                               check bubblewrap, helpers, Wayland support, settings files
sbx clean [--yes] NAME                   delete a private home and its machine-id
sbx open FILE                            run a downloaded program with sensible defaults (what "Open with" calls)
sbx desktop NAME                         make a launcher (.desktop file) start the program through sbx
sbx desktop --remove NAME                undo that
sbx desktop --install-opener             add "Run in sbx" to your file manager's Open With (--remove-opener undoes it)
```

Options go before the program name. A program named like a command: `sbx -- clean`. `man sbx` has the full text.

| Option | Effect |
|---|---|
| `--net=none` | no network |
| `--private` | private home is erased on exit |
| `--here` | share the current folder, read-write (not your whole home, not hidden folders) |
| `--whitelist=PATH` | share a file or folder, read-write; repeatable |
| `--read-only=PATH` | share a file or folder, read-only; repeatable |
| `--allow-sensitive` | allow sharing `~/.ssh`, `~/.gnupg`, `~/.aws`, browser profiles and other secret folders (refused otherwise) |
| `--no-wayland` | hide the Wayland socket |
| `--wayland-strict` | no Wayland at all if the compositor can't restrict it |
| `--x11` | share the X11 socket (weaker isolation) |
| `--no-gpu` | hide `/dev/dri` and NVIDIA devices |
| `--no-sound` | hide PipeWire/PulseAudio (this includes the microphone) |
| `--camera` `--alsa` `--input` `--kvm` `--rocm` `--printing` | share `/dev/video*`, `/dev/snd`, `/dev/input`, `/dev/kvm`, `/dev/kfd`, the CUPS socket |
| `--no-theme` | don't show your fonts, icons and GTK/fontconfig settings |
| `--portals` | allow notifications and basic desktop portals via `xdg-dbus-proxy` |
| `--hostname` | program sees hostname `sbx`, also in `/etc/hostname` and `/etc/hosts` |
| `--no-userns` | the program can't create user namespaces |
| `--env=NAME` / `--setenv=NAME=VALUE` | pass a variable through / set one (`HOME`, `USER`, `LOGNAME`, `XDG_RUNTIME_DIR`, `SBX_ACTIVE` are protected) |
| `--allow-syscall=NAME` | turn one of the extra blocked calls back on: `io_uring`, `userfaultfd`, `process_vm_readv`, `process_vm_writev`, `kcmp`, `pidfd_getfd` |
| `--memory=2G` `--tasks=512` `--cpu=200%` | resource limits through `systemd-run --user` |
| `--name=NAME` | use another private home, machine-id and settings name than the program's |
| `--ignore-config` | ignore the settings files for this run |

Shared paths must be inside your home (not the home itself), on `/mnt`, `/media` or `/run/media`, or in `/tmp`. `~/.config/sbx` and `~/.sbx-homes` can never be shared. A folder is mounted before what is inside it, so `whitelist=~/a/b` stays writable even with `read_only=~/a`. Shared folders that don't exist yet in the private home are created as empty mountpoints for the run and removed again afterwards if nothing was written to them.

## Downloaded programs

For a program you downloaded and don't want to set up by hand:

```
sbx desktop --install-opener     # once
```

Now right-click a program file (a game or any other binary) and choose **Open With → Run in sbx**; it is also the default for program files that don't have the execute bit, which is how browsers save downloads. No terminal needed. `sbx open FILE` does the same from a shell.

A program with no settings file of its own (`~/.config/sbx/apps/NAME.conf`) starts with these defaults:

- **Its own folder, read-write**, and it starts there. Games find their data beside the binary and can save next to it. Nothing else of your home is visible. Generic folders (your home, `Downloads`, `Desktop`, `Documents` and the other user folders, including translated names) and hidden folders are never shared this way; a file loose in `~/Downloads` gets only itself, so put a game in its own folder first.
- **Game controllers** (`/dev/input`), plus the usual defaults: network, GPU, sound and Wayland on.
- It runs even without the execute bit, if it is a dynamically linked ELF program (the usual case; static binaries such as many Go programs need `chmod +x`). The bit is set only while the program runs and then put back, so programs that read their own file (.NET single-file apps) work; if the file can't be changed, sbx starts it through the system loader instead. Plain `sbx ./FILE` does the same.
- If it fails right away you get a desktop notification with the reason (a program that exits with an error within 15 seconds counts as failed).

If a settings file exists for that name, it is used exactly as written and none of the defaults above apply. Tighten a program by creating the file, for example `net=0`, or use `sbx-gui`.

Two limits. A file that *has* the execute bit still starts directly when you double-click it; sbx can't intercept that, so use Open With or `chmod -x`. And these defaults trade isolation for convenience (the network is on, and the program's folder is writable); for something you don't trust at all, use `sbx --net=none --private FILE`.

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
env=HTTPS_PROXY
setenv=GTK_THEME=Adwaita:dark
memory=2G
```

Switches (all `0`/`1`), with defaults: `net=1`, `wayland=1`, `wayland_strict=0`, `x11=0`, `gpu=1`, `sound=1`, `portals=0`, `private=0`, `hostname=0`, `userns=1`, `theme=1`, `camera=0`, `alsa=0`, `input=0`, `kvm=0`, `rocm=0`, `printing=0`, `allow_sensitive=0`. Anything else (`net=maybe`) is reported with its file and line number and ignored, instead of silently counting as on or off.

Lists that add up across the two files: `whitelist`, `read_only`, `env`, `setenv`, `allow_syscall`. Limits: `memory`, `tasks`, `cpu`; an empty value in a program's file cancels the default's. Command-line flags beat the files. Flags can only change a setting in the direction shown in the table above (for example, there is no flag to turn the network back on); `--ignore-config` starts from the built-in defaults.

`sbx show NAME` prints the result of all this. `sbx-gui` edits the same files: run `sbx-gui`, `sbx-gui NAME` or `sbx-gui --default`. It asks `sbx schema` which switches exist, so a switch added to sbx appears there at once.

### Whether to turn `userns` off

`userns=0` closes the biggest gap in the syscall filter (nested user namespaces are a common route into kernel bugs), but programs that build their own sandbox need it: Chromium, Electron apps, Flatpak, nested bubblewrap. That is why it defaults to on. Try `--no-userns` on programs that don't sandbox themselves.

## Checking it

```
python3 test_sbx.py              # all tests, as a normal user
sbx ./sbx-selftest               # run inside the sandbox: every row should say PASS
sbx --net=none --no-userns ./sbx-selftest --expect=net0,userns0   # also check what you asked for
```

`test_sbx.py` has three parts. The **snapshot** (`sbx-preview.snap`) holds the exact `bwrap` command for a number of option sets; when a change to `sbx` alters the mount list the test fails with a diff. Review it, then `UPDATE_SNAPSHOTS=1 python3 test_sbx.py` and commit the new snapshot. The **unit tests** cover settings parsing and precedence, path rules, mount ordering, the syscall filter (run through a small BPF interpreter, with numbers checked against the kernel headers when present), the Wayland client (against a fake compositor) and launcher rewriting. The **isolation tests** run real sandboxes against canary files planted in a temporary home: they check every flag, the exit status, cleanup of mountpoints, `clean`, `desktop` and more, and need bubblewrap. A negative control checks that the self-test fails without the sandbox.

`sbx-selftest` tries to read `~/.ssh`, the real `~/.config/sbx` and `~/.sbx-homes`, `/etc/shadow` and `/root`, writes to the system, and calls blocked system calls; each must fail. Pass extra paths to check that they are hidden too.

## Limits

- This is a convenience sandbox, not a security boundary against a hostile program or a kernel exploit. The system-call filter is a blocklist, and nested user namespaces are allowed unless you set `userns=0`.
- **Wayland:** the security context is implemented against the protocol and tested against a fake compositor; it has not been tried here on a real Mutter, KWin or wlroots session. Check with `sbx doctor`. Without compositor support the program gets the full socket (a warning says so), which on some compositors includes screen capture and input injection; `wayland_strict=1` turns Wayland off in that case instead.
- With `net=1`, the abstract Unix socket namespace is shared with the host, so `x11=0` does not fully keep a program off an X server that listens on an abstract socket, and `/sys/class/net` still lists your network cards. Only `net=0` closes both.
- `sound=1` gives access to the PipeWire/PulseAudio socket, which includes microphone and, depending on setup, camera and screen-capture nodes.
- `portals=1` lets the program ask your desktop to open URLs and files and show file choosers. Leave it off for programs you don't trust. **The file chooser is untested:** the portal sees the program as an ordinary host application, so it hands back real host paths that may not exist inside the private home. Choosing a file in a shared folder works the same as typing its path; elsewhere it probably won't.
- Sharing `~/.ssh`, `~/.gnupg`, `~/.aws` and similar needs `--allow-sensitive`. The list covers common cases, not every program that keeps secrets.
- Not for NixOS or Guix in general: the usual `/run/current-system` links are recreated, but nothing here has been tried on them.
- The MAC-address, DMI and `/var/log` hiding is a privacy measure against fingerprinting by honest programs, not a guarantee; other files under `/sys` and `/proc` still describe the machine.
- 32-bit programs (Wine, Steam, old games) fail because 32-bit system calls are blocked.
- `--new-session` is on, so terminal programs have no `/dev/tty` and no job control. Prompts that read from the terminal directly (`ssh`, `sudo`, `git` passwords) won't work.
- Sharing a single file read-write can break programs that save by renaming over it. Share the folder instead.
- Each program name has one private home, so `sbx python3 a.py` and `sbx python3 b.py` share it; use `--name` to separate them.
- Programs on another drive (`/mnt`, `/media`, `/run/media`) can't be started; copy them elsewhere first.
- Not for root; `sbx` refuses to run as root or inside another `sbx` sandbox.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
