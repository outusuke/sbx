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
    keeps its own mode whatever order the settings were written in. At the very same path read-only wins."""
    modes = {}
    for p in ([here] if here else []) + rw: modes.setdefault(p, "rw")
    for p in ro + ([program] if program else []): modes[p] = "ro"
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
