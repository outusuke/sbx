#| 85_commands: Convenience commands around the sandbox: show, clean, desktop launchers, open, doctor. Nothing here builds the sandbox.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

def cmd_show(a):
    o, a = parse_options(a)
    if len(a) != 1: die("usage: sbx show [options] NAME")
    name = clean(o.get("name") or os.path.basename(a[0]))
    cfg, src = effective(name, o)
    home = f"{HOMES}/{name}"
    print(f"{name}   private home {tilde(home)} ({'exists' if os.path.isdir(home) else 'not created yet'})")
    for k in SCALARS: print(f"  {k:<16} = {int(cfg[k])}   [{src[k]}]")
    for k in LIMITS:
        if cfg[k]: print(f"  {k:<16} = {cfg[k]}   [{src[k]}]")
    for k in LISTS:
        for v in cfg[k]: print(f"  {k:<16} = {v}   [{src[(k, v)]}]")
    for k, flag in (("whitelist", "whitelist"), ("read_only", "read_only")):
        for v in o.get(flag, []): print(f"  {k:<16} = {v}   [flag]")


def cmd_clean(a):
    yes = "--yes" in a
    a = [x for x in a if x != "--yes"]
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx clean [--yes] NAME")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    name = a[0]
    home, mid = f"{HOMES}/{name}", f"{CONF}/ids/{name}"
    if clean(name) != name or not (os.path.isdir(home) or os.path.exists(mid)): die(f"nothing to clean for '{name}'")
    if running(name): die(f"'{name}' is running. Stop it first: sbx stop {name}")
    real = os.path.realpath(home)
    if os.path.islink(home) or not real.startswith(os.path.realpath(HOMES) + "/"): die(f"{tilde(home)} is not a plain private home")
    if not yes:
        if not sys.stdin.isatty(): die("not asking without a terminal; add --yes")
        if input(f"Delete {tilde(home)} and the machine-id of '{name}'? Settings stay. [y/N] ").strip().lower() not in ("y", "yes"):
            die("nothing deleted", 0)

    def unlock(fn, p, _exc):                     # programs sometimes leave read-only folders behind
        os.chmod(os.path.dirname(p), 0o700)
        if not os.path.islink(p): os.chmod(p, 0o700)         # never chmod through a link the program planted
        fn(p)
    shutil.rmtree(real, onerror=unlock)
    for gone in (mid, f"{CONF}/origins/{name}"):
        try: os.unlink(gone)
        except OSError: pass
    print(f"removed {tilde(home)} and its machine-id")


def cmd_reset(a):
    """sbx reset [--yes] NAME | --default | --all: delete settings files so the built-in defaults apply again. Data stays."""
    yes = "--yes" in a
    a = [x for x in a if x != "--yes"]
    if len(a) != 1 or (a[0].startswith("-") and a[0] not in ("--default", "--all")): die("usage: sbx reset [--yes] NAME | --default | --all")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    apps = f"{CONF}/apps"
    if a[0] == "--all":
        files = [f"{CONF}/default.conf"]
        try: files += [f"{apps}/{n}" for n in sorted(os.listdir(apps)) if n.endswith(".conf") and not n.startswith(".")]
        except OSError: pass
    elif a[0] == "--default": files = [f"{CONF}/default.conf"]
    else:
        if clean(a[0]) != a[0]: die(f"'{a[0]}' is not a program name")
        files = [f"{apps}/{a[0]}.conf"]
    files = [p for p in files if os.path.lexists(p)]
    if not files: print("Nothing to reset: already using the built-in defaults."); return
    if not yes:
        if not sys.stdin.isatty(): die("not asking without a terminal; add --yes")
        print("Delete these settings files? Private homes, machine-ids and menu launchers stay.")
        for p in files: print(f"  {tilde(p)}")
        if input("Reset? [y/N] ").strip().lower() not in ("y", "yes"): die("nothing deleted", 0)
    for p in files: os.unlink(p)
    print(f"deleted {len(files)} settings file{'s' if len(files) != 1 else ''}; the built-in defaults apply again")


def cmd_list(a):
    """Every program sbx knows (a private home or a settings file, or running now), and whether it is running."""
    if a: die("usage: sbx list")
    names = set()
    for d, suffix in ((HOMES, ""), (f"{CONF}/apps", ".conf")):
        try: names.update(n[:len(n) - len(suffix)] for n in os.listdir(d) if n.endswith(suffix) and not n.startswith("."))
        except OSError: pass
    live = {p: pid_app(p) for p in running()}
    names.update(live.values())
    if not names: print("No programs yet. Start one with: sbx PROGRAM"); return
    for n in sorted(names):
        k = sum(1 for v in live.values() if v == n)
        print(f"{n:32} " + (f"running ({k})" if k else "idle"))


def cmd_stop(a):
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx stop NAME")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    if not running(a[0]): die(f"'{a[0]}' is not running")
    n = stop_program(a[0])
    print(f"stopping {a[0]}" + (f" ({n} processes)" if n > 1 else ""))


def rewrite_desktop(text, sbx):
    """A copy of a .desktop file whose launchers go through sbx. Returns (new text, list of warnings)."""
    out, notes, group = [], [], ""
    q = f'"{sbx}"' if " " in sbx else sbx
    for line in text.splitlines():
        if line.startswith("["): group = line
        if line.startswith("Exec="):
            rest = line[5:]
            first = rest.split(None, 1)[0] if rest.strip() else ""
            if first in ("env", "sh", "bash") or "=" in first:
                notes.append(f"'{line}' starts with {first}: sbx would name the program '{first}'. Edit the file by hand.")
            line = f"Exec={q} {rest}"
        elif line.startswith("DBusActivatable="): line = "DBusActivatable=false"       # D-Bus activation would bypass sbx
        elif line.startswith("Name=") and group == "[Desktop Entry]": line += " (sbx)"
        out.append(line)
        if line == "[Desktop Entry]": out.append("X-Sbx-Sandboxed=true")
    return "\n".join(out) + "\n", notes


def cmd_menu(install):
    """sbx desktop --install-menu: put sbx-gui in the app menu. The launcher is written by the installed sbx-gui itself, and only
    a root-owned copy counts (see menu_exec in sbx-gui), so nothing you can write to is ever started from the menu."""
    if not install:
        gone = False
        for p in (f"{HOME}/.local/share/applications/org.sbx.Gui.desktop", f"{HOME}/.local/share/icons/hicolor/scalable/apps/org.sbx.Gui.svg"):
            try: os.unlink(p); gone = True
            except OSError: pass
        if gone and shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", f"{HOME}/.local/share/applications"], stderr=subprocess.DEVNULL)
        print("removed sbx from the app menu" if gone else "nothing to remove")
        return
    for d in ("/usr/local/bin", "/usr/bin"):
        try: st = os.stat(d + "/sbx-gui")
        except OSError: continue
        if st.st_uid == 0 and not st.st_mode & 0o022: sys.exit(subprocess.run([d + "/sbx-gui", "--install-menu"]).returncode)
    die("sbx-gui is not installed (a root-owned copy is needed). Install it first: sudo install -m755 sbx-gui /usr/local/bin/")


def cmd_desktop(a):
    if a in (["--install-opener"], ["--remove-opener"]): return cmd_opener(a[0] == "--install-opener")
    if a in (["--install-menu"], ["--remove-menu"]): return cmd_menu(a[0] == "--install-menu")
    remove = "--remove" in a
    a = [x for x in a if x != "--remove"]
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx desktop NAME   |   sbx desktop --remove NAME   (NAME = the launcher's id, e.g. firefox or org.mozilla.firefox)")
    ident = a[0][:-8] if a[0].endswith(".desktop") else a[0]
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", ident) or ident.startswith("."): die(f"'{ident}' is not a launcher id")
    mine = f"{HOME}/.local/share/applications/{ident}.desktop"
    if remove:
        if os.path.exists(mine):
            try:
                with open(mine) as f: ours = "X-Sbx-Sandboxed=true" in f.read()
            except OSError: die(f"{tilde(mine)} doesn't exist")
            if not ours: die(f"{tilde(mine)} was not made by sbx; not removing it")
            os.unlink(mine); print(f"removed {tilde(mine)}"); return
        # adding saves the copy under the system launcher's own name, which can be org.mozilla.firefox for NAME firefox
        found = []
        for p in sorted(glob.glob(f"{HOME}/.local/share/applications/*.{ident}.desktop")):
            try:
                with open(p) as f:
                    if "X-Sbx-Sandboxed=true" in f.read(): found.append(p)
            except OSError: pass
        if not found: die(f"{tilde(mine)} doesn't exist, and no launcher made by sbx is called *.{ident}.desktop")
        for p in found: os.unlink(p); print(f"removed {tilde(p)}")
        return
    dirs = [d for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    cands = [f"{d}/applications/{ident}.desktop" for d in dirs]
    cands += [p for d in dirs for p in sorted(glob.glob(f"{d}/applications/*.{ident}.desktop"))]
    found = next((p for p in cands if os.path.isfile(p)), None)
    if not found: die(f"no launcher called {ident}.desktop in {', '.join(d + '/applications' for d in dirs)}")
    with open(found) as f: text, notes = rewrite_desktop(f.read(), shutil.which("sbx") or os.path.realpath(sys.argv[0]))
    dest = f"{HOME}/.local/share/applications/{os.path.basename(found)}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f: f.write(text)
    for n in notes: warn(n)
    print(f"wrote {tilde(dest)}: the launcher now starts the program through sbx.\nUndo with: sbx desktop --remove {ident}")


# ---------------------------------------------------------------- opening downloaded programs
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


def opener_folder(real):
    """The folder a program brings along when you open it with sbx (its game data, saves...): the folder it sits in,
    unless that is your home, a hidden folder or a general-purpose one like Downloads. None then."""
    d = os.path.dirname(real)
    if d in generic_folders(): return None
    if d.startswith(HOME + "/"):
        return None if d[len(HOME) + 1:].split("/")[0].startswith(".") else d
    return d if d.startswith("/tmp/") else None


def notify(title, body=""):
    """A desktop notification (there is no terminal to print to when started from a file manager). Also printed."""
    warn(f"{title}: {body}" if body else title)
    ns = shutil.which("notify-send")
    if ns:
        try: subprocess.run([ns, "-a", "sbx", "-i", "dialog-error", title, body[:400]], timeout=5, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired): pass


def failure_reason(text, rc):
    """The last few lines the program printed, as one line for a notification. Progress meters (\\r, 45%) are collapsed and
    sbx's own notices ("sbx: ...") are left out, so they don't pass for the reason; they stay when nothing else was printed."""
    segs = [re.sub(r"^(\s*\d+%)+\s*", "", x).strip() for x in re.split(r"[\r\n\x08]+", text)]
    segs = [x for x in segs if x]
    segs = [x for x in segs if not x.startswith("sbx:")] or segs
    segs = [x for i, x in enumerate(segs) if i == 0 or x != segs[i - 1]]      # a repeated line says nothing new
    return " ".join(segs[-3:]) or f"exit code {exit_code(rc)}"


def cmd_open(a):
    """sbx open FILE: what 'Open with' calls. A program with no settings of its own gets: its folder (read-write, for saves
    and game data), game controllers, and the usual defaults. A program with a settings file gets exactly that file."""
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx open FILE")
    f = os.path.abspath(a[0])
    name = clean(os.path.basename(f))
    journal_tag(name)
    if not os.path.isfile(f): notify(f"Can't open {name}", "no such file"); sys.exit(1)
    flags = []
    if not os.access(f, os.X_OK) and not elf_interpreter(f):
        notify(f"Can't run {name}", "it is not executable and not a dynamically linked program; run chmod +x on it"); sys.exit(1)
    if not os.path.exists(f"{CONF}/apps/{name}.conf"):
        flags.append("--input")
        folder = opener_folder(os.path.realpath(f))
        if folder:
            os.chdir(folder); flags.append("--here")
    me = os.path.realpath(sys.argv[0]) if os.path.isfile(sys.argv[0]) else shutil.which("sbx")
    if not me: notify("sbx not found"); sys.exit(1)
    err, t0 = tempfile.TemporaryFile(), time.monotonic()
    rc = subprocess.run([sys.executable, me, *flags, f], stderr=err, env={**os.environ, "SBX_NO_POPUP": "1"}).returncode   # we show the popup, with the reason
    err.seek(0)
    text = err.read().decode(errors="replace")
    sys.stderr.write(text)
    if rc != 0 and time.monotonic() - t0 < 15:                      # it never really started
        why = failure_reason(text, rc)
        warn(f"{name} didn't start: {why}")
        notify_failure(name, why)                                  # clicking it opens the log, like a start from the menu
    sys.exit(exit_code(rc))


OPENER_ID = "sbx-run.desktop"
OPENER_MIMES = ("application/x-executable", "application/x-pie-executable")      # offered, and the default for files without the execute bit
OPENER_LISTED = ("application/x-shellscript",)                                    # only offered under Open With (self-extracting installers); your text editor stays the default


def cmd_opener(install):
    dest = f"{HOME}/.local/share/applications/{OPENER_ID}"
    mimeapps = f"{os.environ.get('XDG_CONFIG_HOME') or HOME + '/.config'}/mimeapps.list"
    if install:
        me = shutil.which("sbx") or os.path.realpath(sys.argv[0])
        q = f'"{me}"' if " " in me else me
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Run in sbx\nComment=Run a downloaded program in the sbx sandbox\n"
                    f"Exec={q} open %f\nMimeType={';'.join(OPENER_MIMES + OPENER_LISTED)};\nTerminal=false\nNoDisplay=true\nCategories=System;\n"
                    "X-Sbx-Opener=true\n")
        xm = shutil.which("xdg-mime")
        if xm: subprocess.run([xm, "default", OPENER_ID, *OPENER_MIMES])
        else: warn("xdg-mime not found: \"Run in sbx\" is in Open With, but not the default")
        if shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", os.path.dirname(dest)], stderr=subprocess.DEVNULL)
        print(f"wrote {tilde(dest)}.\nProgram files and shell scripts now offer \"Run in sbx\" under Open With, and it is the default for program files "
              "without the execute bit. A file with the execute bit still starts directly when you double-click it.\n"
              "Undo with: sbx desktop --remove-opener")
        return
    try:
        with open(dest) as f: ours = "X-Sbx-Opener=true" in f.read()
    except OSError: ours = None
    if ours is False: die(f"{tilde(dest)} was not made by sbx; not removing it")
    if ours: os.unlink(dest)
    try:                                                                  # take our name out of the default-application lists
        with open(mimeapps) as f: lines = f.read().splitlines()
        out = []
        for l in lines:
            k, eq, v = l.partition("=")
            if eq and OPENER_ID in v.split(";"):
                v = ";".join(x for x in v.split(";") if x and x != OPENER_ID)
                if not v: continue
                l = f"{k}={v};"
            out.append(l)
        if out != lines:
            with open(mimeapps, "w") as f: f.write("\n".join(out) + "\n")
    except OSError:
        pass
    print("removed the \"Run in sbx\" opener" if ours is not None else "nothing to remove")


def doctor():
    rows, bad, warned = [], 0, 0
    paint = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM", "dumb") != "dumb"
    colors = {"OK": "32", "WARN": "33", "FAIL": "1;31", "INFO": "2"}

    def row(level, text, hint=""):
        nonlocal bad, warned
        tag = f"{level:<5}"
        if paint and level in colors: tag = f"\033[{colors[level]}m{level}\033[0m" + " " * (5 - len(level))
        rows.append(f"{tag} {text}" + (f"\n      {hint}" if hint else ""))
        bad += level == "FAIL"
        warned += level == "WARN"
    row("OK" if sys.version_info >= (3, 8) else "FAIL", f"Python {platform.python_version()}", "" if sys.version_info >= (3, 8) else "sbx needs Python 3.8 or newer")
    row("FAIL" if os.geteuid() == 0 else "OK", "running as a normal user", "sbx refuses to run as root" if os.geteuid() == 0 else "")
    row("OK" if platform.machine() in ARCHES else "WARN", f"system-call filter for {platform.machine()}",
        "" if platform.machine() in ARCHES else "no list for this architecture: programs run without the filter")
    bw = shutil.which("bwrap")
    if not bw:
        row("FAIL", "bubblewrap", install_hint("bubblewrap"))
    else:
        try: ver = subprocess.run([bw, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired): ver = "?"
        row("OK", f"{bw} ({ver})")
        r = subprocess.run([bw, "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "/bin/true"], capture_output=True, text=True)
        if r.returncode == 0: row("OK", "bubblewrap can create a sandbox")
        else:
            def sysctl(n):
                try:
                    with open("/proc/sys/" + n.replace(".", "/")) as f: return f.read().strip()
                except OSError: return None
            tips = []
            if sysctl("kernel.apparmor_restrict_unprivileged_userns") == "1":
                tips.append("AppArmor blocks user namespaces here (Ubuntu 24.04+): sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0, or give bwrap an AppArmor profile")
            if sysctl("kernel.unprivileged_userns_clone") == "0": tips.append("sudo sysctl kernel.unprivileged_userns_clone=1")
            if sysctl("user.max_user_namespaces") == "0": tips.append("sudo sysctl user.max_user_namespaces=15000")
            row("FAIL", "bubblewrap cannot create a sandbox: " + r.stderr.strip()[:160], "; ".join(tips))
        row("OK" if bwrap_has("--disable-userns") else "WARN", "userns=0 supported (bubblewrap 0.8+)")
    row("OK" if shutil.which("notify-send") else "WARN", "notify-send", "" if shutil.which("notify-send") else f"only needed for the failed-to-start popup: {install_hint('libnotify')}")
    px = shutil.which("xdg-dbus-proxy")
    row("OK" if px else "WARN", "xdg-dbus-proxy", "" if px else f"only needed for portals=1 and tray=1: {install_hint('xdg-dbus-proxy')}")
    ps = shutil.which("pasta")
    row("OK" if ps else "WARN", "pasta", "" if ps else f"only needed for net_private=1: {install_hint('passt')}")
    sd = shutil.which("systemd-run")
    row("OK" if sd else "WARN", "systemd-run", "" if sd else "only needed for memory/tasks/cpu limits")
    wp = wayland_path()
    if wp: row("OK" if wl_supported(wp) else "WARN", f"Wayland socket {wp}",
               "" if wl_supported(wp) else "the compositor has no wp_security_context_v1: programs get full Wayland access (wayland_strict=1 turns Wayland off instead)")
    else: row("INFO", "no Wayland socket (not on a Wayland session)")
    row("INFO", f"X11 display {os.environ['DISPLAY']}" if os.environ.get("DISPLAY") else "no X11 display")
    try:
        with open("/proc/net/unix") as f: abstract_x = any("@/tmp/.X11-unix/" in l for l in f)
    except OSError:
        abstract_x = False
    if abstract_x: row("WARN", "an X server listens on an abstract socket",
                       "with net=1 (the default) a sandboxed program can reach it even with x11=0; only net=0 or net_private=1 closes that")
    rt = runtime_dir()
    row("INFO", "sound: " + (", ".join(x for x in ("pipewire-0", "pulse/native") if os.path.exists(f"{rt}/{x}")) or "no PipeWire/PulseAudio socket"))
    row("INFO", "graphics: " + ("/dev/dri present" if os.path.isdir("/dev/dri") else "no /dev/dri"))
    n = 0
    for p in [f"{CONF}/default.conf"] + sorted(glob.glob(f"{CONF}/apps/*.conf")):
        if os.path.exists(p): parse_conf(p); n += 1                  # warnings about bad lines go to stderr
    row("INFO", f"{n} settings file{'s' if n != 1 else ''} read (problems, if any, are printed above)")
    print("\n".join(rows))
    print()
    if bad: print(f"{bad} problem{'s' if bad != 1 else ''} to fix before sbx can run programs (see FAIL above).")
    elif warned: print(f"sbx works. {warned} optional thing{'s' if warned != 1 else ''} missing (see WARN above); each only matters for the setting it names.")
    else: print("Everything sbx needs is here.")
    return 1 if bad else 0
