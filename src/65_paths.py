#| 65_paths: SECURITY-CRITICAL: which paths may be shared with the program, and in what mount order.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- shared paths
def sensitive_match(q):
    """The secret folder (as ~/...) that this path is, is inside, or contains; None if it is none of those."""
    for rel in SENSITIVE:
        s = f"{HOME}/{rel}"
        if q == s or q.startswith(s + "/") or s.startswith(q + "/"): return "~/" + rel
    return None


def share_path(p, cli, allow_sensitive=False):
    """A file or folder to share: inside your home (not the whole home), on a drive, or in /tmp. None if it can't be."""
    q = os.path.expanduser(p)
    if not cli and not os.path.isabs(q):
        warn(f"skipping '{p}': use a full path or ~/...")
        return None
    q = os.path.realpath(q)
    guarded = [os.path.realpath(x) for x in (CONF, HOMES)]       # the sandbox must not be able to rewrite its own rules
    hit = None if allow_sensitive else sensitive_match(q)
    why = ("doesn't exist" if not os.path.exists(q) else "is your whole home" if q == HOME else
           "would expose sbx's own settings or private homes"
           if any(q == g or q.startswith(g + "/") or g.startswith(q + "/") for g in guarded) else
           f"is or contains {hit}, which holds secrets (use --allow-sensitive or allow_sensitive=1 if you mean it)" if hit else
           None if q.startswith((HOME + "/",) + OUTSIDE) else
           "is outside your home, /mnt, /media and /tmp")
    if not why: return q
    if cli: die(f"'{p}' {why}")
    warn(f"skipping '{p}': it {why}")
    return None


def order_shares(rw, ro, here=None, program=None):
    """[(path, 'rw' | 'ro')] in mount order. A folder is mounted before the paths inside it, so a more specific share
    keeps its own mode whatever order the settings were written in. At the very same path read-only wins, except that the
    program's own app folder (program), which sbx adds by itself and shares read-only, gives way to a read-and-write
    share you asked for (whitelist= / --whitelist) at that path."""
    modes = {}
    for p in ([here] if here else []) + rw: modes.setdefault(p, "rw")
    for p in ro: modes[p] = "ro"
    if program and program not in rw: modes[program] = "ro"
    return sorted(modes.items(), key=lambda kv: (kv[0].count("/"), kv[0]))


def program_share(real):
    """What a program in your home brings along: its app folder for .../APP/bin/tool (at least two levels below home,
    so ~/bin, ~/.local/bin and ~/.cargo/bin stay closed), otherwise just the file."""
    d = os.path.dirname(real)
    if os.path.basename(d) == "bin":
        root = os.path.dirname(d)
        if root.startswith(HOME + "/") and "/" in root[len(HOME) + 1:]: return root
    return real


def share_dir(d):
    """The current folder for --here: a folder inside your home, on a drive, or in /tmp, never the whole home."""
    d = os.path.realpath(d)
    if d == HOME: die("--here: refusing to share your whole home; go into a subfolder first")
    if d.startswith(HOME + "/"):
        if d[len(HOME) + 1:].split("/")[0].startswith("."): die("--here: hidden folders in your home can't be shared")
        return d
    if d.startswith(OUTSIDE): return d
    die("--here: only folders inside your home, on /mnt, /media or in /tmp can be shared")


def hint_args(args, here, shared):
    """Warn about arguments that name something on your side the program won't find. Only a hint; nothing changes."""
    said = 0
    for x in args[:64]:
        v = x.split("=", 1)[1] if x.startswith("--") and "=" in x else x
        if not v or v.startswith("-") or len(v) > 1024 or "\n" in v: continue
        host = os.path.abspath(v)
        if not os.path.lexists(host): continue
        inside = lambda base: host == base or host.startswith(base + "/")
        if not os.path.isabs(v):
            if here and inside(here): continue
            why = "the program starts in its private home"
        else:
            if any(inside(s) for s in shared): continue
            if not (inside(HOME) or any(inside(d) for d in ("/tmp", "/run", "/mnt", "/media", "/srv", "/var/tmp", "/root", "/home"))): continue
            why = "that place is not shared"
        warn(f"'{v}' exists here, but the program won't find it ({why}). Use --here, or --whitelist=PATH / --read-only=PATH.")
        said += 1
        if said == 3: break


def portal_file_args(args, app, shared, export=portal_export):
    """ARGS with each file you started the program with (a full path or file:// URI) swapped for its read-only path in the Documents portal,
    so "Open with" works without sharing the folder. Files in a shared folder, secret folders and sbx's own folders stay as they are."""
    guarded = [os.path.realpath(x) for x in (CONF, HOMES)]
    found = {}
    for i, x in enumerate(args[:64]):
        uri = x.startswith("file://")
        q = urllib.parse.unquote(x[7:]) if uri else x
        if not q.startswith("/") or "\n" in q or len(q) > 1024: continue
        q = os.path.realpath(q)
        if not os.path.isfile(q) or any(q.startswith(s + "/") or q == s for s in shared + guarded): continue
        if q.startswith((HOME + "/",) + OUTSIDE) and not sensitive_match(q): found[i] = (q, uri)
    ids = export([q for q, _ in found.values()], app) if found else {}
    out = list(args)
    for i, (q, uri) in found.items():
        if q in ids:
            p = f"{runtime_dir()}/doc/{ids[q]}/{os.path.basename(q)}"
            out[i] = "file://" + urllib.parse.quote(p) if uri else p
    return out


def generic_folders():
    """Folders that hold all sorts of things (Downloads, Desktop...), never shared as a program's own folder."""
    out = {HOME}
    for n in ("Downloads", "Desktop", "Documents", "Pictures", "Music", "Videos", "Public", "Templates"): out.add(f"{HOME}/{n}")
    try:
        with open(f"{os.environ.get('XDG_CONFIG_HOME') or HOME + '/.config'}/user-dirs.dirs") as f:       # translated names
            for m in re.finditer(r'^XDG_\w+_DIR="(?:\$HOME|' + re.escape(HOME) + r')/?([^"]*)"', f.read(), re.M): out.add(f"{HOME}/{m[1]}".rstrip("/"))
    except OSError:
        pass
    return out


def own_folder(real):
    """(folder, why not): the folder a program file sits in, which a program may be started in (its game data, saves, files it
    makes in the current folder), or (None, reason) when that folder is your home, a hidden folder, a general-purpose one
    like Downloads, or not one sbx shares."""
    d = os.path.dirname(real)
    if any(d == g + "/sbx" or d.startswith(g + "/sbx/") for g in generic_folders() - {HOME}):
        return None, f"{tilde(d)} is a per-program shared folder (share_dirs)"
    if d in generic_folders(): return None, f"{tilde(d)} holds all sorts of things (your home, Downloads, Desktop...)"
    if d.startswith(HOME + "/"):
        if d[len(HOME) + 1:].split("/")[0].startswith("."): return None, f"{tilde(d)} is a hidden folder"
        return d, None
    if d.startswith("/tmp/"): return d, None
    return None, f"{d} is not inside your home or /tmp"


def folder_named_like(name, folder):
    """True if the folder's name starts with the program's name, ignoring case and punctuation: G-Diffuser-v1.2.3-linux-x64 is
    G-Diffuser's. The name must be at least 3 letters or digits and the next character in the folder name must not be a letter
    (game is not Games, p is not Projects). A convenience for the folder=auto default, not a security check; own_folder() decides
    what may be shared at all."""
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(n) < 3: return False
    pat = r"[^a-z0-9]*".join(map(re.escape, n)) + r"(?![a-z])"
    return re.match(r"[^a-z0-9]*" + pat, os.path.basename(folder).lower()) is not None


def opener_folder(real):
    """The folder a program brings along when you open it with sbx (its game data, saves...): the folder it sits in,
    unless that is your home, a hidden folder or a general-purpose one like Downloads. None then."""
    return own_folder(real)[0]


APPS = f"{HOME}/Apps"                # where `sbx install` puts programs: one folder each, named after the program
INSTALLED = f"{CONF}/installed"      # one file per installed program (installed/NAME): the full path of its launcher, on the first line


def installed_launcher(name):
    """The launcher file that `sbx install` recorded for NAME, or None. It has no execute bit; sbx sets it for the run."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name): return None
    try:
        with open(f"{INSTALLED}/{name}") as f: p = f.readline().strip()
    except OSError:
        return None
    return p if p.startswith("/") and os.path.isfile(p) else None
