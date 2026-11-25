#| 80_cli: Command-line flags, usage text, option parsing, `sbx schema` for the GUI.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- command line
FLAGS = {"--net=none": ("net", False), "--net-private": ("net_private", True), "--private": ("private", True), "--here": ("here", True),
         "--no-wayland": ("wayland", False), "--wayland-strict": ("wayland_strict", True), "--x11": ("x11", True),
         "--no-gpu": ("gpu", False), "--gpu-render-only": ("gpu_render_only", True), "--no-sound": ("sound", False), "--portals": ("portals", True),
         "--tray": ("tray", True), "--mpris": ("mpris", True), "--a11y": ("a11y", True), "--gnome-shell": ("gnome_shell", True), "--gnome-shell-manage": ("gnome_shell_manage", True), "--kde": ("kde", True), "--bluetooth": ("bluetooth", True), "--battery": ("battery", True), "--avahi": ("avahi", True), "--hostname": ("hostname", True), "--privacy": ("privacy", True), "--no-theme": ("theme", False),
         "--camera": ("camera", True), "--alsa": ("alsa", True), "--input": ("input", True), "--kvm": ("kvm", True), "--ntsync": ("ntsync", True),
         "--rocm": ("rocm", True), "--printing": ("printing", True), "--ssh-agent": ("ssh_agent", True), "--allow-sensitive": ("allow_sensitive", True),
         "--compat": ("compat", True), "--userns": ("userns", True), "--allow-32bit": ("allow_32bit", True), "--single-instance": ("single_instance", True), "--ignore-config": ("ignore_config", True), "--rebind": ("rebind", True),
         **{"--" + k.replace("_", "-"): (k, True) for k in (*PORTAL_EXTRA, *PORTAL_DEFAULT_ON, "portal_file_transfer")},
         **{"--no-" + k.replace("_", "-"): (k, False) for k in (*PORTAL_TOGGLES, *PORTAL_DEFAULT_ON, "portal_file_transfer")}}
VALUE_FLAGS = {"--whitelist": "whitelist", "--read-only": "read_only", "--env": "env", "--setenv": "setenv",
               "--allow-syscall": "allow_syscall", "--talk": "talk", "--memory": "memory", "--tasks": "tasks", "--cpu": "cpu", "--gtk-portal": "gtk_portal", "--name": "name",
               "--loader": "loader", "--app-id": "app_id"}
APP_ID = r"[A-Za-z0-9._+-]{1,255}"       # what a desktop file name (without .desktop) may hold; a window id for the compositor
COMMANDS = ("preview", "show", "doctor", "clean", "reset", "link", "desktop", "open", "schema", "list", "stop", "rebind", "export", "import", "install", "uninstall")

USAGE = ("sbx [options] PROGRAM [args]           run a program in the sandbox\n"
         "sbx preview [options] PROGRAM [args]   print the command instead of running it\n"
         "sbx show [options] NAME                the settings that apply to a program, and where each comes from\n"
         "sbx doctor                             check that everything sbx needs works here\n"
         "sbx list [--all]                       the programs you set up, where each comes from, and which are running (--all adds those only run from the terminal)\n"
         "sbx rebind NAME | --dismiss NAME       decide a program that moved or whose name another file wants: accept it as the same program, or drop the question\n"
         "sbx stop NAME                          stop a running program\n"
         "sbx clean [--yes] NAME | --unused      delete a program's private home and machine-id (not while it runs); --unused: every nearly empty home nobody set up\n"
         "sbx reset [--yes] NAME|--default|--all  delete settings so the built-in defaults apply again (data stays)\n"
         "sbx export [NAME|--default|--all] [-o FILE] [--force]  write settings (default: all) as one text file, to stdout or FILE\n"
         "sbx import FILE [--force]               load an export back; --force replaces settings that differ\n"
         "sbx link NAME [--desktop] | --remove NAME [--desktop]  make typing NAME in a terminal start it through sbx (a link in ~/.local/bin); --desktop covers its app-menu launchers too\n"
         "sbx install FOLDER|ARCHIVE [NAME] [--launcher PATH] [--link]  move a downloaded program to ~/Apps/NAME, take the execute bit off its launcher, add an app-menu entry that starts it through sbx (--link: a terminal command too)\n"
         "sbx uninstall [--yes] [--trash] NAME    remove a program made by sbx install, with its link and menu entry; --trash moves its folder to the Trash (its private home stays; see sbx clean)\n"
         "sbx open FILE                          run a downloaded program with sensible defaults (for \"Open with\")\n"
         "sbx desktop NAME | --remove NAME       make a launcher start the program through sbx (NAME = its id, or the program it starts)\n"
         "sbx desktop --install-opener           add \"Run in sbx\" to your file manager's Open With (--remove-opener undoes it)\n"
         "sbx desktop --install-installer        add \"Install in sbx\" to your file manager's Open With for folders and archives (--remove-installer undoes it)\n"
         "sbx desktop --install-menu             add sbx-gui to your app menu (--remove-menu undoes it)\n"
         "options (before the program; most also exist as a setting, e.g. net=0, x11=1):\n"
         "  --net=none    no network                   --no-wayland   hide the Wayland socket\n"
         "  --net-private internet, but not this computer's own services (localhost); needs pasta\n"
         "  --private     private home erased on exit  --x11          share the X11 socket (weaker isolation)\n"
         "  --here        share the current folder     --no-gpu       hide /dev/dri and NVIDIA devices\n"
         "  --gpu-render-only      of /dev/dri share only the render nodes (renderD*), not the display card nodes\n"
         "  --no-sound    hide PipeWire/PulseAudio     --portals      notifications and basic portals (xdg-dbus-proxy)\n"
         "  --hostname    hostname 'sbx' inside        --userns       the program can create user namespaces\n"
         "  --allow-32bit 32-bit programs may run (weakens the socket filter)\n"
         "  --privacy     /etc/passwd without your real name or the other accounts; the Account portal is blocked too\n"
         "  --tray        the program can show a tray icon (xdg-dbus-proxy)\n"
         "  --mpris       media keys: the program shows up as a media player and answers play, pause and next (xdg-dbus-proxy)\n"
         "  --a11y        screen reader and other assistive tools can see the program (filtered accessibility bus, xdg-dbus-proxy)\n"
         "  --gnome-shell         the browser's GNOME Shell connector can list and install extensions (xdg-dbus-proxy); --gnome-shell-manage also enable, disable and uninstall them\n"
         "  --kde         the browser's Plasma integration: downloads as Plasma jobs, KRunner tabs and history, media controls, keep awake while downloading (xdg-dbus-proxy)\n"
         "  --single-instance     a second launch hands its files to the running one (shares a private $TMPDIR per program)\n"
         "  --camera --alsa --input --kvm --ntsync --rocm --printing --ssh-agent     share that device or socket\n"
         "  --no-theme    don't show your fonts, icons, GTK settings, title-bar buttons and cursor\n"
         "  --portal-remote-desktop --portal-input-capture --portal-launcher --portal-background    one more desktop portal each (off by default; needs --portals)\n"
         "  --no-portal-notification --no-portal-openuri --no-portal-print --no-portal-trash --no-portal-email --no-portal-inhibit --no-portal-network\n"
         "  --no-portal-file-transfer --no-portal-screenshot --no-portal-screencast --no-portal-location --no-portal-shortcuts --no-portal-usb --no-portal-clipboard --no-portal-wallpaper --no-portal-secret\n"
         "                switch one portal off (with --portals; all on by default): notifications, links and files, print dialog, trash, email, keep-awake, network state,\n"
         "                dragged-in files, screenshots, screen sharing, location, global shortcuts, USB, shared clipboard, wallpaper, program secret\n"
         "  --compat      pass graphics/toolkit variables (WGPU_*, MESA_*, SDL_*, QT_QPA_*...) that are set; nothing else\n"
         "  --wayland-strict       no Wayland at all if the compositor can't restrict it\n"
         "  --whitelist=PATH       share a file or folder (read-write); can be repeated\n"
         "  --read-only=PATH       share a file or folder (read-only); can be repeated\n"
         "  --allow-sensitive      allow sharing ~/.ssh, ~/.gnupg and other secret folders\n"
         "  --env=NAME             pass a variable through     --setenv=NAME=VALUE   set a variable\n"
         "  --allow-syscall=NAME   turn a blocked system call back on (io_uring, userfaultfd, kcmp, ...)\n"
         "  --talk=NAME            let the program call this session-bus name, e.g. org.freedesktop.FileManager1 (xdg-dbus-proxy)\n"
         "  --bluetooth --battery --avahi   Bluetooth (org.bluez), battery and power state (UPower), network service discovery (Avahi): through the system bus (xdg-dbus-proxy)\n"
         "  --memory=2G --tasks=512 --cpu=200%   limits (systemd-run --user)\n"
         "  --gtk-portal=auto|1|0  tell GTK programs to use the file chooser portal (with --portals): auto does, except where the host's cursor size is 0\n"
         "  --name=NAME            use another private home and settings name than the program's\n"
         "  --app-id=ID            the id the desktop is told the program has, so it finds the launcher (a launcher's file name without .desktop; sbx desktop sets it)\n"
         "  --loader=PATH          start the program through this dynamic loader (an absolute path; sbx does it itself for a file it can't make executable)\n"
         "  --ignore-config        ignore the settings files for this run\n"
         "  --rebind               this file is the same program as before, just moved (only needed when sbx can't tell; see below)\n"
         "notes: the program starts in its private home, so use full paths or --here for files.\n"
         "  Flags only turn things off (or on, for the extras); settings files set the rest.\n"
         "  net=1 also shares abstract Unix sockets (some X servers); only net=0 or --net-private closes that.\n"
         "  32-bit programs are blocked unless --allow-32bit. Terminal programs lose /dev/tty (--new-session).\n"
         "  A program named like a command (clean, show...): sbx -- clean\n"
         "  A name belongs to the file that first used it. The same file moved or copied is recognised by its content; a different file\n"
         "  under that name is held for your decision (sbx rebind NAME, or sbx-gui) or refused (--name=OTHER). When a move is accepted, what the\n"
         "  program stored beside its old file moves to the new folder in its private home.\n"
         "  A file run by its path whose size and SHA-256 are those of a recorded program whose file is gone is that program renamed: it keeps\n"
         "  that name, even if the new file name has leftovers (not a copy; not when the new name has settings of its own; --name overrides).\n"
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
            elif key == "app_id":
                if not re.fullmatch(APP_ID, v): die(f"{k}: '{v}' is not an id (letters, digits and . _ + - only)")
                o[key] = v
            else:
                if key == "env" and not env_name_ok(v): die(f"{k}: '{v}' is not a variable name")
                if key == "setenv" and ("=" not in v or not env_name_ok(v.partition("=")[0])): die(f"{k}: use NAME=VALUE")
                if key == "allow_syscall" and not syscall_ok(v): die(f"{k}: '{v}' can't be allowed; choose from {', '.join(sorted(EXTRA_SYSCALLS))}, io_uring")
                if key == "talk" and not talk_ok(v): die(f"{k}: '{v}' is not a usable D-Bus name (a wildcard needs three parts first, like org.example.App.*)")
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
            "sensitive": SENSITIVE, "outside": list(OUTSIDE), "syscalls": sorted(EXTRA_SYSCALLS) + ["io_uring"],
            "wayland_restricted": bool(wayland_path() and wl_supported(wayland_path()))}      # can this desktop restrict Wayland programs?
