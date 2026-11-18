#| 55_portals: SECURITY-CRITICAL: xdg-dbus-proxy filter rules and the proxy process.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- portals
def portal_rules(cfg=None):
    on = lambda k: not cfg or cfg.get(k, True)          # a switch in PORTAL_TOGGLES: on unless the settings say 0
    rules = (["--talk=org.freedesktop.Notifications"] if on("portal_notification") else []) + [f"--see={PORTAL_NAME}"]
    extra = [i for k, i in PORTAL_EXTRA.items() if cfg and cfg.get(k)]
    if cfg and cfg.get("camera"): extra.append("Camera")          # the Camera portal follows the camera switch
    off = {i for k, v in PORTAL_TOGGLES.items() if not on(k) for i in v}
    basic = [i for i in PORTAL_BASIC if i not in off and not (i == "Account" and cfg and cfg.get("privacy"))]     # Account hands out the real name
    rules += [f"--call={PORTAL_NAME}=org.freedesktop.portal.{i}.*" for i in basic + extra]
    rules += [f"--call={PORTAL_NAME}=org.freedesktop.portal.Request.*@/org/freedesktop/portal/desktop/request/*",
              f"--call={PORTAL_NAME}=org.freedesktop.portal.Session.*@/org/freedesktop/portal/desktop/session/*",   # screencast etc. sessions
              f"--call={PORTAL_NAME}=org.freedesktop.DBus.Properties.*@/org/freedesktop/portal/desktop",
              "--broadcast=org.freedesktop.portal.*=@/org/freedesktop/portal/*"]
    return rules


def portal_app_id(name):
    """The application id the desktop portals know this program by: org.sbx.Run.NAME, with anything outside letters, digits and _
    turned into _ and a p put in front of a digit (an id part may not start with one). The Documents portal keeps the files you picked
    for it under doc/by-app/ID."""
    part = re.sub(r"[^A-Za-z0-9_]", "_", name) or "_"
    return "org.sbx.Run." + ("p" + part if part[0].isdigit() else part)


def tray_rules():
    """Show a tray icon (StatusNotifierItem) and answer media keys (MPRIS). Nothing else on the bus."""
    return ["--talk=org.kde.StatusNotifierWatcher"] + [
        f"--own=org.kde.StatusNotifierItem-{p}-{i}" for p in TRAY_PIDS for i in TRAY_IDS] + ["--own=org.mpris.MediaPlayer2.*"]


def bus_rules(cfg=None):
    """What the proxy lets through: the portal rules when portals is on (also when nothing is said), plus the tray rules."""
    rules = portal_rules(cfg) if cfg is None or cfg.get("portals") else []
    return rules + (tray_rules() if cfg and cfg.get("tray") else [])


PROXY_ROOT_SKIP = ("proc", "dev", "sys", "home", "root", "mnt", "media", "srv", "boot", "lost+found")


def top_binds(skip=()):
    """One read-only bind for each top-level folder of this computer (links stay links), for the proxy's small root.
    The new root is then a filesystem a file can be added to, such as /.flatpak-info: bwrap can't make a file on a read-only /."""
    a = []
    try: top = sorted(os.listdir("/"))
    except OSError: top = []
    for e in top:
        p = "/" + e
        if e in skip: continue
        if os.path.islink(p): a += ["--symlink", os.readlink(p), p]
        elif os.path.isdir(p): a += ["--ro-bind", p, p]
    return a


def app_info_text(app, instance=0):
    """The .flatpak-info the portal reads from the proxy. It wants an [Instance] group too, or it refuses every call."""
    return f"[Application]\nname={app}\n\n[Instance]\ninstance-id={instance}\n"


def proxy_sandbox(info, rt):
    """bwrap arguments for a small root to run xdg-dbus-proxy in: the system read-only, your runtime folder read-write (the bus is there,
    and the proxy makes its own socket there), your home and other private folders not at all, and the program's .flatpak-info at
    /.flatpak-info. xdg-desktop-portal reads that file from the process that talks to it, which is the proxy, so it knows the program as an
    app of its own and the file chooser hands out files through the document portal, not as real paths the sandbox can't see."""
    a = ["bwrap", "--die-with-parent", "--new-session", "--clearenv", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
         "--proc", "/proc", "--dev", "/dev"]
    return a + top_binds(PROXY_ROOT_SKIP) + ["--bind", rt, rt, "--ro-bind", info, "/.flatpak-info"]


def write_app_info(path, app):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f: f.write(app_info_text(app, os.getpid()))


def start_proxy(sock, cfg=None, app=None):
    """Start xdg-dbus-proxy with the portal and tray rules. Returns (process, pipe) or None (a warning says why).
    With APP (an application id) the proxy runs in a small sandbox that tells the portals which app it is; if that fails it runs plainly."""
    px = shutil.which("xdg-dbus-proxy")
    if not px:
        warn(f"xdg-dbus-proxy missing, so portals and the tray are off: {install_hint('xdg-dbus-proxy')}")
        return None
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime_dir()}/bus"
    os.makedirs(os.path.dirname(sock), mode=0o700, exist_ok=True)
    info = sock + ".info"
    for wrapped in ((True, False) if app and shutil.which("bwrap") else (False,)):
        cmd = [px, "--fd=%d", addr, sock, "--filter"] + bus_rules(cfg)
        if wrapped:
            try: write_app_info(info, app)
            except OSError: continue
        r, w = os.pipe()
        cmd[1] = f"--fd={w}"
        if wrapped: cmd = proxy_sandbox(info, runtime_dir()) + cmd
        try:
            proc = subprocess.Popen(cmd, pass_fds=[w], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            os.close(r); os.close(w)
            stop_proxy(None, sock)
            if wrapped: continue
            warn(f"could not start xdg-dbus-proxy ({e}); running without portals or tray")
            return None
        os.close(w)
        ok = False
        try: ok = bool(select.select([r], [], [], 3)[0]) and bool(os.read(r, 1))
        except OSError: pass
        if ok: return proc, r
        stop_proxy((proc, r), sock)
        if wrapped: warn("the portal proxy could not start as an app of its own; the file chooser may give paths the program can't see")
    stop_proxy(None, sock)
    warn("could not start xdg-dbus-proxy; running without portals or tray")
    return None


def stop_proxy(px, sock):
    if px:
        try: os.close(px[1])
        except OSError: pass
        try:
            px[0].terminate()
            try: px[0].wait(2)
            except subprocess.TimeoutExpired: px[0].kill(); px[0].wait(2)       # a stuck proxy must not outlive us
        except (OSError, subprocess.SubprocessError): pass
    for p in (sock, sock + ".info"):
        try: os.unlink(p)
        except OSError: pass
