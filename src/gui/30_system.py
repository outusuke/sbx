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


def covered_programs():
    """Names with an sbx link in ~/.local/bin or an sbx launcher in ~/.local/share/applications: somebody set those up on purpose."""
    names, d = set(), f"{HOME}/.local/bin"
    try: names.update(program_name(n) for n in os.listdir(d) if os.path.islink(f"{d}/{n}") and os.path.basename(os.readlink(f"{d}/{n}")) == "sbx")
    except OSError: pass
    for p in glob.glob(f"{HOME}/.local/share/applications/*.desktop"):
        try:
            with open(p, errors="replace") as f: text = f.read()
        except OSError: continue
        if "X-Sbx-Sandboxed=true" not in text: continue
        stem = os.path.basename(p)[:-8]
        names.update(program_name(x) for x in (launcher_program(text), stem, stem.rsplit(".", 1)[-1]) if x)
    return names


def program_groups():
    """(the programs you set up, the ones only run from the terminal). Set up: a settings file, an sbx link or launcher, or running now.
    The others are just a private home that a run created, as for `sbx curl ...` typed once."""
    try: conf = {f[:-5] for f in os.listdir(APPS) if f.endswith(".conf")}
    except OSError: conf = set()
    chosen = conf | covered_programs()
    names = app_names()
    setup = [n for n in names if n in chosen or program_running(n)]
    return setup, [n for n in names if n not in setup]


def unused_programs(others):
    """Of the terminal-only programs, those whose private home is nearly empty."""
    return [n for n in others if os.path.isdir(f"{HOMES}/{n}") and home_is_small(n)]


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
