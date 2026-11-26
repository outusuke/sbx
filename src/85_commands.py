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
    for k in SCALARS:
        auto = k == "wayland_strict" and src[k] == "default" and cfg["wayland"] and wayland_path() and wl_supported(wayland_path())
        print(f"  {k:<16} = {int(cfg[k]) or int(bool(auto))}   [{'default, on because the compositor can restrict programs' if auto else src[k]}]")
    for k in LIMITS:
        if cfg[k] or k == "share_dirs": print(f"  {k:<16} = {cfg[k] or 'none'}   [{src[k]}]")
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
    home = os.path.realpath(f"{HOMES}/{name}")
    if sys.version_info >= (3, 12): shutil.rmtree(home, onexc=unlock)          # onerror is deprecated there; unlock() only needs the path
    else: shutil.rmtree(home, onerror=unlock)
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
    if clean(name) != name: die(bad_name(name))
    if not (os.path.isdir(home) or os.path.exists(mid)): die(f"nothing to clean for '{name}'")
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
        if running(n): warn(f"'{n}' started meanwhile; skipped"); continue          # the list was made before the question was asked
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
        if clean(a[0]) != a[0]: die(bad_name(a[0]))
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


# ---------------------------------------------------------------- export / import
EXPORT_MAGIC = "# sbx settings export"
EXPORT_MARK = re.compile(r"# \[(default\.conf|apps/[A-Za-z0-9][A-Za-z0-9._-]*\.conf)\]")
EXPORT_MAX = 1 << 20                  # a settings file is a few lines; refuse to read anything bigger as a bundle


def export_files():
    """[(label, path)] for default.conf and every apps/NAME.conf that exists."""
    out = []
    if os.path.isfile(f"{CONF}/default.conf"): out.append(("default.conf", f"{CONF}/default.conf"))
    try: names = sorted(os.listdir(f"{CONF}/apps"))
    except OSError: names = []
    for f in names:
        if f.endswith(".conf") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", f[:-5]) and os.path.isfile(f"{CONF}/apps/{f}"):
            out.append((f"apps/{f}", f"{CONF}/apps/{f}"))
    return out


def export_text(target):
    """The bundle for TARGET: '--all', '--default' or a program name. Dies when there is nothing to export."""
    want = None if target == "--all" else "default.conf" if target == "--default" else f"apps/{target}.conf"
    files = [(l, p) for l, p in export_files() if want in (None, l)]
    if not files: die("no settings to export" if want is None else f"no settings file for {target}: the default settings apply to it")
    out = [EXPORT_MAGIC]
    for label, path in files:
        try:
            with open(path, errors="replace") as f: lines = f.read().splitlines()
        except OSError as e: die(f"could not read {tilde(path)}: {e.strerror or e}")
        out.append(f"# [{label}]")
        # A comment that looks like a section marker would split the file on import: keep it a plain comment.
        out += [("#" + l) if EXPORT_MARK.fullmatch(l.strip()) or l.strip() == EXPORT_MAGIC else l for l in lines]
    return "\n".join(out) + "\n"


def write_private(path, text, replace):
    """Write TEXT to PATH (mode 600, all or nothing). An existing file is only replaced when REPLACE is true."""
    if os.path.lexists(path) and not replace: die(f"{path} already exists; --force replaces it")
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f: f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        try: os.unlink(tmp)
        except OSError: pass
        die(f"could not write {path}: {e.strerror or e}")


def cmd_export(a):
    """sbx export [NAME | --default | --all] [-o FILE] [--force]: settings files as one text file (stdout without -o)."""
    force, out, target, i = False, None, None, 0
    while i < len(a):
        x = a[i]
        if x == "--force": force = True
        elif x == "-o":
            if i + 1 >= len(a): die("-o needs a file name")
            out = a[i + 1]; i += 1
        elif x in ("--default", "--all") or not x.startswith("-"):
            if target: die("usage: sbx export [NAME | --default | --all] [-o FILE] [--force]")
            target = x
        else: die(f"unknown option {x}")
        i += 1
    target = target or "--all"
    if not target.startswith("-") and clean(target) != target: die(bad_name(target))
    text = export_text(target)
    if out is None: sys.stdout.write(text); return
    write_private(out, text, force)
    n = text.count("\n# [")
    print(f"exported {n} settings file{'s' if n != 1 else ''} to {out}")


def parse_bundle(text):
    """{label: file text} from an export. Dies when the text is not one of ours."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != EXPORT_MAGIC: die(f"not an sbx settings export (the first line must be '{EXPORT_MAGIC}')")
    secs, cur = {}, None
    for l in lines[1:]:
        m = EXPORT_MARK.fullmatch(l.strip())
        if m:
            if m.group(1) in secs: die(f"{m.group(1)} appears twice in the export")
            cur = secs[m.group(1)] = []
        elif cur is None:
            if l.strip() and not l.lstrip().startswith("#"): die("the export has settings before its first [section]")
        else: cur.append(l)
    if not secs: die("the export holds no settings")
    return {k: "\n".join(v).strip("\n") + "\n" for k, v in secs.items()}


TIGHTENING = ("net_private", "private", "wayland_strict", "gpu_render_only", "hostname", "privacy")      # switches that turn something off or narrow it when on


def widening(text):
    """What a settings file's TEXT gives a program beyond the defaults: switches turned on that are off by default (apart from the ones
    that only restrict), shared paths, passed variables and allowed system calls, as ['ssh_agent=1', 'whitelist=~/Documents']."""
    out = []
    for line in text.splitlines():
        k, _, v = (x.strip() for x in re.sub(r"(^|\s)#.*$", "", line).strip().partition("="))
        if k in SCALARS and not SCALARS[k] and k not in TIGHTENING and parse_bool(v): out.append(f"{k}=1")
        elif k in LISTS and v: out.append(f"{k}={v}")
    return out


def cmd_import(a):
    """sbx import FILE|- [--force]: put the settings of an export back. Nothing is written when a file would be replaced without --force."""
    force = "--force" in a
    rest = [x for x in a if x != "--force"]
    bad = [x for x in rest if x.startswith("-") and x != "-"]
    if bad: die(f"unknown option {bad[0]}")
    if len(rest) != 1: die("usage: sbx import FILE [--force]")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    try:
        if rest[0] == "-": raw = sys.stdin.read(EXPORT_MAX + 1)
        else:
            with open(rest[0], errors="replace") as f: raw = f.read(EXPORT_MAX + 1)
    except OSError as e: die(f"could not read {rest[0]}: {e.strerror or e}")
    if len(raw) > EXPORT_MAX or "\0" in raw: die("that is not an sbx settings export")
    secs = parse_bundle(raw)
    todo, same, clash = [], 0, []
    for label, text in secs.items():
        path = f"{CONF}/{label}"
        try:
            with open(path, errors="replace") as f: old = f.read()
        except OSError: old = None
        if old is None: todo.append((path, text))
        elif old.strip("\n") == text.strip("\n"): same += 1
        # A file without a single setting (blank or comments only, e.g. after every switch went back to its default) is nothing to lose.
        elif not any(l.strip() and not l.lstrip().startswith("#") for l in old.splitlines()): todo.append((path, text))
        else: clash.append(label); todo.append((path, text))
    if clash and not force:
        die("these settings already exist and differ: " + ", ".join(clash) + ". Nothing was imported; --force replaces them")
    try: os.makedirs(f"{CONF}/apps", mode=0o700, exist_ok=True)
    except OSError as e: die(f"could not create {tilde(CONF)}/apps: {e.strerror or e}")
    for path, text in todo: write_private(path, text, True)
    print(f"imported {len(todo)} settings file{'s' if len(todo) != 1 else ''}" + (f", {same} already the same" if same else ""))
    wide = [(p[len(CONF) + 1:], widening(t)) for p, t in todo]
    if any(w for _, w in wide):
        print("Settings that give programs more than the defaults do (read them before running anything; the file may come from someone else):")
        for label, w in wide:
            if w: print(f"  {label}: {', '.join(w)}")


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
    real = installed_launcher(name) or find_in_path(name)          # a program from `sbx install` has no execute bit, so PATH can't find it
    if not real: die(f"'{name}' is not installed (no program of that name in your PATH, and none from sbx install)")
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
    made = installed_launcher(name)
    if made:                                                     # from `sbx install`: a file without the execute bit, named by its link
        if os.environ.get("SBX_ACTIVE"):
            loader = elf_interpreter(made)
            return os.execv(loader, [loader, made] + args) if loader else die(f"'{name}' can't be started from inside a sandbox")
        run(made, args, {"name": name})
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
    if clean(name) != name: die(bad_name(name))
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


def exec_first(rest):
    try: w = shlex.split(rest)
    except ValueError: w = rest.split(None, 1)
    return w[0] if w else ""


def rewrite_desktop(text, sbx, app_id=None, name=None, via=None):
    """A copy of a .desktop file whose launchers go through sbx. With APP_ID, each Exec= tells sbx that id (--app-id), which sbx
    gives the desktop as the program's id. Returns (new text, list of warnings).
    NAME sets the home and settings name, VIA a program to put in front of each Exec=; the text is None when VIA skips the launcher."""
    out, notes, group = [], [], ""
    q = f'"{sbx}"' if " " in sbx else sbx
    opt = (f" --app-id={app_id}" if app_id else "") + (f" --name={name}" if name is not None else "")
    if via:
        for line in text.splitlines():
            first = exec_first(line[5:]) if line.startswith("Exec=") else ""
            if first in ("env", "sh", "bash") or "=" in first:
                return None, [f"'{line}' starts with {first}: skipped, --via would put {via} in front of {first}. Edit the file by hand."]
    for line in text.splitlines():
        if line.startswith("["): group = line
        if line.startswith("Exec="):
            rest = line[5:]
            first = exec_first(rest)
            if first in ("env", "sh", "bash") or "=" in first:
                notes.append(f"'{line}' starts with {first}: sbx would name the program '{first}'. Edit the file by hand.")
            if via and first and os.path.basename(first) != os.path.basename(via): rest = f"{via} {rest}"
            line = f"Exec={q}{opt} {rest}"
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
    if w and os.path.basename(w[0]) == "sbx":
        w = w[1:]
        while w and w[0].startswith(("--app-id=", "--name=")): w = w[1:]        # the id and name add_launcher gives the launcher
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


def launcher_name(path):
    m = re.search(r'^Exec=[^\n]*?\bsbx"?(?: --app-id=\S+)? --name=(\S+)', launcher_text(path), re.M)
    return m.group(1) if m else None


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


GLOB = re.compile(r"[*?\[]")


def system_launchers(ident):
    """What `sbx desktop IDENT` changes: the launcher with that id (IDENT.desktop, or *.IDENT.desktop), else every one that starts a program called IDENT.
    A glob in IDENT matches ids only."""
    dirs = [d for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    if GLOB.search(ident):
        seen, out = set(), []
        for d in dirs:
            for p in sorted(glob.glob(f"{glob.escape(d)}/applications/{ident}.desktop")):
                if os.path.basename(p) not in seen: seen.add(os.path.basename(p)); out.append(p)
        return out
    cands = [f"{d}/applications/{ident}.desktop" for d in dirs]
    cands += [p for d in dirs for p in sorted(glob.glob(f"{d}/applications/*.{ident}.desktop"))]
    first = next((p for p in cands if os.path.isfile(p)), None)
    return [first] if first else launchers_starting(ident)


def our_launchers(ident, by_program=False):
    """Launchers in ~/.local/share/applications made by `sbx desktop` for IDENT: the one called IDENT.desktop, else *.IDENT.desktop
    (the name kept from the system launcher), else those that start a program called IDENT. by_program skips to the last."""
    d = user_launchers()
    ours = lambda p: "X-Sbx-Sandboxed=true" in launcher_text(p)
    if GLOB.search(ident): return [p for p in sorted(glob.glob(f"{glob.escape(d)}/{ident}.desktop")) if ours(p)]
    if not by_program:
        found = [p for p in [f"{d}/{ident}.desktop"] if ours(p)] or [p for p in sorted(glob.glob(f"{d}/*.{ident}.desktop")) if ours(p)]
        if found: return found
    return [p for p in sorted(glob.glob(f"{d}/*.desktop")) if ours(p) and ident in (launcher_program(p), launcher_name(p))]


def add_launcher(src, ident, name=None, via=None, dry=False):
    """Write the sbx copy of the system launcher SRC to ~/.local/share/applications. Never replaces a file sbx didn't make.
    With DRY it only prints what it would write. Returns whether it wrote or would write."""
    dest = f"{user_launchers()}/{os.path.basename(src)}"
    if os.path.lexists(dest) and "X-Sbx-Sandboxed=true" not in launcher_text(dest):
        warn(f"{tilde(dest)} already exists and was not made by sbx; not touching it"); return False
    ident_of_file = os.path.basename(src)[:-8] if src.endswith(".desktop") else ""        # a launcher's id is its file name without .desktop
    text, notes = rewrite_desktop(launcher_text(src), shutil.which("sbx") or os.path.realpath(sys.argv[0]), ident_of_file if re.fullmatch(APP_ID, ident_of_file) else None, name, via)
    for n in notes: warn(n)
    if text is None: return False
    if dry:
        print(f"would write {tilde(dest)}")
        for line in text.splitlines():
            if line.startswith("Exec="): print(f"  {line}")
        return True
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f: f.write(text)
    print(f"wrote {tilde(dest)}: the launcher now starts the program through sbx.\nUndo with: sbx desktop --remove {ident}")
    return True


ICON_SIZES = (16, 22, 24, 32, 36, 48, 64, 72, 96, 128, 192, 256, 512)       # the size folders the hicolor theme lists
ICON_THEME = f"{HOME}/.local/share/icons/hicolor"


def find_icon(folder, name):
    """The icon a program brought along, for its menu entry: the Icon= of a .desktop file in its folder (a file of that name inside it, else
    the icon theme name), else an image named like the program, else the best image in a folder called icons/ (Firefox keeps
    browser/chrome/icons/default/default128.png). An svg, else the biggest png. A path inside FOLDER, a theme name, or None."""
    norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())
    stem = lambda p: norm(os.path.splitext(os.path.basename(p))[0])
    images, wanted = [], None
    for d, dirs, files in os.walk(folder):
        if d[len(folder):].count("/") >= 4: dirs[:] = []
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            if os.path.islink(p) or not os.path.isfile(p) or re.search(r"[\x00-\x1f]", p): continue
            if f.endswith(".desktop") and wanted is None and d[len(folder):].count("/") <= 1:
                try:
                    with open(p, errors="replace") as fh:
                        for line in fh:
                            if line.startswith("Icon="): wanted = line[5:].strip(); break
                except OSError: pass
            elif f.lower().endswith((".svg", ".png", ".xpm")): images.append(p)

    def best(c):
        svg = [p for p in c if p.lower().endswith(".svg")]
        return svg[0] if svg else max(c, key=os.path.getsize) if c else None
    if wanted and not wanted.startswith("/") and re.fullmatch(r"[A-Za-z0-9._+-]+", wanted):
        hit = best([p for p in images if stem(p) == norm(os.path.splitext(wanted)[0])])
        return hit or wanted
    n = norm(name)
    hit = best([p for p in images if n and stem(p).startswith(n)])
    if hit: return hit
    in_icons = [p for p in images if "icons" in os.path.relpath(p, folder).lower().split("/")[:-1] and not p.lower().endswith(".xpm")]
    return best(in_icons)


def image_kind(p):
    """('svg', None), ('png', pixels) or None for a file that is neither (checked by what is inside, not by its name)."""
    try:
        with open(p, "rb") as f: head = f.read(16384)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        w, h = struct.unpack(">II", head[16:24])
        return ("png", max(w, h)) if 16 <= max(w, h) <= 8192 else None
    return ("svg", None) if b"<svg" in head.lower() else None


def remove_icons(name):
    for p in glob.glob(f"{glob.escape(ICON_THEME)}/*/apps/sbx-{name}.*"):
        try: os.unlink(p)
        except OSError: pass


def install_icon(name, icon):
    """Copy the program's icon into the icon theme as sbx-NAME, so the menu finds it whatever the file was called or where it sits.
    Returns what Icon= should say: the new name, or ICON unchanged when it is a theme name or can't be copied."""
    if not icon or not icon.startswith("/"): return icon
    kind = image_kind(icon)
    if not kind or os.path.getsize(icon) > 5_000_000: return icon
    ext, px = kind
    size = "scalable" if ext == "svg" else "%dx%d" % ((s := min(ICON_SIZES, key=lambda x: (abs(x - px), -x))), s)
    remove_icons(name)
    try:
        os.makedirs(f"{ICON_THEME}/{size}/apps", exist_ok=True)
        shutil.copyfile(icon, f"{ICON_THEME}/{size}/apps/sbx-{name}.{ext}")
        os.utime(ICON_THEME)                                              # the theme trusts an old cache unless its top folder is newer
    except OSError:
        return icon
    tool = shutil.which("gtk-update-icon-cache") or shutil.which("gtk4-update-icon-cache")
    if tool and os.path.exists(f"{ICON_THEME}/icon-theme.cache"): subprocess.run([tool, "-f", "-t", ICON_THEME], capture_output=True, check=False)
    return f"sbx-{name}"


def write_install_launcher(name, main, me):
    """Write ~/.local/share/applications/NAME.desktop for a program made by `sbx install`: it starts MAIN through ME (sbx), with the icon the
    program's folder brought along. Returns (path, icon)."""
    q = lambda x: f'"{x}"' if re.search(r"\s", x) else x
    icon = install_icon(name, find_icon(f"{APPS}/{name}", name))
    text = f"[Desktop Entry]\nX-Sbx-Sandboxed=true\nType=Application\nName={name}\n" + (f"Icon={icon}\n" if icon else "") + f"Exec={q(me)} --name={name} {q(main)} %U\nTerminal=false\nCategories=Utility;\n"
    os.makedirs(user_launchers(), exist_ok=True)
    path = f"{user_launchers()}/{name}.desktop"
    with open(path, "w") as f: f.write(text)
    if shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", user_launchers()], stderr=subprocess.DEVNULL)
    return path, icon


def installed_main(name):
    """The program's launcher path from the install record of `sbx install`, if the record and the program are still there, else None."""
    try:
        with open(f"{INSTALLED}/{name}") as f: main = f.readline().strip()
    except OSError: return None
    return main if main and os.path.isfile(main) else None


def cmd_desktop(a):
    if a in (["--install-opener"], ["--remove-opener"]): return cmd_opener(a[0] == "--install-opener")
    if a in (["--install-menu"], ["--remove-menu"]): return cmd_menu(a[0] == "--install-menu")
    if a in (["--install-installer"], ["--remove-installer"]): return cmd_installer(a[0] == "--install-installer")
    remove, dry = "--remove" in a, "--dry-run" in a
    a = [x for x in a if x not in ("--remove", "--dry-run")]
    name = via = None
    for x in [x for x in a if x.startswith(("--name=", "--via="))]:
        if x.startswith("--name="): name = x[7:]
        else: via = x[6:]
        a.remove(x)
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx desktop [--dry-run] [--name=NAME] [--via=PROGRAM] PATTERN   |   sbx desktop [--dry-run] --remove PATTERN   (PATTERN = a launcher's id, e.g. firefox or org.mozilla.firefox, a glob over ids, e.g. 'myapp*', or the program it starts, e.g. google-chrome-stable)")
    if remove and (name is not None or via is not None): die("--name and --via go with adding a launcher, not with --remove")
    if name is not None and clean(name) != name: die(bad_name(name))
    if via is not None and not re.fullmatch(r"[A-Za-z0-9_+/][A-Za-z0-9._+/-]*", via): die(f"'{via}' is not a program for --via (a name or a path, no spaces)")
    ident = a[0][:-8] if a[0].endswith(".desktop") else a[0]
    if not re.fullmatch(r"[A-Za-z0-9._+*?\[\]!-]+", ident) or ident.startswith("."): die(f"'{ident}' is not a launcher id")
    mine = f"{user_launchers()}/{ident}.desktop"
    if remove:
        if os.path.exists(mine) and "X-Sbx-Sandboxed=true" not in launcher_text(mine): die(f"{tilde(mine)} was not made by sbx; not removing it")
        found = our_launchers(ident)
        if not found: die(f"{tilde(mine)} doesn't exist, and no launcher made by sbx is called *.{ident}.desktop or starts {ident}")
        for p in found:
            if dry: print(f"would remove {tilde(p)}")
            else: os.unlink(p); print(f"removed {tilde(p)}")
        return
    found = system_launchers(ident)
    main = None if found or GLOB.search(ident) else installed_main(ident)
    if main:                                                  # made by `sbx install` and the launcher was removed: write it again
        if name is not None or via is not None: die(f"--name and --via don't apply to '{ident}': the launcher sbx install made already names it")
        if os.path.lexists(mine) and "X-Sbx-Sandboxed=true" not in launcher_text(mine): die(f"{tilde(mine)} already exists and was not made by sbx; not touching it")
        if dry: print(f"would write {tilde(mine)}: the launcher sbx install made for {ident}"); return
        path, icon = write_install_launcher(ident, main, shutil.which("sbx") or os.path.realpath(sys.argv[0]))
        print(f"wrote {tilde(path)}: the launcher starts the program through sbx" + (f", icon {icon}" if icon else ", no icon found in its folder") + f".\nUndo with: sbx desktop --remove {ident}")
        return
    if not found: die(f"no launcher called {ident}.desktop, and none that starts {ident}, in the applications folders of XDG_DATA_DIRS" if not GLOB.search(ident) else f"no launcher id matches {ident} in the applications folders of XDG_DATA_DIRS")
    if not any([add_launcher(p, ident, name, via, dry) for p in found]): sys.exit(1)


# ---------------------------------------------------------------- opening downloaded programs
NOTIFY_IDS = {}


def send_notification(title, body, icon, replace=None):
    """Show a notification. With REPLACE, a later one with the same key takes the place of the earlier one: by id (-r), which
    GNOME needs, plus the hint other desktops use. A notify-send too old for -p/-r gets the hint only."""
    ns = shutil.which("notify-send")
    if not ns: return
    base = [ns, "-a", "sbx", "-i", icon]
    try:
        if replace:
            hint = ["-h", f"string:x-canonical-private-synchronous:{replace}"]
            r = subprocess.run([*base, *hint, "-p", *(["-r", str(NOTIFY_IDS[replace])] if replace in NOTIFY_IDS else []), title, body],
                               capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                if r.stdout.strip().isdigit(): NOTIFY_IDS[replace] = int(r.stdout.strip())
                return
            subprocess.run([*base, *hint, title, body], timeout=5, stdin=subprocess.DEVNULL)
        else:
            subprocess.run([*base, title, body], timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired): pass


def notify(title, body="", icon="dialog-error", replace=None):
    """A desktop notification (there is no terminal to print to when started from a file manager). Also printed."""
    warn(f"{title}: {body}" if body else title)
    send_notification(title, body[:400], icon, replace)


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


INSTALLER_ID = "sbx-install.desktop"
INSTALLER_MIMES = ("inode/directory", "application/zip", "application/x-tar", "application/x-compressed-tar", "application/x-xz-compressed-tar",
                   "application/x-bzip-compressed-tar", "application/x-zstd-compressed-tar", "application/gzip", "application/x-gzip")      # offered, never the default


def cmd_installer(install):
    """sbx desktop --install-installer: add "Install in sbx" to the file manager's Open With for folders and archives (it runs sbx install --notify)."""
    dest = f"{HOME}/.local/share/applications/{INSTALLER_ID}"
    if install:
        me = shutil.which("sbx") or os.path.realpath(sys.argv[0])
        q = f'"{me}"' if " " in me else me
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Install in sbx\nComment=Move a downloaded program to ~/Apps and start it only through sbx\n"
                    f"Exec={q} install --notify %f\nMimeType={';'.join(INSTALLER_MIMES)};\nTerminal=false\nNoDisplay=true\nCategories=System;\n"
                    "X-Sbx-Installer=true\n")
        if shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", os.path.dirname(dest)], stderr=subprocess.DEVNULL)
        print(f"wrote {tilde(dest)}.\nRight-click a folder or archive, Open With Other Application, Install in sbx. It is never the default for them.\n"
              "Undo with: sbx desktop --remove-installer")
        return
    try:
        with open(dest) as f: ours = "X-Sbx-Installer=true" in f.read()
    except OSError: ours = None
    if ours is False: die(f"{tilde(dest)} was not made by sbx; not removing it")
    if ours:
        os.unlink(dest)
        if shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", os.path.dirname(dest)], stderr=subprocess.DEVNULL)
    print("removed \"Install in sbx\"" if ours is not None else "nothing to remove")


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
        true = "/bin/true" if os.path.exists("/bin/true") else shutil.which("true") or "/bin/true"      # NixOS has no /bin/true
        try: r = subprocess.run([bw, "--unshare-all", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", true], capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired: r = subprocess.CompletedProcess([], 1, "", "timed out after 30 seconds")
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
    row("OK" if px else "WARN", "xdg-dbus-proxy", "" if px else f"only needed for portals=1, tray=1, mpris=1, gnome_shell=1, kde=1, a11y=1, talk=, bluetooth=1, battery=1 and avahi=1: {install_hint('xdg-dbus-proxy')}")
    ps = shutil.which("pasta")
    row("OK" if ps else "WARN", "pasta", "" if ps else f"only needed for net_private=1: {install_hint('passt')}")
    if ps:
        pr = pasta_problem()
        if not pr:
            true = shutil.which("true") or "/bin/true"
            try: r = subprocess.run([ps, "--config-net", "--", true], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired) as e: r = subprocess.CompletedProcess([], 1, "", str(e) or "timed out")
            if r.returncode: pr = (r.stderr.strip().splitlines() or ["failed"])[-1][:160]
        row("WARN" if pr else "OK", "pasta can set up a network" if not pr else "pasta cannot set up a network: " + pr, "only needed for net_private=1" if pr else "")
    sd = shutil.which("systemd-run")
    row("OK" if sd else "WARN", "systemd-run", "" if sd else "only needed for memory/tasks/cpu limits")
    wp = wayland_path()
    if wp: row("OK" if wl_supported(wp) else "WARN", f"Wayland socket {wp}",
               "" if wl_supported(wp) else "the compositor has no wp_security_context_v1: programs get full Wayland access (wayland_strict=1 turns Wayland off instead). Where it has one, wayland_strict is on unless you set it")
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
