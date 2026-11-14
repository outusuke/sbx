#| 66_origins: SECURITY-CRITICAL: which file a program name belongs to (origins/NAME), what happens when another file shows up under it, and the decisions waiting for you (pending/NAME).
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- what a name belongs to
# origins/NAME: line 1 the real path the name was first run from, line 2 "size mtime_ns sha256" of that file (absent in files from
# older versions), line 3 "from PATH" after the name moved (so sbx-gui can say so). pending/NAME: old=, new=, rule= (c or d), reason=.
ORIGIN_META = re.compile(r"(\d+) (\d+) ([0-9a-f]{64})")
SOURCE_RANK = {"System": 0, "Downloaded": 2}      # the order of `sbx list --all` for programs only run from the terminal; anything else sorts between


def fingerprint(path):
    """(size, mtime in ns, SHA-256 of the whole file as hex) or None when the file isn't there. The hex is None for a file that can't be
    read. The file is stat'ed before it is read, so one that changes meanwhile shows a newer mtime next time and is hashed again."""
    try: st = os.stat(path)
    except OSError: return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    except OSError:
        return st.st_size, st.st_mtime_ns, None
    return st.st_size, st.st_mtime_ns, h.hexdigest()


def read_origin(name):
    """(path, (size, mtime_ns, sha256) or None, moved-from path or None) from origins/NAME, or None when nothing is recorded."""
    try:
        with open(f"{CONF}/origins/{name}") as f: lines = f.read().split("\n")
    except OSError:
        return None
    path = lines[0].strip()
    if not path: return None
    m = ORIGIN_META.fullmatch(lines[1].strip()) if len(lines) > 1 else None
    moved = lines[2][5:].strip() if len(lines) > 2 and lines[2].startswith("from ") else None
    return path, ((int(m[1]), int(m[2]), m[3]) if m else None), moved or None


def write_origin(name, path, fp=None, moved=None):
    """Record PATH (with its fingerprint FP, when there is one, and where it moved MOVED from) as what NAME belongs to."""
    p = f"{CONF}/origins/{name}"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lines = [path]
    if (fp and fp[2]) or moved: lines.append(f"{fp[0]} {fp[1]} {fp[2]}" if fp and fp[2] else "")
    if moved: lines.append(f"from {moved}")
    with open(p + ".tmp", "w") as f: f.write("\n".join(lines) + "\n")
    os.replace(p + ".tmp", p)


def downloads_dir():
    """Your Downloads folder: XDG_DOWNLOAD_DIR in user-dirs.dirs (translated names), else ~/Downloads."""
    try:
        with open(f"{os.environ.get('XDG_CONFIG_HOME') or HOME + '/.config'}/user-dirs.dirs") as f:
            m = re.search(r'^XDG_DOWNLOAD_DIR="(?:\$HOME|' + re.escape(HOME) + r')/?([^"]*)"', f.read(), re.M)
        if m and m[1]: return f"{HOME}/{m[1]}".rstrip("/")
    except OSError:
        pass
    return f"{HOME}/Downloads"


def source_kind(path):
    """System (/usr, /opt, /snap...), Downloaded (inside your Downloads folder), Home (the rest of your home) or Other."""
    if path.startswith(SYSTEM_PROGRAMS): return "System"
    if path.startswith(downloads_dir() + "/"): return "Downloaded"
    return "Home" if path.startswith(HOME + "/") else "Other"


def origin_source(name):
    """Where NAME's program comes from, as recorded: {kind, path, missing, moved, moved_exists}, or None when nothing is recorded."""
    rec = read_origin(name)
    if not rec: return None
    path, _meta, moved = rec
    return {"kind": source_kind(path), "path": path, "missing": not os.path.isfile(path), "moved": moved,
            "moved_exists": bool(moved and os.path.isfile(moved))}


def source_text(s):
    """One line for a source: 'System: /usr/bin', 'Downloaded: ~/Downloads/Crash', 'Missing: ~/old/path', plus 'moved from ...'."""
    text = (f"Missing: {tilde(s['path'])}" if s["missing"] else f"System: {os.path.dirname(s['path'])}" if s["kind"] == "System"
            else f"{s['kind']}: {tilde(s['path'])}")
    return text + (f", {'copied' if s['moved_exists'] else 'moved'} from {tilde(s['moved'])}" if s["moved"] else "")


# ---------------------------------------------------------------- decisions waiting for you
def pending_path(name):
    return f"{CONF}/pending/{name}"


def read_pending(name):
    """{old, new, rule, reason} from pending/NAME, or None."""
    try:
        with open(pending_path(name)) as f: text = f.read()
    except OSError:
        return None
    d = {}
    for line in text.split("\n"):
        k, eq, v = line.partition("=")
        if eq and k in ("old", "new", "rule", "reason"): d[k] = v
    return d if d.get("old") and d.get("new") and d.get("rule") in ("c", "d") else None


def write_pending(name, old, new, rule, reason):
    p = pending_path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w") as f: f.write(f"old={old}\nnew={new}\nrule={rule}\nreason={reason}\n")
    os.replace(p + ".tmp", p)


def clear_pending(name):
    try: os.unlink(pending_path(name))
    except OSError: pass


def pending_stale(name, p):
    """Why the decision P no longer applies, or None while it does: it describes what is recorded (the recorded path is still its old
    one), the new file is still there, and the old file is gone for rule c / still there for rule d."""
    rec = read_origin(name)
    if not rec or rec[0] != p["old"]: return "what the name belongs to has changed since"
    if not os.path.isfile(p["new"]): return f"{tilde(p['new'])} is gone"
    if os.path.isfile(p["old"]) != (p["rule"] == "d"): return f"{tilde(p['old'])} is back" if p["rule"] == "c" else f"{tilde(p['old'])} is gone"
    return None


def pending_names():
    """Names with a decision waiting that still applies."""
    try: names = sorted(os.listdir(f"{CONF}/pending"))
    except OSError: return []
    return [n for n in names if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", n) and (p := read_pending(n)) and not pending_stale(n, p)]


def hold(name, old, real, rule):
    """Neither accept nor refuse for good: write pending/NAME, send a notification, and stop without starting the program."""
    if rule == "d":
        why = f"which still exists, and now {tilde(real)} wants the same name: a different program"
        tail = f"A different program: use --name=OTHER for a separate home. If it really is the same one: sbx rebind {name}"
    else:
        why = f"which is gone; now it is {tilde(real)}"
        tail = f"Same program: sbx rebind {name}. Not the same: sbx rebind --dismiss {name}, then --name=OTHER for a home of its own"
    try:
        write_pending(name, old, real, rule, f"was first run from {tilde(old)}, {why}")
        notify_click(f"{name} is waiting for your decision. Open sbx-gui and use the buttons on its page, or run: sbx rebind {name}",
                     f"{name} is waiting for your decision. Click to open sbx, which has a button to decide.", "Open sbx", [name])
    except OSError:
        tail = f"sbx can't save a decision in {tilde(CONF)}. The same program: run it once with --rebind. A different one: --name=OTHER"
    die(f"'{name}' was first run from {tilde(old)}, {why}. They would share the settings and private home of '{name}'.\n      {tail}")


def sha_now(path, known):
    """The SHA-256 of PATH as it is now: the recorded one while size and mtime still match, else hashed again. None when unreadable."""
    try: st = os.stat(path)
    except OSError: return None
    if known and known[:2] == (st.st_size, st.st_mtime_ns): return known[2]
    return (fingerprint(path) or (0, 0, None))[2]


def carry_data(name, old, real):
    """A program that writes beside its own file (settings, saves, a chosen disc) finds that in its private home under the folder the file is in:
    HOME/Games/x/save.dat is HOMES/NAME/Games/x/save.dat. After a move that is another folder, and everything it stored looks lost. When OLD is
    gone, move what is stored under its folder to the folder of REAL, inside the private home only: never over something that is already there,
    never through a link (the program can plant links in its own home), and not for a file outside your home. Says so, in one line. Returns
    how many entries moved."""
    if not old or os.path.lexists(old): return 0                    # still there: a copy, the original may be in use
    a, b = os.path.dirname(old), os.path.dirname(real)
    if a == b or not (a.startswith(HOME + "/") and b.startswith(HOME + "/")): return 0
    home = os.path.realpath(HOMES) + "/" + name
    if os.path.realpath(f"{HOMES}/{name}") != home: return 0          # the private home is a plain folder, not a link to somewhere
    src, dst = f"{home}/{a[len(HOME) + 1:]}", f"{home}/{b[len(HOME) + 1:]}"
    if os.path.realpath(src) != src or os.path.realpath(dst) != dst or not os.path.isdir(src): return 0      # no link anywhere on the way
    moved = 0
    try:
        for e in sorted(os.listdir(src)):
            s, d = f"{src}/{e}", f"{dst}/{e}"
            if dst == s or dst.startswith(s + "/") or os.path.lexists(d): continue         # the new folder itself, or taken
            os.makedirs(dst, mode=0o700, exist_ok=True)
            os.rename(s, d); moved += 1
    except OSError:
        pass
    if moved: warn(f"'{name}': moved {moved} item{'s' if moved != 1 else ''} it had stored beside its old file, from {tilde(a)} to {tilde(b)}, in its private home")
    return moved


def follow(name, real, fp, old):
    """NAME now belongs to REAL, which moved there from OLD: record it, and take along what the program stored beside the old file."""
    write_origin(name, real, fp, old)
    carry_data(name, old, real)


def check_origin(name, real, rebind=False):
    """A program name owns settings (apps/NAME.conf) and a private home. Remember which file first used the name, and when a file
    shows up under it from another path, decide in this order, the first match winning:
      a. the same content (SHA-256): the same program moved or copied. Accepted, quietly.
      b. other content, the old path gone, the same file name: moved, then updated. Accepted, with one line saying so.
      c. other content, the old path gone, another file name: unclear. Asked on a terminal, otherwise left pending.
      d. other content, the old path still there: two programs want one name. Refused, left pending.
    Pending (c, d) means: pending/NAME is written, a notification is sent, nothing starts; `sbx rebind NAME` or sbx-gui decides.
    Moves between system folders (updates) are always fine; rebind=True accepts the new file. A run from the recorded path is never held
    up by what the file contains: a changed size or mtime only refreshes the record (updates and self-patching games must work)."""
    if "\n" in real: die(f"'{name}': a file name with a line break in it can't be recorded ({real!r}). Rename the file")
    rec = read_origin(name)
    old = rec[0] if rec else None
    if old == real: return refresh_origin(name, real, rec)
    system = lambda x: x.startswith(SYSTEM_PROGRAMS)
    if old is None or rebind or (system(old) and system(real)):
        follow(name, real, fingerprint(real), old)
        return clear_pending(name)
    new = fingerprint(real)
    exists = os.path.isfile(old)
    known = rec[1]
    old_sha = sha_now(old, known) if exists else known[2] if known else None      # the old file may have been updated since it was recorded
    if new and new[2] and new[2] == old_sha:                       # a
        follow(name, real, new, old)
        return clear_pending(name)
    if exists: hold(name, old, real, "d")                          # d
    if os.path.basename(old) == os.path.basename(real):            # b
        follow(name, real, new, old)
        clear_pending(name)
        return warn(f"'{name}' moved from {tilde(old)} to {tilde(real)}")
    if sys.stdin.isatty():                                         # c
        try: answer = input(f"'{name}' was first run from {tilde(old)}, which is gone. Now it is {tilde(real)}. Same program? [y/N] ")
        except EOFError: answer = ""
        if answer.strip().lower() in ("y", "yes"):
            follow(name, real, new, old)
            return clear_pending(name)
        die("not accepted. A different program: use --name=OTHER for a separate home. The same one after all: run it once with --rebind")
    hold(name, old, real, "c")


def refresh_origin(name, real, rec):
    """The program runs from the path it was recorded with. Whatever it contains now, it starts; the record only follows it: a missing
    line 2 (older version) is added, a changed size or mtime is hashed again. A record that can't be written is no reason to stop."""
    clear_pending(name)                                            # the file went back to where it was recorded
    try:
        st = os.stat(real)
        if rec[1] and rec[1][:2] == (st.st_size, st.st_mtime_ns): return
        fp = fingerprint(real)
        if fp and fp[2]: write_origin(name, real, fp, rec[2])
    except OSError:
        pass
