#| 85_commands: Convenience commands around the sandbox: show, clean, desktop launchers, open, doctor. Nothing here builds the sandbox.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

def cmd_show(a):
    o, a = parse_options(a)
    if len(a) != 1: die("usage: sbx show [options] NAME")
    name = clean(o.get("name") or os.path.basename(a[0]))
    if not o.get("name") and "/" in a[0] and os.path.isfile(a[0]):             # a file: the program it runs as (a renamed file keeps its old name)
        owner, _old, why = file_owner(name, os.path.realpath(a[0]))
        if owner != name: print(f"{tilde(os.path.abspath(a[0]))} is '{owner}' renamed (same size and SHA-256 as its recorded file)")
        if why: print(why)
        name = owner
    cfg, src = effective(name, o)
    home = f"{HOMES}/{name}"
    print(f"{name}   private home {tilde(home)} ({'exists' if os.path.isdir(home) else 'not created yet'})")
    where = origin_source(name)
    if where: print(f"  {'source':<16} = {source_text(where)}   [{tilde(CONF)}/origins/{name}]")
    via = [t for t, names in (("link", linked_programs()), ("menu", launcher_programs())) if name in names]
    if via: print(f"  {'starts via':<16} = {', '.join(via)}")
    p = read_pending(name)
    if p and not pending_stale(name, p): print(f"  {'waiting':<16} = {tilde(p['old'])} -> {tilde(p['new'])}   [sbx rebind {name} accepts it, sbx rebind --dismiss {name} drops it]")
    for k in SCALARS: print(f"  {k:<16} = {int(cfg[k])}   [{src[k]}]")
    for k in LIMITS:
        if cfg[k]: print(f"  {k:<16} = {cfg[k]}   [{src[k]}]")
    for k in LISTS:
        for v in cfg[k]: print(f"  {k:<16} = {v}   [{src[(k, v)]}]")
    for k, flag in (("whitelist", "whitelist"), ("read_only", "read_only")):
        for v in o.get(flag, []): print(f"  {k:<16} = {v}   [flag]")


def home_problem(name):
    """Why NAME's private home can't be deleted, or None."""
    home = f"{HOMES}/{name}"
    real = os.path.realpath(home)
    if os.path.islink(home) or not real.startswith(os.path.realpath(HOMES) + "/"): return f"{tilde(home)} is not a plain private home"
    return None


def wipe_home(name):
    """Delete NAME's private home, its machine-id, its origin note and any decision waiting on it. Settings stay."""
    def unlock(fn, p, _exc):                     # programs sometimes leave read-only folders behind
        os.chmod(os.path.dirname(p), 0o700)
        if not os.path.islink(p): os.chmod(p, 0o700)         # never chmod through a link the program planted
        fn(p)
    shutil.rmtree(os.path.realpath(f"{HOMES}/{name}"), onerror=unlock)
    for gone in (f"{CONF}/ids/{name}", f"{CONF}/origins/{name}", pending_path(name)):
        try: os.unlink(gone)
        except OSError: pass


def linked_programs():
    """Names with an sbx link in ~/.local/bin."""
    d = f"{HOME}/.local/bin"
    try: return {clean(n) for n in os.listdir(d) if is_sbx_link(f"{d}/{n}")}
    except OSError: return set()


def launcher_programs():
    """Names an sbx launcher in ~/.local/share/applications starts or is called after."""
    names = set()
    for p in glob.glob(f"{user_launchers()}/*.desktop"):
        if "X-Sbx-Sandboxed=true" not in launcher_text(p): continue
        stem = os.path.basename(p)[:-8]
        names.update(clean(x) for x in (launcher_program(p), stem, stem.rsplit(".", 1)[-1]) if x)
    return names


def covered_programs():
    """Names with an sbx link in ~/.local/bin or an sbx launcher in ~/.local/share/applications: somebody set those up on purpose."""
    return linked_programs() | launcher_programs()


def known_programs(live=()):
    """{name: set up?} for every program sbx knows. Set up means a settings file, an sbx link or launcher, or running now;
    otherwise all sbx has is a private home that a run created, as for `sbx curl ...` typed once."""
    homes, conf = set(), set()
    for d, suffix, into in ((HOMES, "", homes), (f"{CONF}/apps", ".conf", conf)):
        try: into.update(n[:len(n) - len(suffix)] for n in os.listdir(d) if n.endswith(suffix) and not n.startswith("."))
        except OSError: pass
    chosen = conf | set(live) | covered_programs()
    return {n: n in chosen for n in homes | conf | set(live)}


def unused_programs():
    """Programs nobody set up whose private home is nearly empty: what `sbx clean --unused` deletes."""
    live = {pid_app(p) for p in running()}
    return sorted(n for n, chosen in known_programs(live).items() if not chosen and os.path.isdir(f"{HOMES}/{n}") and home_is_small(n))


def cmd_clean(a):
    yes = "--yes" in a
    a = [x for x in a if x != "--yes"]
    if len(a) != 1 or (a[0].startswith("-") and a[0] != "--unused"): die("usage: sbx clean [--yes] NAME   |   sbx clean [--yes] --unused")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    if a[0] == "--unused": return clean_unused(yes)
    name = a[0]
    home, mid = f"{HOMES}/{name}", f"{CONF}/ids/{name}"
    if clean(name) != name or not (os.path.isdir(home) or os.path.exists(mid)): die(f"nothing to clean for '{name}'")
    if running(name): die(f"'{name}' is running. Stop it first: sbx stop {name}")
    if home_problem(name): die(home_problem(name))
    if not yes:
        if not sys.stdin.isatty(): die("not asking without a terminal; add --yes")
        if input(f"Delete {tilde(home)} and the machine-id of '{name}'? Settings stay. [y/N] ").strip().lower() not in ("y", "yes"):
            die("nothing deleted", 0)
    wipe_home(name)
    print(f"removed {tilde(home)} and its machine-id")


def clean_unused(yes):
    """sbx clean --unused: the private homes of programs nobody set up (no settings file, link or launcher) that hold under 64 KiB."""
    names = unused_programs()
    if not names: print("Nothing unused: every program has settings, a link, a launcher, is running, or holds more than 64 KiB."); return
    if not yes:
        if not sys.stdin.isatty(): die("not asking without a terminal; add --yes")
        print(f"Delete the private homes of these {len(names)} programs? None has settings, a link or a launcher, and each holds under {UNUSED_BYTES // 1024} KiB.")
        for n in names: print(f"  {tilde(f'{HOMES}/{n}')}")
        if input("Delete? [y/N] ").strip().lower() not in ("y", "yes"): die("nothing deleted", 0)
    done = 0
    for n in names:
        problem = home_problem(n)
        if problem: warn(problem + "; skipped"); continue
        wipe_home(n); done += 1
    print(f"removed {done} private home{'s' if done != 1 else ''}")


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


def cmd_link(a):
    """sbx link NAME [--desktop] | --remove NAME [--desktop]: a link ~/.local/bin/NAME to sbx, so typing NAME starts the program through sbx
    (firejail's firecfg). --desktop does the same for the app-menu launchers that start NAME (what `sbx desktop` does)."""
    remove, both = "--remove" in a, "--desktop" in a
    a = [x for x in a if x not in ("--remove", "--desktop")]
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx link NAME [--desktop]   |   sbx link --remove NAME [--desktop]")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    name = a[0]
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", name) or name.startswith(".") or name.startswith("sbx") or name in COMMANDS: die(f"'{name}' can't be a link name")
    dest = f"{HOME}/.local/bin/{name}"
    if remove:
        did = False
        if os.path.lexists(dest):
            if not is_sbx_link(dest): die(f"{tilde(dest)} was not made by sbx; not removing it")
            os.unlink(dest); print(f"removed {tilde(dest)}; '{name}' starts directly again"); did = True
        if both:
            for p in our_launchers(name, by_program=True): os.unlink(p); print(f"removed {tilde(p)}"); did = True
        elif our_launchers(name, by_program=True): print(f"An app-menu launcher still starts it through sbx. Undo with: sbx link --remove {name} --desktop")
        if not did: die(f"{tilde(dest)} doesn't exist" + (f", and no launcher made by sbx starts {name}" if both else ""))
        return
    real = find_in_path(name)
    if not real: die(f"'{name}' is not installed (no program of that name in your PATH)")
    if os.path.lexists(dest) and not is_sbx_link(dest): die(f"{tilde(dest)} already exists and was not made by sbx; not touching it")
    me = shutil.which("sbx") or os.path.realpath(sys.argv[0])
    if os.path.basename(me) != "sbx": die("can't tell where sbx is installed; start this command by the name sbx")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.lexists(dest): os.unlink(dest)
    os.symlink(me, dest)
    print(f"linked {tilde(dest)}: typing '{name}' now starts {real} through sbx\nStarting it by its full path still skips sbx. Undo with: sbx link --remove {name}")
    dirs = [os.path.realpath(d) for d in os.environ.get("PATH", "").split(":") if d]
    mine, theirs = os.path.realpath(os.path.dirname(dest)), os.path.realpath(os.path.dirname(real))
    if mine not in dirs: warn(f"{tilde(os.path.dirname(dest))} is not in your PATH, so '{name}' still starts directly. Add it, then open a new terminal")
    elif theirs in dirs and dirs.index(theirs) < dirs.index(mine):
        warn(f"{tilde(os.path.dirname(dest))} is not before {os.path.dirname(real)} in your PATH, so '{name}' still starts directly. Put it first, then open a new terminal")
    if both:
        found = launchers_starting(name)
        if not found: print(f"no app-menu launcher starts '{name}'; nothing to add there")
        for p in found: add_launcher(p, name)
        return
    ids = [os.path.basename(p)[:-8] for p in launchers_starting(name) if not os.path.lexists(f"{user_launchers()}/{os.path.basename(p)}")]
    if len(ids) == 1: print(f"The app-menu launcher {ids[0]} still starts it directly. Cover it too with: sbx desktop {ids[0]}")
    elif ids: print(f"The app-menu launchers {', '.join(ids)} still start it directly. Cover them too with: sbx link {name} --desktop")


def linked_name(argv0):
    """The program name when sbx was started through a link made by `sbx link`, else None."""
    n = os.path.basename(argv0)
    return n if is_sbx_link(argv0) and not n.startswith("sbx") else None


def run_linked(argv0, args):
    """Started through a link: run the real program of that name, sandboxed. Its arguments are its own, never sbx options."""
    name = os.path.basename(argv0)
    real = find_in_path(name)
    if not real: die(f"'{name}' not found in your PATH, apart from this link. Remove it with: sbx link --remove {name}")
    if os.environ.get("SBX_ACTIVE"): return os.execv(real, [name] + args)          # already in a sandbox (one program starting another)
    run(name, args, {})


def cmd_list(a):
    """Programs somebody set up (a settings file, an sbx link or launcher, or running now), and whether each is running, where it comes from
    and whether a decision about it is waiting. --all adds the ones that only have a private home, which every `sbx PROGRAM` run leaves
    behind; those come last, System programs first and Downloaded ones at the end."""
    if a not in ([], ["--all"]): die("usage: sbx list [--all]")
    live = {p: pid_app(p) for p in running()}
    progs = known_programs(set(live.values()))
    if not progs: print("No programs yet. Start one with: sbx PROGRAM"); return
    src = {n: origin_source(n) for n in progs}
    rank = lambda n: (SOURCE_RANK.get(src[n]["kind"], 1) if src[n] else 1, n)
    setup = sorted(n for n in progs if progs[n])
    terminal = sorted((n for n in progs if not progs[n]), key=rank)
    linked, menu, waiting = linked_programs(), launcher_programs(), set(pending_names())
    for n in setup + (terminal if a else []):
        k = sum(1 for v in live.values() if v == n)
        tags = [t for t, names in (("link", linked), ("menu", menu)) if n in names]
        parts = ([source_text(src[n])] if src[n] else []) + ([f"[{', '.join(tags)}]"] if tags else [])
        if n in waiting: parts.append(f"waiting for your decision: sbx rebind {n}")
        if not progs[n]: parts.append("(only run from the terminal)")
        print((f"{n:32} {'running (%d)' % k if k else 'idle':<12} " + "   ".join(parts)).rstrip())
    if terminal and not a: print(f"{len(terminal)} more only run from the terminal, with no settings, link or launcher. Show them: sbx list --all   Delete the empty ones: sbx clean --unused")


def cmd_stop(a):
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx stop NAME")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    if not running(a[0]): die(f"'{a[0]}' is not running")
    n = stop_program(a[0])
    print(f"stopping {a[0]}" + (f" ({n} processes)" if n > 1 else ""))


def cmd_rebind(a):
    """sbx rebind NAME | --dismiss NAME: decide what sbx left pending when a different file showed up under a program's name."""
    dismiss = "--dismiss" in a
    a = [x for x in a if x != "--dismiss"]
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx rebind NAME   |   sbx rebind --dismiss NAME")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    name = a[0]
    if clean(name) != name: die(f"'{name}' is not a program name")
    p = read_pending(name)
    if dismiss:
        if not os.path.lexists(pending_path(name)): die(f"nothing is waiting for a decision for '{name}'")
        clear_pending(name)
        print(f"dropped the decision for '{name}'. Nothing changed: the name still belongs to {tilde(p['old']) if p else 'what it did'}.")
        return
    if not p: die(f"nothing is waiting for a decision for '{name}'")
    why = pending_stale(name, p)
    if why:
        clear_pending(name)
        die(f"the decision for '{name}' no longer applies ({why}). Start it again and sbx decides afresh")
    follow(name, p["new"], None, p["old"])               # the next run adds the fingerprint (a big file isn't read here)
    clear_pending(name)
    print(f"'{name}' now belongs to {tilde(p['new'])}, with the settings and private home it had. Start it again.")


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


def user_launchers():
    return f"{HOME}/.local/share/applications"


def exec_program(value):
    """The program an Exec= value starts: the base name of its first word, past a leading sbx. None when it can't tell."""
    try: w = shlex.split(value)
    except ValueError: return None
    if w and os.path.basename(w[0]) == "sbx": w = w[1:]
    return os.path.basename(w[0]) if w else None


def launcher_text(path):
    try:
        with open(path, errors="replace") as f: return f.read()
    except OSError: return ""


def launcher_program(path):
    """The program the main Exec= line of a .desktop file starts (see exec_program), or None."""
    group = ""
    for line in launcher_text(path).splitlines():
        if line.startswith("["): group = line
        elif group == "[Desktop Entry]" and line.startswith("Exec="): return exec_program(line[5:])
    return None


def launchers_starting(name):
    """System launchers whose Exec= starts a program called NAME. Of two with the same file name the first in XDG_DATA_DIRS wins, as in the menu."""
    seen, out = set(), []
    for d in [d for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]:
        for p in sorted(glob.glob(f"{d}/applications/*.desktop")):
            b = os.path.basename(p)
            if b in seen: continue
            seen.add(b)
            if launcher_program(p) == name: out.append(p)
    return out


def system_launchers(ident):
    """What `sbx desktop IDENT` changes: the launcher with that id (IDENT.desktop, or *.IDENT.desktop), else every one that starts a program called IDENT."""
    dirs = [d for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    cands = [f"{d}/applications/{ident}.desktop" for d in dirs]
    cands += [p for d in dirs for p in sorted(glob.glob(f"{d}/applications/*.{ident}.desktop"))]
    first = next((p for p in cands if os.path.isfile(p)), None)
    return [first] if first else launchers_starting(ident)


def our_launchers(ident, by_program=False):
    """Launchers in ~/.local/share/applications made by `sbx desktop` for IDENT: the one called IDENT.desktop, else *.IDENT.desktop
    (the name kept from the system launcher), else those that start a program called IDENT. by_program skips to the last."""
    d = user_launchers()
    ours = lambda p: "X-Sbx-Sandboxed=true" in launcher_text(p)
    if not by_program:
        found = [p for p in [f"{d}/{ident}.desktop"] if ours(p)] or [p for p in sorted(glob.glob(f"{d}/*.{ident}.desktop")) if ours(p)]
        if found: return found
    return [p for p in sorted(glob.glob(f"{d}/*.desktop")) if ours(p) and launcher_program(p) == ident]


def add_launcher(src, ident):
    """Write the sbx copy of the system launcher SRC to ~/.local/share/applications. Never replaces a file sbx didn't make."""
    dest = f"{user_launchers()}/{os.path.basename(src)}"
    if os.path.lexists(dest) and "X-Sbx-Sandboxed=true" not in launcher_text(dest):
        warn(f"{tilde(dest)} already exists and was not made by sbx; not touching it"); return False
    text, notes = rewrite_desktop(launcher_text(src), shutil.which("sbx") or os.path.realpath(sys.argv[0]))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f: f.write(text)
    for n in notes: warn(n)
    print(f"wrote {tilde(dest)}: the launcher now starts the program through sbx.\nUndo with: sbx desktop --remove {ident}")
    return True


def cmd_desktop(a):
    if a in (["--install-opener"], ["--remove-opener"]): return cmd_opener(a[0] == "--install-opener")
    if a in (["--install-menu"], ["--remove-menu"]): return cmd_menu(a[0] == "--install-menu")
    remove = "--remove" in a
    a = [x for x in a if x != "--remove"]
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx desktop NAME   |   sbx desktop --remove NAME   (NAME = the launcher's id, e.g. firefox or org.mozilla.firefox, or the program it starts, e.g. google-chrome-stable)")
    ident = a[0][:-8] if a[0].endswith(".desktop") else a[0]
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", ident) or ident.startswith("."): die(f"'{ident}' is not a launcher id")
    mine = f"{user_launchers()}/{ident}.desktop"
    if remove:
        if os.path.exists(mine) and "X-Sbx-Sandboxed=true" not in launcher_text(mine): die(f"{tilde(mine)} was not made by sbx; not removing it")
        found = our_launchers(ident)
        if not found: die(f"{tilde(mine)} doesn't exist, and no launcher made by sbx is called *.{ident}.desktop or starts {ident}")
        for p in found: os.unlink(p); print(f"removed {tilde(p)}")
        return
    found = system_launchers(ident)
    if not found: die(f"no launcher called {ident}.desktop, and none that starts {ident}, in the applications folders of XDG_DATA_DIRS")
    if not any([add_launcher(p, ident) for p in found]): sys.exit(1)


# ---------------------------------------------------------------- opening downloaded programs
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


MISSING_FILE = re.compile(r"panicked at [^\n]*\n[^\n]*kind: NotFound|FileNotFoundError|no such file or directory", re.I)


def missing_file_hint(text):
    """One line of advice when a failed run's output says a file was not found, else ''. A program starts in its private home, so
    files it expects in the folder it was started from are missing; folder=1 starts it in the folder its file sits in."""
    if not MISSING_FILE.search(text): return ""
    return ("It looked for a file that was not there. If it works on files in its own folder, turn on folder=1 for it "
            "(sbx-gui: Start in the program's own folder). That only works from a folder of its own, not Downloads or your home")


def cmd_open(a):
    """sbx open FILE: what 'Open with' calls. A program with no settings of its own gets game controllers and the usual defaults.
    Its own folder is not shared unless folder=1 says so; a program that dies on a missing file is told so (see missing_file_hint).
    A program with a settings file gets exactly that file."""
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx open FILE")
    f = os.path.abspath(a[0])
    name = clean(os.path.basename(f))
    if os.path.isfile(f): name = file_owner(name, os.path.realpath(f))[0]       # a renamed file is its old program: its settings decide below
    journal_tag(name)
    if not os.path.isfile(f): notify(f"Can't open {name}", "no such file"); sys.exit(1)
    flags = []
    if not os.access(f, os.X_OK) and not elf_interpreter(f):
        notify(f"Can't run {name}", "it is not executable and not a dynamically linked program; run chmod +x on it"); sys.exit(1)
    if not os.path.exists(f"{CONF}/apps/{name}.conf"):
        flags.append("--input")
    me = os.path.realpath(sys.argv[0]) if os.path.isfile(sys.argv[0]) else shutil.which("sbx")
    if not me: notify("sbx not found"); sys.exit(1)
    err, t0 = tempfile.TemporaryFile(), time.monotonic()
    rc = subprocess.run([sys.executable, me, *flags, f], stderr=err, env={**os.environ, "SBX_NO_POPUP": "1"}).returncode   # we show the popup, with the reason
    err.seek(0)
    text = err.read().decode(errors="replace")
    sys.stderr.write(text)
    sig = crash_signal(rc)
    if sig and not read_pending(name): notify_crash(name, sig)                 # a crash says so, however soon or late it came
    elif rc != 0 and time.monotonic() - t0 < 15 and not read_pending(name):    # it never really started (a decision waiting has its own notification)
        why = failure_reason(text, rc)
        tip = missing_file_hint(text)
        warn(f"{name} didn't start: {why}")
        if tip: warn(tip)
        notify_failure(name, why + (f" {tip}." if tip else ""))                                  # clicking it opens the log, like a start from the menu
    elif rc != 0 and exit_code(rc) not in STOP_CODES and not read_pending(name): notify_ended(name, exit_code(rc))      # not when you stopped it
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
