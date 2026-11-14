#| 30_system: Things outside the window: the app-menu entry and window icon, finding sbx, asking `sbx schema`.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

MENU_DESKTOP = HOME + "/.local/share/applications/org.sbx.Gui.desktop"
MENU_ICON = HOME + "/.local/share/icons/hicolor/scalable/apps/org.sbx.Gui.svg"
MENU_ENTRY = """[Desktop Entry]
Type=Application
Name=sbx
GenericName=Sandbox Settings
Comment=Edit the settings of programs run in the sbx sandbox
Exec=@EXEC@
Icon=org.sbx.Gui
Terminal=false
StartupNotify=true
Categories=System;Settings;Security;GTK;
Keywords=sandbox;bubblewrap;firejail;permissions;
"""
MENU_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <rect x="8" y="8" width="112" height="112" rx="26" fill="#3584e4"/>
  <path d="M64 24 L94 36 V62 C94 80 81 94 64 102 C47 94 34 80 34 62 V36 Z" fill="#fff" opacity=".95"/>
  <rect x="50" y="58" width="28" height="22" rx="4" fill="#3584e4"/>
  <path d="M55 58 V51 a9 9 0 0 1 18 0 V58" fill="none" stroke="#3584e4" stroke-width="5"/>
</svg>
"""


RUNNING = HOME + "/.local/state/sbx/running"      # written by sbx: RUNNING/PID holds "NAME\nSTARTTIME"


def live_sbx(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f: return b"sbx" in f.read()
    except OSError:
        return False


def program_running(name):
    """True if an sbx process is running NAME. A file counts only if the process under that PID is the one that wrote it
    (same start time), so a reused PID doesn't make a program look busy."""
    try: pids = [f for f in os.listdir(RUNNING) if f.isdigit()]
    except OSError: return False
    for pid in pids:
        try:
            with open(f"{RUNNING}/{pid}") as f: who, _, start = f.read().partition("\n")
            with open(f"/proc/{pid}/stat", "rb") as f: now = f.read().rsplit(b")", 1)[1].split()[19].decode()
        except (OSError, IndexError, UnicodeDecodeError):
            continue
        if who.strip() == name and start.strip() == now and live_sbx(pid): return True
    return False


def use_window_icon():
    """Give the window its icon even when 'Show sbx in the app menu' is off: the SVG goes into a private icon folder in the
    cache (nothing in your own icon folders) and becomes the default icon of every window. A dock on Wayland still needs the
    menu entry to show it, because it looks the icon up through the launcher file."""
    try:
        base = (os.environ.get("XDG_CACHE_HOME") or HOME + "/.cache") + "/sbx/icons"
        svg = base + "/hicolor/scalable/apps/org.sbx.Gui.svg"
        if not os.path.isfile(svg):
            os.makedirs(os.path.dirname(svg), exist_ok=True)
            with open(svg + ".tmp", "w") as f: f.write(MENU_SVG)
            os.replace(svg + ".tmp", svg)
        display = Gdk.Display.get_default()
        if display is None: return
        Gtk.IconTheme.get_for_display(display).add_search_path(base)
        Gtk.Window.set_default_icon_name("org.sbx.Gui")
    except (OSError, GLib.Error):
        pass


def menu_entry_exists():
    return os.path.isfile(MENU_DESKTOP)


def menu_exec():
    """The installed sbx-gui the launcher should start. Only a root-owned copy that nobody else can write to counts, so a
    program running as you can't swap the launcher's target for its own code; a copy in your home or the download folder
    never does."""
    for d in ("/usr/local/bin", "/usr/bin"):
        path = d + "/sbx-gui"
        try: st = os.stat(path)
        except OSError: continue
        if st.st_uid == 0 and not st.st_mode & 0o022: return path
    raise OSError(errno.ENOENT, "Install sbx-gui first: sudo install -m755 sbx-gui /usr/local/bin/")


def set_menu_entry(on):
    """Add or remove sbx-gui's own launcher in the user's app menu (the launcher file needs no root)."""
    if on:
        entry = MENU_ENTRY.replace("@EXEC@", menu_exec())
        os.makedirs(os.path.dirname(MENU_DESKTOP), exist_ok=True)
        os.makedirs(os.path.dirname(MENU_ICON), exist_ok=True)
        for path, text in ((MENU_ICON, MENU_SVG), (MENU_DESKTOP, entry)):
            with open(path + ".tmp", "w") as f: f.write(text)
            os.replace(path + ".tmp", path)
    else:
        for path in (MENU_DESKTOP, MENU_ICON):
            try: os.unlink(path)
            except OSError: pass
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", os.path.dirname(MENU_DESKTOP)], stderr=subprocess.DEVNULL, timeout=10)


def launcher_is_ours(name):
    """True if a launcher in ~/.local/share/applications made by `sbx desktop` belongs to NAME: NAME.desktop, *.NAME.desktop (the name
    kept from the system launcher, e.g. org.mozilla.firefox for firefox), or one that starts a program called NAME (google-chrome.desktop
    for google-chrome-stable)."""
    d = f"{HOME}/.local/share/applications"
    for path in [f"{d}/{name}.desktop"] + sorted(glob.glob(f"{d}/*.{name}.desktop")) + sorted(glob.glob(f"{d}/*.desktop")):
        try:
            with open(path, errors="replace") as f: text = f.read()
        except OSError: continue
        if "X-Sbx-Sandboxed=true" not in text: continue
        if os.path.basename(path) == f"{name}.desktop" or path.endswith(f".{name}.desktop") or launcher_program(text) == name: return True
    return False


def launcher_program(text):
    """The program the main Exec= line of a .desktop file starts, past a leading sbx (same rule as sbx's own launcher matching)."""
    group = ""
    for line in text.splitlines():
        if line.startswith("["): group = line
        elif group == "[Desktop Entry]" and line.startswith("Exec="):
            try: w = shlex.split(line[5:])
            except ValueError: return None
            if w and os.path.basename(w[0]) == "sbx": w = w[1:]
            return os.path.basename(w[0]) if w else None
    return None


def link_is_ours(name):
    """True if ~/.local/bin/NAME is a link made by `sbx link`."""
    p = f"{HOME}/.local/bin/{name}"
    try: return os.path.islink(p) and os.path.basename(os.readlink(p)) == "sbx"
    except OSError: return False


UNUSED_BYTES = 64 * 1024        # same rule as `sbx clean --unused`


def home_is_small(name, limit=UNUSED_BYTES):
    """True if the private home holds at most LIMIT bytes in files (links are not followed; a folder that can't be read counts as not small)."""
    total, unreadable = 0, []
    for d, _dirs, files in os.walk(f"{HOMES}/{name}", onerror=unreadable.append):
        for f in files:
            try: total += os.lstat(f"{d}/{f}").st_size
            except OSError: unreadable.append(f)
            if total > limit or unreadable: return False
    return not unreadable


def linked_programs():
    """Names with an sbx link in ~/.local/bin."""
    d = f"{HOME}/.local/bin"
    try: return {program_name(n) for n in os.listdir(d) if os.path.islink(f"{d}/{n}") and os.path.basename(os.readlink(f"{d}/{n}")) == "sbx"}
    except OSError: return set()


def launcher_programs():
    """Names an sbx launcher in ~/.local/share/applications starts or is called after."""
    names = set()
    for p in glob.glob(f"{HOME}/.local/share/applications/*.desktop"):
        try:
            with open(p, errors="replace") as f: text = f.read()
        except OSError: continue
        if "X-Sbx-Sandboxed=true" not in text: continue
        stem = os.path.basename(p)[:-8]
        names.update(program_name(x) for x in (launcher_program(text), stem, stem.rsplit(".", 1)[-1]) if x)
    return names


def covered_programs():
    """Names with an sbx link in ~/.local/bin or an sbx launcher in ~/.local/share/applications: somebody set those up on purpose."""
    return linked_programs() | launcher_programs()


# ---- where a program comes from (origins/NAME, written by sbx; the same reading rules as sbx's read_origin) ----
ORIGIN_META = re.compile(r"(\d+) (\d+) ([0-9a-f]{64})")


def read_origin(name):
    """(path, (size, mtime_ns, sha256) or None, moved-from path or None) from origins/NAME, or None when nothing is recorded."""
    try:
        with open(f"{CONF}/origins/{name}", errors="replace") as f: lines = f.read().split("\n")
    except OSError:
        return None
    path = lines[0].strip()
    if not path: return None
    m = ORIGIN_META.fullmatch(lines[1].strip()) if len(lines) > 1 else None
    moved = lines[2][5:].strip() if len(lines) > 2 and lines[2].startswith("from ") else None
    return path, ((int(m[1]), int(m[2]), m[3]) if m else None), moved or None


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


def source_parts(s):
    """The pieces of a source line, as shown in a list row: 'System · /usr/bin', 'Downloaded · ~/Downloads/Crash', 'Missing: ~/old/path',
    then 'moved from ...' (or 'copied from ...' while the original is still there)."""
    parts = [f"Missing: {tilde(s['path'])}" if s["missing"] else f"System · {os.path.dirname(s['path'])}" if s["kind"] == "System"
             else f"{s['kind']} · {tilde(s['path'])}"]
    if s["moved"]: parts.append(f"{'copied' if s['moved_exists'] else 'moved'} from {tilde(s['moved'])}")
    return parts


def source_tags(name, linked=None, menu=None):
    """'link' and 'menu' for the ways a program is started through sbx besides the window (the two sets may be passed in, to look them up once for a whole list)."""
    linked = linked_programs() if linked is None else linked
    menu = launcher_programs() if menu is None else menu
    return [t for t, names in (("link", linked), ("menu", menu)) if name in names]


# ---- decisions waiting for you (pending/NAME, written by sbx; same rules as sbx's read_pending and pending_stale) ----
def read_pending(name):
    """{old, new, rule, reason} from pending/NAME, or None."""
    try:
        with open(f"{CONF}/pending/{name}", errors="replace") as f: text = f.read()
    except OSError:
        return None
    d = {}
    for line in text.split("\n"):
        k, eq, v = line.partition("=")
        if eq and k in ("old", "new", "rule", "reason"): d[k] = v
    return d if d.get("old") and d.get("new") and d.get("rule") in ("c", "d") else None


def pending_stale(name, p):
    """Why the decision P no longer applies, or None while it does."""
    rec = read_origin(name)
    if not rec or rec[0] != p["old"]: return "what the name belongs to has changed since"
    if not os.path.isfile(p["new"]): return f"{tilde(p['new'])} is gone"
    if os.path.isfile(p["old"]) != (p["rule"] == "d"): return f"{tilde(p['old'])} is back" if p["rule"] == "c" else f"{tilde(p['old'])} is gone"
    return None


def pending_decisions():
    """{name: decision} for every decision still waiting (one the situation has moved on from is left out)."""
    try: names = sorted(os.listdir(f"{CONF}/pending"))
    except OSError: return {}
    return {n: p for n in names if NAME_RE.match(n) and (p := read_pending(n)) and not pending_stale(n, p)}


def program_groups():
    """(the programs you set up, the ones only run from the terminal). Set up: a settings file, an sbx link or launcher, or running now.
    The others are just a private home that a run created, as for `sbx curl ...` typed once; System ones come first there, Downloaded last."""
    try: conf = {f[:-5] for f in os.listdir(APPS) if f.endswith(".conf")}
    except OSError: conf = set()
    chosen = conf | covered_programs()
    names = app_names()
    setup = [n for n in names if n in chosen or program_running(n)]
    def rank(n):
        s = origin_source(n)
        return SOURCE_RANK.get(s["kind"], 1) if s else 1, n
    return setup, sorted((n for n in names if n not in setup), key=rank)


def row_subtitle(name, waiting=False, linked=None, menu=None, show_running=True):
    """The second line of a program's row: running (unless the row shows that itself), waiting for a decision, where it comes from, how else it starts, then its settings."""
    s = origin_source(name)
    parts = (["Running now"] if show_running and program_running(name) else []) + (["Waiting for your decision"] if waiting else [])
    return " · ".join(parts + (source_parts(s) if s else []) + source_tags(name, linked, menu) + [summary(name)])


def unused_programs(others):
    """Of the terminal-only programs, those whose private home is nearly empty."""
    return [n for n in others if os.path.isdir(f"{HOMES}/{n}") and home_is_small(n)]


def in_path(name):
    """True if PATH holds a program of that name, not counting the links `sbx link` makes (they are how sbx starts, not the program)."""
    for d in os.environ.get("PATH", "").split(":"):
        p = os.path.join(d, name) if d else ""
        if p and os.path.isfile(p) and os.access(p, os.X_OK) and not (os.path.islink(p) and os.path.basename(os.readlink(p)) == "sbx"): return True
    return False


def start_command(name):
    """(the sbx command that starts NAME from the window, or None, and why not). A program in your PATH starts by its name. One that is not (a
    downloaded game) starts from the file sbx recorded for it, as `sbx open` does when you use Open With; a name that was given with --name
    keeps it. When that file is gone there is nothing to start: run it once from its new place and sbx recognises it (see check_origin)."""
    if in_path(name): return [sbx_cmd(), name], None
    rec = read_origin(name)
    if not rec: return None, f"{name} is not in your PATH, and sbx has not run it from a file yet. Open it once with Open With, Run in sbx."
    path = rec[0]
    if not os.path.isfile(path): return None, f"The file {tilde(path)} is gone. If you moved it, start it once from its new place (Open With, Run in sbx): sbx recognises a moved program."
    if program_name(os.path.basename(path)) == name: return [sbx_cmd(), "open", path], None
    return [sbx_cmd(), f"--name={name}", path], None


def sbx_cmd():
    return shutil.which("sbx") or "/usr/local/bin/sbx"


def load_schema():
    """Ask sbx which switches and secret folders exist, so this window can't drift from the command line."""
    global GROUPS, SWITCHES, SENSITIVE
    try:
        r = subprocess.run([sbx_cmd(), "schema"], capture_output=True, text=True, timeout=10)
        sch = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return
    known = set(sch.get("switches", {}))
    if not known: return
    SENSITIVE = list(sch.get("sensitive", SENSITIVE))
    groups = [(t, [row for row in rows if row[0] in known]) for t, rows in GROUPS]
    missing = [k for k in sch["switches"] if k not in {row[0] for _, rows in groups for row in rows}]
    if missing: groups.append(("Other", [(k, k.replace("_", " ").capitalize(), "Setting from sbx", bool(sch["switches"][k])) for k in missing]))
    GROUPS = [(t, rows) for t, rows in groups if rows]
    SWITCHES = {k: (t, s, d) for _, rows in GROUPS for k, t, s, d in rows}


def folder_problem(name):
    """Why the switch "Start in the program's own folder" shares nothing for this program (its file sits in your home, Downloads or
    another general folder, or in a hidden folder), or None. Same rule as own_folder() in sbx."""
    rec = read_origin(name)
    path = os.path.realpath(rec[0]) if rec and os.path.isfile(rec[0]) else (os.path.realpath(shutil.which(name)) if shutil.which(name) else None)
    if not path: return None
    d = os.path.dirname(path)
    generic = {HOME} | {f"{HOME}/{n}" for n in ("Downloads", "Desktop", "Documents", "Pictures", "Music", "Videos", "Public", "Templates")}
    try:
        with open(f"{os.environ.get('XDG_CONFIG_HOME') or HOME + '/.config'}/user-dirs.dirs") as f:
            for m in re.finditer(r'^XDG_\w+_DIR="(?:\$HOME|' + re.escape(HOME) + r')/?([^"]*)"', f.read(), re.M): generic.add(f"{HOME}/{m[1]}".rstrip("/"))
    except OSError:
        pass
    if d in generic: return f"{tilde(d)} holds all sorts of things, so it is not shared. Move the program into a folder of its own."
    if d.startswith(HOME + "/") and d[len(HOME) + 1:].split("/")[0].startswith("."): return f"{tilde(d)} is a hidden folder, so it is not shared."
    if not (d.startswith(HOME + "/") or d.startswith("/tmp/")): return f"{d} is not inside your home or /tmp, so it is not shared."
    return None
