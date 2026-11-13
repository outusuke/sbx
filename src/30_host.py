#| 30_host: Looking at the host: machine-id file, program lookup, runtime dir, Wayland socket path, bwrap features, scoped X cookie.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- small helpers
def machine_id(name, create):
    """A random machine-id per program, so the real one can't be used to recognise your computer."""
    p = f"{CONF}/ids/{name}"
    if create and not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(uuid.uuid4().hex + "\n")
    return p


def find_program(prog, noexec=False):
    path = os.path.abspath(prog) if "/" in prog else shutil.which(prog)
    if not path or not os.path.isfile(path) or not (noexec or os.access(path, os.X_OK)):
        die(f"'{prog}' not found or not executable")
    real = os.path.realpath(path)
    if real.startswith(DRIVES):
        die(f"'{prog}' is on another drive, which the sandbox hides. Copy it somewhere else first.")
    return path, real


def resolv_dirs():
    """/etc/resolv.conf is often a link into /run, which is emptied; keep the folder it points to."""
    out = ["/run/systemd/resolve", "/run/NetworkManager"]
    try:
        t = os.path.realpath("/etc/resolv.conf")
        if t.startswith("/run/") and os.path.dirname(t) not in out: out.append(os.path.dirname(t))
    except OSError:
        pass
    return out


def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def wayland_path():
    wl = os.environ.get("WAYLAND_DISPLAY", "")
    if not wl: return None
    p = wl if wl.startswith("/") else f"{runtime_dir()}/{wl}"
    return p if os.path.exists(p) else None


def sandbox_path():
    p = ["/usr/local/bin", "/usr/bin", "/usr/sbin"]
    if os.path.isdir("/bin") and not os.path.islink("/bin"): p += ["/bin", "/sbin"]          # distros without merged /usr
    if os.path.islink("/run/current-system"): p += ["/run/current-system/sw/bin", "/run/current-system/profile/bin"]
    return ":".join(p)


def bwrap_has(flag):
    try: r = subprocess.run(["bwrap", "--help"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired): return False
    return flag in r.stdout + r.stderr


def private_net(cfg):
    """net_private: the program gets its own network (through pasta) that reaches the internet but not this computer."""
    return bool(cfg["net"] and cfg["net_private"])


# pasta gives the sandbox its own network namespace. Nothing is forwarded in either direction (-t/-u/-T/-U none), the gateway
# address is not mapped to this computer (--no-map-gw), and DNS goes through a forwarder address that pasta answers itself.
DNS_FWD = "169.254.1.1"
PASTA = ["pasta", "--quiet", "--config-net", "--no-map-gw", "--dns-forward", DNS_FWD,
         "-t", "none", "-u", "none", "-T", "none", "-U", "none", "--"]
SEC_FD = 9      # pasta closes inherited descriptors, so bwrap's seccomp filter is re-opened from a file inside it


def net_wrap(secpath=None):
    """The words that go in front of bwrap to run it inside pasta. With secpath, a shell first opens that file as descriptor 9."""
    if secpath: return PASTA + ["/bin/sh", "-c", f'exec {SEC_FD}<"$1"; shift; exec "$@"', "sh", secpath]
    return PASTA


def scoped_xauth(src, disp, dst):
    """A copy of the XAUTHORITY file with only the cookie for this display on this computer (no xauth tool needed), or None.
    An entry is kept when it is for this display number AND for this machine (family Local with our host name, or family
    Wild), so a cookie for display :0 on another host is not shared. Entries with no display number (Xwayland's own auth
    file) are kept too. A DISPLAY that is not ':N' (for example SSH's localhost:10.0) gets nothing."""
    if not disp.startswith(":"): return None
    num = disp[1:].split(".")[0].encode()
    me = os.fsencode(socket.gethostname())
    try:
        with open(src, "rb") as f: data = f.read(1_000_000)
    except OSError:
        return None
    keep, i = [], 0
    while i + 2 <= len(data):
        start = i
        fam = int.from_bytes(data[i:i+2], "big"); i += 2      # family: 256 Local, 65535 Wild
        fields = []
        for _ in range(4):                        # address, display number, auth name, auth data
            if i + 2 > len(data): return None     # truncated or not an Xauthority file: not shared
            n = int.from_bytes(data[i:i+2], "big"); i += 2
            fields.append(data[i:i+n]); i += n
        if i > len(data): return None
        mine = fam == 65535 or (fam == 256 and fields[0] == me)
        if fields[1] == b"" or (fields[1] == num and mine): keep.append(data[start:i])
    if not keep: return None
    try:
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(b"".join(keep))
    except OSError:
        return None
    return dst


# ---------------------------------------------------------------- desktop settings the sandbox can't read
# The title-bar button layout (minimize, maximize, close) and the cursor theme and size live in dconf, which a sandboxed GTK
# program can't reach: it falls back to GTK's own defaults (no minimize/maximize button on GNOME, a different cursor).
# sbx runs outside the sandbox, so it reads them here and gives the program only these settings, as a GSettings key file
# (GTK reads them from GSettings, and that beats a settings.ini). The program is pointed at it with GSETTINGS_BACKEND=keyfile.
# Nothing is hardcoded: if no desktop setting is found, nothing is shared. Every value is checked before it goes in the file.
LAYOUT_KEYS = (("org.gnome.desktop.wm.preferences", "button-layout"),     # GNOME (stored in dconf)
               ("org.cinnamon.muffin", "button-layout"),
               ("org.mate.Marco.general", "button-layout"))
LAYOUT_RE = re.compile(r"[a-z,:_]{0,64}")       # only button names, commas and the colon; anything else is ignored
CURSOR_SCHEMA = "org.gnome.desktop.interface"
CURSOR_THEME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}")    # a folder name: no '/', no quotes, no leading dot
CURSOR_SIZE_RE = re.compile(r"[0-9]{1,3}")
CURSOR_SIZE_MAX = 256


def gsettings_get(schema, key):
    """The raw gsettings value as text, or None (no gsettings, no such key, error, timeout)."""
    gs = shutil.which("gsettings")
    if not gs: return None
    try: r = subprocess.run([gs, "get", schema, key], capture_output=True, text=True, timeout=3)
    except Exception: return None
    return r.stdout.strip() if r.returncode == 0 else None


def gsettings_string(raw):
    """The text of a plain gsettings string 'like this', else None. No escapes, no other quote style."""
    if raw is None or len(raw) < 2 or raw[0] != "'" or raw[-1] != "'": return None
    return raw[1:-1]


def gtk_ini_layout():
    """gtk-decoration-layout from your own GTK settings.ini, or None."""
    for ver in ("gtk-4.0", "gtk-3.0"):
        try:
            with open(f"{HOME}/.config/{ver}/settings.ini") as f:
                for ln in f:
                    k, _, v = ln.partition("=")
                    if k.strip() == "gtk-decoration-layout" and LAYOUT_RE.fullmatch(v.strip()): return v.strip()
        except (OSError, UnicodeDecodeError):
            pass
    return None


def host_button_layout():
    """The button layout from the desktop settings (gsettings/dconf), else from your own GTK settings.ini, else None."""
    for schema, key in LAYOUT_KEYS:
        v = gsettings_get(schema, key)
        v = v.strip("'\"") if v is not None else None
        if v is not None and LAYOUT_RE.fullmatch(v): return v
    return gtk_ini_layout()


def host_cursor():
    """(theme, size) of the host's cursor from gsettings; None for whatever is missing or doesn't pass the checks.
    Size 0 means 'default' and counts as unset."""
    theme = gsettings_string(gsettings_get(CURSOR_SCHEMA, "cursor-theme"))
    if theme is not None and not CURSOR_THEME_RE.fullmatch(theme): theme = None
    size = gsettings_get(CURSOR_SCHEMA, "cursor-size")
    if size is not None and CURSOR_SIZE_RE.fullmatch(size) and 0 < int(size) <= CURSOR_SIZE_MAX: size = int(size)
    else: size = None
    return theme, size


def gsettings_keyfile_text():
    """Text of the key file: just the host's button layout and cursor theme/size. None if there is nothing to share.
    Always written under GNOME's schemas, which are the ones GTK reads, whichever desktop the values came from."""
    out = []
    layout = host_button_layout()
    if layout is not None: out.append(f"[org/gnome/desktop/wm/preferences]\nbutton-layout='{layout}'\n")
    theme, size = host_cursor()
    cur = ([f"cursor-theme='{theme}'\n"] if theme is not None else []) + ([f"cursor-size={size}\n"] if size is not None else [])
    if cur: out.append("[org/gnome/desktop/interface]\n" + "".join(cur))
    return "\n".join(out) or None


def gsettings_keyfile(dst, live):
    """The key file for this run at dst (written only when live), or None when there is nothing to share."""
    text = gsettings_keyfile_text()
    if text is None: return None
    if live:
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f: f.write(text)
    return dst
