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


def scoped_xauth(src, disp, dst):
    """A copy of the XAUTHORITY file with only the cookie for this display (no xauth tool needed), or None."""
    num = disp[1:].split(".")[0].encode()
    try:
        with open(src, "rb") as f: data = f.read(1_000_000)
    except OSError:
        return None
    keep, i = [], 0
    while i + 2 <= len(data):
        start = i
        i += 2                                    # family
        fields = []
        for _ in range(4):                        # address, display number, auth name, auth data
            if i + 2 > len(data): return None     # truncated or not an Xauthority file: use it unchanged
            n = int.from_bytes(data[i:i+2], "big"); i += 2
            fields.append(data[i:i+n]); i += n
        if i > len(data): return None
        if fields[1] in (num, b""): keep.append(data[start:i])
    if not keep: return None
    try:
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(b"".join(keep))
    except OSError:
        return None
    return dst
