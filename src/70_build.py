#| 70_build: SECURITY-CRITICAL: build() assembles the complete bwrap command line (and, when live, the files and sockets it needs).
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- the bwrap command
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
    if live: check_origin(name, real, o.get("rebind", False))
    cfg, src = effective(name, o)
    ok = cfg["allow_sensitive"]
    rw = [p for p in [share_path(x, False, ok) for x in cfg["whitelist"]] + [share_path(x, True, ok) for x in o.get("whitelist", [])] if p]
    ro = [p for p in [share_path(x, False, ok) for x in cfg["read_only"]] + [share_path(x, True, ok) for x in o.get("read_only", [])] if p]
    allow = [n for n in cfg["allow_syscall"] if syscall_ok(n)]
    if live and cfg["hostname"] and not bwrap_has("--hostname"):
        die("hostname=1 needs bubblewrap with --hostname (0.7 or newer), so the computer name can't be hidden here. Update bubblewrap, or turn hostname off.")
    if live and not cfg["userns"] and not bwrap_has("--disable-userns"):
        die("userns=0 needs bubblewrap 0.8 or newer (--disable-userns). Not starting without it.")
    shares = order_shares(rw, ro, cwd, program_share(real) if real.startswith(HOME + "/") else None)
    rt = runtime_dir()
    tag = f"{rt}/sbx/{os.getpid() if live else 'PID'}"
    state = {"proxy": None, "bus": tag + ".bus", "files": [], "fds": [], "keep": [], "wl": None, "cleanup": [], "prefix": []}
    env = {"HOME": HOME, "USER": USER, "LOGNAME": USER, "PATH": sandbox_path(), "XDG_RUNTIME_DIR": rt, "SBX_ACTIVE": "1"}
    for k in ENV_KEEP:
        if k in os.environ: env[k] = os.environ[k]
    if cfg["compat"]:
        for k, v in os.environ.items():
            if (k in COMPAT_ENV or k.startswith(COMPAT_PREFIXES)) and k not in ENV_PROTECTED: env[k] = v
    a = ["bwrap", "--unshare-all"] + (["--share-net"] if cfg["net"] else [])
    if not cfg["userns"]: a += ["--unshare-user", "--disable-userns"]
    a += ["--die-with-parent", "--new-session", "--clearenv"]
    if cfg["hostname"]: a += ["--hostname", "sbx"]
    machine = platform.machine()
    if machine in ARCHES:
        if live:
            sec = seccomp_file(allow)
            os.set_inheritable(sec.fileno(), True)
            state["keep"].append(sec); state["fds"].append(sec.fileno())
            a += ["--seccomp", str(sec.fileno())]
        else:
            a += ["--seccomp", "FD"]

    def data(dest, text, ext):                  # a read-only file made from text: a private file, removed when the run ends
        src = f"{tag}.{ext}"                    # (a file, not an inherited descriptor: a wrapper such as pasta would close those)
        if live:
            os.makedirs(os.path.dirname(src), mode=0o700, exist_ok=True)
            fd = os.open(src, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as f: f.write(text)
            state["files"].append(src)
        return ["--ro-bind", src, dest]

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
    # Desktop access (each one a setting): Wayland, X11, GPU, sound, portals.
    gui, late = False, []          # late: mounts that may sit inside the home, so they go after the home is replaced
    wp = wayland_path() if cfg["wayland"] else None
    if wp:
        bind_src = wp
        if live:
            state["wl"] = wayland_context(wp, tag + ".wl", name, f"{name}-{os.getpid()}")
            if state["wl"]: bind_src = tag + ".wl"
        elif wl_supported(wp): bind_src = tag + ".wl"
        if bind_src == wp and cfg["wayland_strict"]:
            if live: warn("the compositor has no security-context support, and wayland_strict is on: Wayland is off")
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
    if cfg["printing"]: a += ["--ro-bind-try", "/run/cups/cups.sock", "/run/cups/cups.sock"]; env["CUPS_SERVER"] = "/run/cups/cups.sock"
    if cfg["sound"]:
        for x in ("pipewire-0", "pulse/native"): a += ["--ro-bind-try", f"{rt}/{x}", f"{rt}/{x}"]
        env["PULSE_SERVER"] = f"unix:{rt}/pulse/native"
    sa = os.environ.get("SSH_AUTH_SOCK", "")
    if cfg["ssh_agent"] and sa and os.path.exists(sa):     # the program can sign as you while it runs
        late += ["--ro-bind", sa, sa]; env["SSH_AUTH_SOCK"] = sa
    if cfg["portals"] or cfg["tray"]:
        sock = state["bus"]
        if live:
            state["proxy"] = start_proxy(sock, cfg)
            if not state["proxy"]: sock = None
        if sock:
            a += ["--ro-bind", sock, f"{rt}/bus"]; env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
    if live and not cfg["portals"] and any(cfg[k] for k in PORTAL_EXTRA):
        warn("portal_* settings need portals=1; ignored")
    # Hide the real home and other users: empty the folder that holds your home, then put the private home in its place.
    parent = os.path.dirname(HOME)
    hide = [parent if parent != "/" else HOME]
    for d in EMPTY_DIRS + ["/home"]:
        if os.path.isdir(d) and not os.path.islink(d) and d not in hide and not HOME.startswith(d + "/"):
            hide.append(d)
    for d in hide: a += ["--tmpfs", d]
    for d in HIDE_SYS + ([] if cfg["net"] else ["/sys/class/net"]):     # /sys/class/net would list the host's cards even without a network
        if os.path.isdir(d): a += ["--tmpfs", d]
    a += ["--tmpfs", HOME] if cfg["private"] else ["--bind", f"{HOMES}/{name}", HOME]     # private=1: erased on exit
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
    exe = real if real.startswith(HOME + "/") or path.startswith(HOME + "/") else path
    if live: hint_args(args, cwd, [p for p, _ in shares])
    limits = [f"-p{p}={cfg[k]}" for k, p in (("memory", "MemoryMax"), ("tasks", "TasksMax"), ("cpu", "CPUQuota")) if cfg[k]]
    if limits: state["prefix"] = ["systemd-run", "--user", "--scope", "--quiet"] + limits + ["--"]
    return name, a + ["--chdir", cwd or HOME, "--"] + ([loader] if loader else []) + [exe] + args, state
