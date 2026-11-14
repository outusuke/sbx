#| 80_cli: Command-line flags, usage text, option parsing, `sbx schema` for the GUI.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- command line
FLAGS = {"--net=none": ("net", False), "--net-private": ("net_private", True), "--private": ("private", True), "--here": ("here", True),
         "--no-wayland": ("wayland", False), "--wayland-strict": ("wayland_strict", True), "--x11": ("x11", True),
         "--no-gpu": ("gpu", False), "--gpu-render-only": ("gpu_render_only", True), "--no-sound": ("sound", False), "--portals": ("portals", True),
         "--tray": ("tray", True), "--hostname": ("hostname", True), "--privacy": ("privacy", True), "--no-userns": ("userns", False), "--no-theme": ("theme", False),
         "--camera": ("camera", True), "--alsa": ("alsa", True), "--input": ("input", True), "--kvm": ("kvm", True),
         "--rocm": ("rocm", True), "--printing": ("printing", True), "--ssh-agent": ("ssh_agent", True), "--allow-sensitive": ("allow_sensitive", True),
         "--compat": ("compat", True), "--ignore-config": ("ignore_config", True), "--rebind": ("rebind", True),
         **{"--" + k.replace("_", "-"): (k, True) for k in PORTAL_EXTRA}}
VALUE_FLAGS = {"--whitelist": "whitelist", "--read-only": "read_only", "--env": "env", "--setenv": "setenv",
               "--allow-syscall": "allow_syscall", "--memory": "memory", "--tasks": "tasks", "--cpu": "cpu", "--name": "name",
               "--loader": "loader"}
COMMANDS = ("preview", "show", "doctor", "clean", "reset", "link", "desktop", "open", "schema", "list", "stop", "rebind")

USAGE = ("sbx [options] PROGRAM [args]           run a program in the sandbox\n"
         "sbx preview [options] PROGRAM [args]   print the command instead of running it\n"
         "sbx show [options] NAME                the settings that apply to a program, and where each comes from\n"
         "sbx doctor                             check that everything sbx needs works here\n"
         "sbx list [--all]                       the programs you set up, where each comes from, and which are running (--all adds those only run from the terminal)\n"
         "sbx rebind NAME | --dismiss NAME       decide a program that moved or whose name another file wants: accept it as the same program, or drop the question\n"
         "sbx stop NAME                          stop a running program\n"
         "sbx clean [--yes] NAME | --unused      delete a program's private home and machine-id (not while it runs); --unused: every nearly empty home nobody set up\n"
         "sbx reset [--yes] NAME|--default|--all  delete settings so the built-in defaults apply again (data stays)\n"
         "sbx link NAME [--desktop] | --remove NAME [--desktop]  make typing NAME in a terminal start it through sbx (a link in ~/.local/bin); --desktop covers its app-menu launchers too\n"
         "sbx open FILE                          run a downloaded program with sensible defaults (for \"Open with\")\n"
         "sbx desktop NAME | --remove NAME       make a launcher start the program through sbx (NAME = its id, or the program it starts)\n"
         "sbx desktop --install-opener           add \"Run in sbx\" to your file manager's Open With (--remove-opener undoes it)\n"
         "sbx desktop --install-menu             add sbx-gui to your app menu (--remove-menu undoes it)\n"
         "options (before the program; most also exist as a setting, e.g. net=0, x11=1):\n"
         "  --net=none    no network                   --no-wayland   hide the Wayland socket\n"
         "  --net-private internet, but not this computer's own services (localhost); needs pasta\n"
         "  --private     private home erased on exit  --x11          share the X11 socket (weaker isolation)\n"
         "  --here        share the current folder     --no-gpu       hide /dev/dri and NVIDIA devices\n"
         "  --gpu-render-only      of /dev/dri share only the render nodes (renderD*), not the display card nodes\n"
         "  --no-sound    hide PipeWire/PulseAudio     --portals      notifications and basic portals (xdg-dbus-proxy)\n"
         "  --hostname    hostname 'sbx' inside        --no-userns    the program can't create user namespaces\n"
         "  --privacy     /etc/passwd without your real name or the other accounts\n"
         "  --tray        tray icon and media keys (xdg-dbus-proxy)\n"
         "  --camera --alsa --input --kvm --rocm --printing --ssh-agent     share that device or socket\n"
         "  --no-theme    don't show your fonts, icons, GTK settings, title-bar buttons and cursor\n"
         "  --portal-screenshot --portal-screencast --portal-location --portal-shortcuts --portal-remote-desktop\n"
         "  --portal-input-capture --portal-wallpaper --portal-launcher --portal-secret    one more desktop portal each (needs --portals)\n"
         "  --compat      pass graphics/toolkit variables (WGPU_*, MESA_*, SDL_*, QT_QPA_*...) that are set; nothing else\n"
         "  --wayland-strict       no Wayland at all if the compositor can't restrict it\n"
         "  --whitelist=PATH       share a file or folder (read-write); can be repeated\n"
         "  --read-only=PATH       share a file or folder (read-only); can be repeated\n"
         "  --allow-sensitive      allow sharing ~/.ssh, ~/.gnupg and other secret folders\n"
         "  --env=NAME             pass a variable through     --setenv=NAME=VALUE   set a variable\n"
         "  --allow-syscall=NAME   turn a blocked system call back on (io_uring, userfaultfd, kcmp, ...)\n"
         "  --memory=2G --tasks=512 --cpu=200%   limits (systemd-run --user)\n"
         "  --name=NAME            use another private home and settings name than the program's\n"
         "  --ignore-config        ignore the settings files for this run\n"
         "  --rebind               this file is the same program as before, just moved (only needed when sbx can't tell; see below)\n"
         "notes: the program starts in its private home, so use full paths or --here for files.\n"
         "  Flags only turn things off (or on, for the extras); settings files set the rest.\n"
         "  net=1 also shares abstract Unix sockets (some X servers); only net=0 or --net-private closes that.\n"
         "  32-bit programs are blocked by the syscall filter. Terminal programs lose /dev/tty (--new-session).\n"
         "  A program named like a command (clean, show...): sbx -- clean\n"
         "  A name belongs to the file that first used it. The same file moved or copied is recognised by its content; a different file\n"
         "  under that name is held for your decision (sbx rebind NAME, or sbx-gui) or refused (--name=OTHER).\n"
         "settings: ~/.config/sbx/default.conf and ~/.config/sbx/apps/NAME.conf with lines like\n"
         "  net=0   gpu=0   portals=1   whitelist=~/Projects   read_only=~/.gitconfig   env=HTTPS_PROXY   memory=2G")


def parse_options(a):
    """Leading options -> (opts, the rest). Dies on anything it doesn't know."""
    a, o = list(a), {}
    while a and a[0].startswith("-"):
        f = a.pop(0)
        if f == "--": break
        k, eq, v = f.partition("=")
        if f in FLAGS: o[FLAGS[f][0]] = FLAGS[f][1]
        elif eq and k in VALUE_FLAGS:
            key = VALUE_FLAGS[k]
            if key in LIMITS:
                if not re.fullmatch(LIMITS[key], v): die(f"{k}: '{v}' is not a valid value")
                o[key] = v
            elif key in ("name", "loader"): o[key] = v
            else:
                if key == "env" and not env_name_ok(v): die(f"{k}: '{v}' is not a variable name")
                if key == "setenv" and ("=" not in v or not env_name_ok(v.partition("=")[0])): die(f"{k}: use NAME=VALUE")
                if key == "allow_syscall" and not syscall_ok(v): die(f"{k}: '{v}' can't be allowed; choose from {', '.join(sorted(EXTRA_SYSCALLS))}, io_uring")
                o.setdefault(key, []).append(v)
        elif k in VALUE_FLAGS: die(f"{k} needs a value: {k}=...")
        elif eq and k in FLAGS: die(f"{k} doesn't take a value")
        else:
            near = difflib.get_close_matches(f, [x for x in FLAGS] + [x + "=" for x in VALUE_FLAGS], n=1, cutoff=0.6)
            die(f"unknown option {f}" + (f" (did you mean {near[0]}?)" if near else "") + "\nsbx --help lists the options.")
    return o, a


def schema():
    """What sbx-gui needs to know, so the two can't drift apart."""
    return {"version": VERSION, "switches": SCALARS, "lists": list(LISTS), "limits": {k: v for k, v in LIMITS.items()},
            "sensitive": SENSITIVE, "outside": list(OUTSIDE), "syscalls": sorted(EXTRA_SYSCALLS) + ["io_uring"]}
