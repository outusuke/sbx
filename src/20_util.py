#| 20_util: Messages, name cleaning, distro hints, exit-status rule, ELF header reader. No sandbox knowledge.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

def die(msg, code=1):
    print(f"sbx: {msg}", file=sys.stderr)
    if LOG_NAME[0]: log(f"ERROR {msg}", LOG_NAME[0])         # a start from the app menu has no terminal; the log is where it shows
    sys.exit(code)


def warn(msg):
    print(f"sbx: {msg}", file=sys.stderr)
    if LOG_NAME[0]: log(f"WARNING {msg}", LOG_NAME[0])


def clean(n):
    return (re.sub(r"[^A-Za-z0-9._-]", "_", n).lstrip("._-")[:64]) or "app"


def tilde(p):
    return "~/" + p[len(HOME) + 1:] if p.startswith(HOME + "/") else p


def install_hint(pkg):
    """How to install a package on this distribution (from /etc/os-release)."""
    ids = []
    try:
        with open("/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k in ("ID", "ID_LIKE"): ids += v.strip('"').split()
    except OSError:
        pass
    cmds = {"fedora": "sudo dnf install", "rhel": "sudo dnf install", "centos": "sudo dnf install",
            "debian": "sudo apt install", "ubuntu": "sudo apt install", "arch": "sudo pacman -S",
            "opensuse": "sudo zypper install", "suse": "sudo zypper install", "alpine": "sudo apk add",
            "void": "sudo xbps-install"}
    for i in ids:
        if i in cmds: return f"{cmds[i]} {pkg}"
    return f"install the '{pkg}' package"


def exit_code(rc):
    """The shell's way: 128 + the signal number when the program was killed by a signal."""
    return 128 - rc if rc < 0 else rc


def elf_interpreter(path):
    """The dynamic loader an ELF program asks for (PT_INTERP), or None. Lets a file without the execute bit be run."""
    try:
        with open(path, "rb") as f:
            h = f.read(64)
            if h[:4] != b"\x7fELF" or h[5] != 1: return None                     # not ELF, or big-endian
            if h[4] == 2: phoff, phentsize, phnum = struct.unpack_from("<QHH", h, 32)[0], *struct.unpack_from("<HH", h, 54)
            elif h[4] == 1: phoff, phentsize, phnum = struct.unpack_from("<I", h, 28)[0], *struct.unpack_from("<HH", h, 42)
            else: return None
            for i in range(min(phnum, 64)):
                f.seek(phoff + i * phentsize)
                e = f.read(phentsize)
                if h[4] == 2: kind, _, off, _, _, size = struct.unpack_from("<IIQQQQ", e)
                else: kind, off, _, _, size = struct.unpack_from("<IIIII", e)
                if kind == 3 and 0 < size < 4096:                                 # PT_INTERP
                    f.seek(off)
                    p = f.read(size).split(b"\0")[0].decode(errors="replace")
                    return p if os.path.isabs(p) and os.path.isfile(p) else None
    except (OSError, struct.error, IndexError):
        pass
    return None
