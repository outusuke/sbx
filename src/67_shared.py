#| 67_shared: Per-program shared folders: <your folder>/sbx/NAME mounted over the same folder in the private home.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- shared folders
def share_names(text):
    """The folder names in a share_dirs value, in SHARE_DIRS order."""
    want = {x.strip() for x in text.split(",")}
    return [n for n in SHARE_DIRS if n in want]


def share_hosts(names):
    """[(your real folder, its path inside the sandbox)] for NAMES. Left out: a folder that is unset, missing, your home, hidden,
    secret, or inside another one in the list."""
    found = []
    for n in names:
        d = user_dir(SHARE_DIRS[n])
        if d and os.path.isdir(d) and not d[len(HOME) + 1:].startswith(".") and not sensitive_match(d) and d not in found: found.append(d)
    found.sort(key=lambda d: d.count("/"))
    keep = []
    for d in found:
        if not any(d.startswith(k + "/") for k in keep): keep.append(d)
    return keep


def share_move(old, new):
    """Move what is in the folder OLD (the private home's own Downloads, ...) into NEW. Nothing is overwritten and nothing is deleted:
    (moved, left) counts; an entry whose name is taken, or that can't be renamed, stays."""
    moved = left = 0
    try: entries = sorted(os.listdir(old))
    except OSError: return 0, 0
    for e in entries:
        if os.path.lexists(f"{new}/{e}"): left += 1; continue
        try: os.rename(f"{old}/{e}", f"{new}/{e}"); moved += 1
        except OSError: left += 1
    return moved, left


def share_mounts(name, text, live):
    """[(folder for this program, path inside the sandbox)] to bind after the private home. live=True also makes the folders (mode
    0700) and moves what the private home already holds there. A folder that isn't safe to use (sbx or sbx/NAME is a link or not
    a plain folder, the private home's own folder is behind a link, it can't be made) is skipped with a warning: it stays in the
    private home."""
    out, moved, left = [], 0, 0
    home = os.path.realpath(f"{HOMES}/{name}")
    for d in share_hosts(share_names(text)):
        rel = d[len(HOME) + 1:]
        base, own = f"{d}/sbx", f"{d}/sbx/{name}"
        bad = next((p for p in (base, own) if os.path.lexists(p) and (os.path.islink(p) or not os.path.isdir(p))), None)
        inside = f"{HOMES}/{name}/{rel}"
        if bad: why = f"{tilde(bad)} is a link or not a plain folder"
        elif os.path.realpath(inside) != f"{home}/{rel}": why = f"{tilde(inside)} is behind a link"
        else: why = None
        if why is None and live:
            try:
                for p in (base, own):
                    if not os.path.isdir(p): os.mkdir(p, 0o700)
            except OSError as e:
                why = f"can't make {tilde(own)} ({e.strerror})"
        if why:
            if live: warn(f"share_dirs: {rel} stays in the private home: {why}")
            continue
        if live and os.path.isdir(inside):
            m, k = share_move(inside, own)
            moved, left = moved + m, left + k
        out.append((own, f"{HOME}/{rel}"))
    if live and moved: warn(f"moved {moved} item{'s' if moved != 1 else ''} from the private home's shared folders into <folder>/sbx/{name}" + (f"; {left} left in place (the name is taken there)" if left else ""))
    return out
