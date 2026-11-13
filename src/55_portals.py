#| 55_portals: SECURITY-CRITICAL: xdg-dbus-proxy filter rules and the proxy process.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- portals
def portal_rules(cfg=None):
    rules = ["--talk=org.freedesktop.Notifications", f"--see={PORTAL_NAME}"]
    extra = [i for k, i in PORTAL_EXTRA.items() if cfg and cfg.get(k)]
    if cfg and cfg.get("camera"): extra.append("Camera")          # the Camera portal follows the camera switch
    rules += [f"--call={PORTAL_NAME}=org.freedesktop.portal.{i}.*" for i in PORTAL_BASIC + tuple(extra)]
    rules += [f"--call={PORTAL_NAME}=org.freedesktop.portal.Request.*@/org/freedesktop/portal/desktop/request/*",
              f"--call={PORTAL_NAME}=org.freedesktop.portal.Session.*@/org/freedesktop/portal/desktop/session/*",   # screencast etc. sessions
              f"--call={PORTAL_NAME}=org.freedesktop.DBus.Properties.*@/org/freedesktop/portal/desktop",
              "--broadcast=org.freedesktop.portal.*=@/org/freedesktop/portal/*"]
    return rules


def tray_rules():
    """Show a tray icon (StatusNotifierItem) and answer media keys (MPRIS). Nothing else on the bus."""
    return ["--talk=org.kde.StatusNotifierWatcher"] + [
        f"--own=org.kde.StatusNotifierItem-{p}-{i}" for p in TRAY_PIDS for i in TRAY_IDS] + ["--own=org.mpris.MediaPlayer2.*"]


def bus_rules(cfg=None):
    """What the proxy lets through: the portal rules when portals is on (also when nothing is said), plus the tray rules."""
    rules = portal_rules(cfg) if cfg is None or cfg.get("portals") else []
    return rules + (tray_rules() if cfg and cfg.get("tray") else [])


def start_proxy(sock, cfg=None):
    """Start xdg-dbus-proxy with the portal and tray rules. Returns (process, pipe) or None (a warning says why)."""
    px = shutil.which("xdg-dbus-proxy")
    if not px:
        warn(f"xdg-dbus-proxy missing, so portals and the tray are off: {install_hint('xdg-dbus-proxy')}")
        return None
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime_dir()}/bus"
    os.makedirs(os.path.dirname(sock), mode=0o700, exist_ok=True)
    r, w = os.pipe()
    proc = subprocess.Popen([px, f"--fd={w}", addr, sock, "--filter"] + bus_rules(cfg), pass_fds=[w],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    ok = False
    try: ok = bool(select.select([r], [], [], 3)[0]) and bool(os.read(r, 1))
    except OSError: pass
    if ok: return proc, r
    stop_proxy((proc, r), sock)
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
    try: os.unlink(sock)
    except OSError: pass
