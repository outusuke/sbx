#| 30_system: Things outside the window: the app-menu entry and window icon, finding sbx, asking `sbx schema`.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

MENU_DESKTOP = HOME + "/.local/share/applications/org.sbx.Gui.desktop"
MENU_ICON = HOME + "/.local/share/icons/hicolor/scalable/apps/org.sbx.Gui.svg"
MENU_ENTRY = f"""[Desktop Entry]
Type=Application
Name=sbx
GenericName=Sandbox Settings
Comment=Edit the settings of programs run in the sbx sandbox
Exec={os.path.realpath(sys.argv[0]) if os.path.basename(sys.argv[0]) == "sbx-gui" else "/usr/local/bin/sbx-gui"}
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


def set_menu_entry(on):
    """Add or remove sbx-gui's own launcher in the user's app menu (no root needed)."""
    if on:
        os.makedirs(os.path.dirname(MENU_DESKTOP), exist_ok=True)
        os.makedirs(os.path.dirname(MENU_ICON), exist_ok=True)
        for path, text in ((MENU_ICON, MENU_SVG), (MENU_DESKTOP, MENU_ENTRY)):
            with open(path + ".tmp", "w") as f: f.write(text)
            os.replace(path + ".tmp", path)
    else:
        for path in (MENU_DESKTOP, MENU_ICON):
            try: os.unlink(path)
            except OSError: pass
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", os.path.dirname(MENU_DESKTOP)], stderr=subprocess.DEVNULL, timeout=10)


def launcher_is_ours(name):
    """True if ~/.local/share/applications/NAME.desktop was made by `sbx desktop`."""
    try:
        with open(f"{HOME}/.local/share/applications/{name}.desktop") as f: return "X-Sbx-Sandboxed=true" in f.read()
    except OSError: return False


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
