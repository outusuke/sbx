#| 70_build: SECURITY-CRITICAL: build() assembles the complete bwrap command line (and, when live, the files and sockets it needs).
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- the bwrap command
def fake_passwd():
    """A minimal /etc/passwd for privacy=1: you (no full name), root and nobody. Written from scratch, not filtered from the
    real file, so neither your real name (the GECOS field) nor the other accounts on this computer can come through."""
    me = f"{USER}:x:{os.getuid()}:{os.getgid()}::{HOME}:{_pw.pw_shell or '/bin/sh'}\n"
    root = "root:x:0:0:root:/root:/usr/sbin/nologin\n"
    return (me if os.getuid() == 0 else root + me) + "nobody:x:65534:65534:Nobody:/:/usr/sbin/nologin\n"


def build(prog, args, live=False, o=None):
    """The bwrap command. live=True also creates what a real run needs (machine-id, proxy, X cookie, listening sockets...)
    and returns it in `state` so run() can clean up; preview (live=False) only prints."""
    o = o or {}
    loader = o.get("loader")
    if loader and not (os.path.isabs(loader) and os.path.isfile(loader)): die(f"--loader: '{loader}' is not a program")
    path, real = find_program(prog, bool(loader))
    cwd = share_dir(os.getcwd()) if o.get("here") else None
    name = clean(o.get("name") or os.path.basename(path))       # the name you typed, not the file behind a link
    if o.get("name") and name != o["name"]: warn(f"--name: using '{name}' (letters, digits and . _ - only)")
    renamed = None
    if not o.get("name") and "/" in prog:                       # a file, not a name from PATH: a renamed file is still its program (by content, before its file name counts)
        owner, old, why = file_owner(name, real)
        if why and live: warn(why)
        if old: renamed = (name, old)                           # claimed just now (else it was claimed by an earlier run: nothing to say)
        name = owner
    if live:
        check_origin(name, real, o.get("rebind", False))
        if renamed: took_over(renamed[0], name, real, renamed[1])
    cfg, src = effective(name, o)
    if cwd is None and cfg["folder"]:                         # folder=1: start in the folder the program file sits in, like --here from there
        cwd, why = own_folder(real)
        if cwd is None and live:
            warn(f"folder=1: not shared, {name} starts in its private home. {why}. Put the program in a folder of its own (a subfolder, e.g. ~/Games/{name}/) and run it from there.")
    elif cwd is None and src["folder"] == "default":          # folder not set by you anywhere: a program in a folder named after it gets that folder
        d, _ = own_folder(real)
        if d and folder_named_like(name, d):
            cwd = d
            if live: warn(f"{name} starts in {tilde(d)} and shares it read-write, because the folder is named after the program. Turn off with folder=0 (apps/{name}.conf, or sbx-gui).")
    ok = cfg["allow_sensitive"]
    rw = [p for p in [share_path(x, False, ok) for x in cfg["whitelist"]] + [share_path(x, True, ok) for x in o.get("whitelist", [])] if p]
    ro = [p for p in [share_path(x, False, ok) for x in cfg["read_only"]] + [share_path(x, True, ok) for x in o.get("read_only", [])] if p]
    allow = [n for n in cfg["allow_syscall"] if syscall_ok(n)]
    if live and cfg["hostname"] and not bwrap_has("--hostname"):
        die("hostname=1 needs bubblewrap with --hostname (0.7 or newer), so the computer name can't be hidden here. Update bubblewrap, or turn hostname off.")
    if live and not cfg["userns"] and not bwrap_has("--disable-userns"):
        die("userns=0 needs bubblewrap 0.8 or newer (--disable-userns). Not starting without it. Update bubblewrap, or turn userns on for this program.")
    priv = private_net(cfg)
    if live and cfg["net_private"] and not cfg["net"]:
        warn("net_private does nothing while net=0: the program has no network at all")
    if live and priv and not shutil.which("pasta"): die("net_private=1 needs pasta: " + install_hint("passt") + ". Or turn net_private off.")
    if live and priv and pasta_problem(): die(f"net_private=1: {pasta_problem()}. Or turn net_private off.")
    shares = order_shares(rw, ro, cwd, program_share(real) if real.startswith(HOME + "/") else None)
    rt = runtime_dir()
    tag = f"{rt}/sbx/{os.getpid() if live else 'PID'}"
    inst = f"{rt}/.flatpak/{os.getpid() if live else 'PID'}"      # the portal looks the program up here, by the pid in bwrapinfo.json (as it does for a flatpak)
    state = {"proxy": None, "bus": tag + ".bus", "a11y": None, "a11y_bus": tag + ".a11y", "system": None, "system_bus": tag + ".system", "files": [], "fds": [], "keep": [], "wl": None, "cleanup": [], "prefix": [], "wrap": []}
    wrapfiles = []          # files the shell in front of bwrap opens as descriptors when pasta is used, which closes inherited ones: (descriptor, "<" or ">", path)
    env = {"HOME": HOME, "USER": USER, "LOGNAME": USER, "PATH": sandbox_path(), "XDG_RUNTIME_DIR": rt, "SBX_ACTIVE": "1"}
    for k in ENV_KEEP:
        if k in os.environ: env[k] = os.environ[k]
    if cfg["compat"]:
        for k, v in os.environ.items():
            if (k in COMPAT_ENV or k.startswith(COMPAT_PREFIXES)) and k not in ENV_PROTECTED: env[k] = v
    a = ["bwrap", "--unshare-all"] + (["--share-net"] if cfg["net"] else [])
    if not cfg["userns"]: a += ["--unshare-user", "--disable-userns"]
    if priv: a += ["--uid", str(os.getuid()), "--gid", str(os.getgid())]      # pasta's user namespace maps you to root; keep your own ids inside
    a += ["--die-with-parent", "--new-session", "--clearenv"]
    if cfg["hostname"]: a += ["--hostname", "sbx"]

    def put(src, blob):                         # a private file next to the other helper files, removed when the run ends
        if not live: return                     # (a file, not an inherited descriptor: a wrapper such as pasta would close those)
        os.makedirs(os.path.dirname(src), mode=0o700, exist_ok=True)
        fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(blob)
        state["files"].append(src)

    machine = platform.machine()
    c32 = bool(cfg["allow_32bit"]) and machine in COMPAT32
    if cfg["allow_32bit"] and live:
        warn("allow_32bit=1: 32-bit programs may run. socketcall() hides its arguments from the filter, so for them the socket() family check does not apply (AF_ALG, AF_PACKET, AF_VSOCK...)" if c32 else f"allow_32bit=1 has no effect on {machine}")
    if machine in ARCHES and priv:              # inside pasta: bwrap reads the filter from descriptor 9, which a shell opens from this file
        put(f"{tag}.seccomp", seccomp_program(machine, allow, c32))
        a += ["--seccomp", str(SEC_FD)]
        wrapfiles.append((SEC_FD, "<", f"{tag}.seccomp"))
    elif machine in ARCHES:
        if live:
            sec = seccomp_file(allow, c32)
            os.set_inheritable(sec.fileno(), True)
            state["keep"].append(sec); state["fds"].append(sec.fileno())
            a += ["--seccomp", str(sec.fileno())]
        else:
            a += ["--seccomp", "FD"]

    def data(dest, text, ext):                  # a read-only file made from text
        put(f"{tag}.{ext}", text.encode())
        return ["--ro-bind", f"{tag}.{ext}", dest]

    def track(dst, source, tryit):              # bwrap creates mountpoints inside the private home; note them for cleanup
        if not live or cfg["private"] or not dst.startswith(HOME + "/") or (tryit and not os.path.exists(source)): return
        parts = dst[len(HOME) + 1:].split("/")
        for i in range(1, len(parts) + 1):
            p = f"{HOMES}/{name}/" + "/".join(parts[:i])
            if not os.path.lexists(p) and p not in state["cleanup"]: state["cleanup"].append(p)

    def mount(kind, source, dst=None):
        dst = dst or source
        a.extend([kind, source, dst]); track(dst, source, kind.endswith("-try"))

    a += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run"]
    a += data("/proc/sys/kernel/random/boot_id", str(uuid.uuid4()) + "\n", "boot_id")      # the host's boot id is a fingerprint
    for link in NIX_LINKS:
        if os.path.islink(link): a += ["--symlink", os.readlink(link), link]
    for d in resolv_dirs():
        if os.path.isdir(d): a += ["--ro-bind", d, d]
    a += ["--perms", "0700", "--dir", rt]
    if cfg["single_instance"]:      # a second launch of the same program finds the first one's lock/socket files (Chrome keeps its singleton socket in $TMPDIR)
        si = f"{rt}/sbx/single/{name}"
        if live:
            for d in (f"{rt}/sbx", f"{rt}/sbx/single", si): os.makedirs(d, mode=0o700, exist_ok=True)
        a += ["--bind", si, si]
        env["TMPDIR"] = si          # set before setenv=, so a setenv=TMPDIR=... of yours still wins
    # Desktop access (each one a setting): Wayland, X11, GPU, sound, portals.
    gui, late = False, []          # late: mounts that may sit inside the home, so they go after the home is replaced
    wp = wayland_path() if cfg["wayland"] else None
    if wp:
        bind_src = wp
        if live:
            state["wl"] = wayland_context(wp, tag + ".wl", o.get("app_id") or name, f"{name}-{os.getpid()}")
            if state["wl"]: bind_src = tag + ".wl"
        elif wl_supported(wp): bind_src = tag + ".wl"
        # wayland_strict that you never set is on exactly where the compositor can restrict programs: if the attempt failed
        # there, the program gets no Wayland instead of quietly getting the full socket. Where it can't, it gets the full socket.
        if bind_src == wp and (cfg["wayland_strict"] or (live and src["wayland_strict"] == "default" and wl_supported(wp))):
            if live: warn("no restricted Wayland socket could be made, and wayland_strict is on: Wayland is off")
            wp = None
        elif bind_src == wp and live:
            warn("the compositor has no security-context support, so the program gets full Wayland access")
        if wp:
            a += ["--ro-bind", bind_src, wp]; gui = True
            env["WAYLAND_DISPLAY"] = os.environ["WAYLAND_DISPLAY"]; env["XDG_SESSION_TYPE"] = "wayland"
    if cfg["x11"]:
        disp = os.environ.get("DISPLAY", "")
        if disp.startswith(":"):
            sock = f"/tmp/.X11-unix/X{disp[1:].split('.')[0]}"
            a += ["--ro-bind-try", sock, sock]; env["DISPLAY"] = disp; gui = True
        xa = os.environ.get("XAUTHORITY")
        if xa and os.path.isfile(xa):
            xsrc = xa
            if live:                                           # only this display's cookie, not every cookie in the file
                xsrc = scoped_xauth(xa, disp, tag + ".xauth")
                if xsrc: state["files"].append(xsrc)
                else: warn("no usable cookie for this display in XAUTHORITY; not passing the file")
            if xsrc: late += ["--ro-bind", xsrc, xa]; env["XAUTHORITY"] = xa
    if cfg["gpu"] or cfg["rocm"]:
        if cfg["gpu_render_only"]:      # render nodes are enough to draw; the card* nodes can also drive the display
            for p in sorted(glob.glob("/dev/dri/renderD*")): a += ["--dev-bind", p, p]
        else:
            a += ["--dev-bind-try", "/dev/dri", "/dev/dri"]
    if cfg["gpu"]:
        for p in sorted(glob.glob("/dev/nvidia*")): a += ["--dev-bind", p, p]
    if cfg["rocm"]: a += ["--dev-bind-try", "/dev/kfd", "/dev/kfd"]
    if cfg["camera"]:
        for p in sorted(glob.glob("/dev/video*") + glob.glob("/dev/media*")): a += ["--dev-bind", p, p]
    if cfg["input"]: a += ["--dev-bind-try", "/dev/input", "/dev/input", "--ro-bind-try", "/run/udev", "/run/udev"]   # SDL/libinput classify devices from udev's database
    if cfg["alsa"]: a += ["--dev-bind-try", "/dev/snd", "/dev/snd"]
    if cfg["kvm"]: a += ["--dev-bind-try", "/dev/kvm", "/dev/kvm"]
    if cfg["ntsync"]: a += ["--dev-bind-try", "/dev/ntsync", "/dev/ntsync"]
    if cfg["printing"]: a += ["--ro-bind-try", "/run/cups/cups.sock", "/run/cups/cups.sock"]; env["CUPS_SERVER"] = "/run/cups/cups.sock"
    if cfg["sound"]:
        for x in ("pipewire-0", "pulse/native"): a += ["--ro-bind-try", f"{rt}/{x}", f"{rt}/{x}"]
        env["PULSE_SERVER"] = f"unix:{rt}/pulse/native"
    sa = os.environ.get("SSH_AUTH_SOCK", "")
    if cfg["ssh_agent"] and sa and os.path.exists(sa):     # the program can sign as you while it runs
        late += ["--ro-bind", sa, sa]; env["SSH_AUTH_SOCK"] = sa
    if cfg["portals"] or cfg["talk"] or any(cfg[k] for k, _ in BUS_INTEGRATIONS):
        sock = state["bus"]
        if live:
            state["proxy"] = start_proxy(sock, cfg, portal_app_id(name) if cfg["portals"] else None)
            if not state["proxy"]: sock = None
        if sock:
            a += ["--ro-bind", sock, f"{rt}/bus"]; env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
            if cfg["portals"]:      # a toolkit uses the file chooser portal only when told to; a theme that compat passes through from your session (gtk3, qt6ct) would open Qt's own dialog, so it is replaced; env= and setenv= still win (no /.flatpak-info for the program: glycin would then run its image loaders through flatpak-spawn, which fails, and GTK aborts)
                gtk = cfg["gtk_portal"] or "auto"
                if gtk == "auto" and cursor_size_is_zero():
                    gtk = "0"
                    if live: warn("gtk_portal=auto: the host's cursor size is 0, which crashes GTK with the file chooser portal, so GTK_USE_PORTAL is not set (gtk_portal=1 sets it anyway)")
                if gtk != "0": env["GTK_USE_PORTAL"] = "1"
                env["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
                info = f"{inst}/bwrapinfo.json"      # bwrap writes {"child-pid": N} here once the sandbox exists; the portal refuses every call without it
                put(info, b""); state["cleanup"].append(inst)
                if priv: a += ["--info-fd", str(INFO_FD)]; wrapfiles.append((INFO_FD, ">", info))
                elif live:
                    f = os.fdopen(os.open(info, os.O_WRONLY | os.O_NOFOLLOW), "wb"); os.set_inheritable(f.fileno(), True)
                    state["keep"].append(f); state["fds"].append(f.fileno()); a += ["--info-fd", str(f.fileno())]
                else: a += ["--info-fd", "FD"]
    if cfg["a11y"]:         # a screen reader talks to each program over the accessibility bus; the program gets a filtered copy of it (works without portals)
        sock = state["a11y_bus"]
        if live:
            state["a11y"] = start_a11y_proxy(sock)
            if not state["a11y"]: sock = None
        if sock:
            a += ["--ro-bind", sock, f"{rt}/at-spi/bus"]; env["AT_SPI_BUS_ADDRESS"] = f"unix:path={rt}/at-spi/bus"
            env.update(A11Y_ENV)
    if any(cfg[k] for k in SYSTEM_BUS_NAMES):
        sock = state["system_bus"]
        if live:
            state["system"] = start_system_proxy(sock, cfg)
            if not state["system"]: sock = None
        if sock:
            a += ["--ro-bind", sock, SYSTEM_BUS_SOCKET]; env["DBUS_SYSTEM_BUS_ADDRESS"] = f"unix:path={SYSTEM_BUS_SOCKET}"
    if cfg["portals"]:      # the files you pick in a chooser appear here, only this program's own (bind-try: nothing when there is no document portal)
        a += ["--bind-try", f"{runtime_dir()}/doc/by-app/{portal_app_id(name)}", f"{rt}/doc"]
    if live and not cfg["portals"] and any(cfg[k] for k in PORTAL_EXTRA):
        warn("portal_* settings need portals=1; ignored")
    # Hide the real home and other users: empty the folder that holds your home, then put the private home in its place.
    parent = os.path.dirname(HOME)
    hide = [parent if parent != "/" else HOME]
    for d in EMPTY_DIRS + ["/home"]:
        if os.path.isdir(d) and not os.path.islink(d) and d not in hide and not HOME.startswith(d + "/"):
            hide.append(d)
    for d in hide: a += ["--tmpfs", d]
    for d in HIDE_SYS + ([] if cfg["net"] and not priv else ["/sys/class/net"]):     # /sys/class/net would list the host's cards even without a network, or with pasta's own
        if os.path.isdir(d): a += ["--tmpfs", d]
    a += ["--tmpfs", HOME] if cfg["private"] else ["--bind", f"{HOMES}/{name}", HOME]     # private=1: erased on exit
    if not cfg["private"]:
        for own, dst in share_mounts(name, cfg["share_dirs"], live): a += ["--bind", own, dst]
    for i in range(0, len(late), 3): mount(late[i], late[i + 1], late[i + 2])
    if gui and cfg["theme"]:
        for rel in THEME: mount("--ro-bind-try", f"{HOME}/{rel}")
        gs = gsettings_keyfile(tag + ".gsettings", live)          # button layout and cursor from dconf, which the program can't read
        if gs:
            mount("--ro-bind", gs, f"{HOME}/.config/glib-2.0/settings/keyfile")
            env["GSETTINGS_BACKEND"] = "keyfile"
            if live: state["files"].append(gs)
    for p, mode in shares: mount("--bind" if mode == "rw" else "--ro-bind", p)      # --here, whitelist=, read_only=, the program itself
    for p in SECRETS + [x for g in SECRET_GLOBS for x in glob.glob(g)]:
        if os.path.isdir(p): a += ["--tmpfs", p]
        elif os.path.isfile(p): a += ["--ro-bind", "/dev/null", p]
    if cfg["hostname"]:                         # /etc/hostname and /etc/hosts would still tell the real name
        if os.path.isfile("/etc/hostname"): a += data("/etc/hostname", "sbx\n", "hostname")
        if os.path.isfile("/etc/hosts"): a += data("/etc/hosts", "127.0.0.1 localhost\n::1 localhost\n127.0.1.1 sbx\n", "hosts")
    if cfg["privacy"] and os.path.isfile("/etc/passwd"):      # onto the file's real path: bwrap can't mount on a symlink (NixOS)
        a += data(os.path.realpath("/etc/passwd"), fake_passwd(), "passwd")
    if priv and os.path.isfile("/etc/resolv.conf"):      # the only name server pasta answers; onto the link's target, bwrap can't mount on a symlink
        a += data(resolv_target(), f"nameserver {DNS_FWD}\n", "resolv.conf")
    if os.path.isfile("/etc/machine-id"):
        mid = machine_id(name, live)
        a += ["--ro-bind", mid, "/etc/machine-id"]
        if os.path.isfile("/var/lib/dbus/machine-id") and not os.path.islink("/var/lib/dbus/machine-id"):
            a += ["--ro-bind", mid, "/var/lib/dbus/machine-id"]
    for k in cfg["env"]:                        # your own variables, passed through or set; the core ones are off limits
        if k in os.environ and k not in ENV_PROTECTED: env[k] = os.environ[k]
    for kv in cfg["setenv"]:
        k, _, v = kv.partition("=")
        if k in ENV_PROTECTED: warn(f"setenv: {k} can't be changed")
        else: env[k] = v
    for k, v in env.items(): a += ["--setenv", k, v]
    shared = [p for p, _ in shares]
    if live and cfg["portals"]:
        args = portal_file_args(args, portal_app_id(name), shared)
        shared.append(f"{rt}/doc")
    exe = real if real.startswith(HOME + "/") or path.startswith(HOME + "/") else path
    if live: hint_args(args, cwd, shared)
    limits = [f"-p{p}={cfg[k]}" for k, p in (("memory", "MemoryMax"), ("tasks", "TasksMax"), ("cpu", "CPUQuota")) if cfg[k]]
    if limits: state["prefix"] = ["systemd-run", "--user", "--scope", "--quiet"] + limits + ["--"]
    if priv: state["wrap"] = net_wrap(wrapfiles)
    return name, a + ["--chdir", cwd or HOME, "--"] + ([loader] if loader else []) + [exe] + shlex.split(cfg["args"]) + args, state
