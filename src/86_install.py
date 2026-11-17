#| 86_install: `sbx install` and `sbx uninstall`: put a downloaded program (a folder or an archive) in ~/Apps/NAME and make it start through sbx.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- install
def guess_name(src):
    """anki-26.9.4-linux-x86_64.tar.zst -> anki; G-Diffuser-v1.2.3-linux-x64 -> g-diffuser. The part up to the first -<digit>."""
    b = os.path.basename(src.rstrip("/"))
    b = re.sub(r"\.(tar(\.[A-Za-z0-9]+)?|tgz|tbz2?|txz|zip)$", "", b, flags=re.I)
    b = re.sub(r"[-_.]v?\d.*$", "", b)
    return re.sub(r"[^A-Za-z0-9._+-]", "-", b).strip("-._").lower()


def program_kind(p):
    """'elf' for an ELF program, 'script' for a file starting with #!, else None. Libraries (.so) are not programs."""
    if re.search(r"\.so(\.|$)", os.path.basename(p)) or os.path.islink(p) or not os.path.isfile(p): return None
    try:
        with open(p, "rb") as f: h = f.read(4)
    except OSError:
        return None
    return "elf" if h == b"\x7fELF" else "script" if h[:2] == b"#!" else None


def pick_launcher(root, name, given=None):
    """The launcher of a program folder, as a path relative to it: the one named by --launcher, else the only executable program at
    its top (or in bin/), else the one named like the program. Dies listing the candidates when it can't tell."""
    if given:
        p = os.path.realpath(os.path.join(root, given))
        if not p.startswith(os.path.realpath(root) + "/") or not os.path.isfile(p): die(f"--launcher {given}: no such file inside the program's folder")
        return os.path.relpath(p, os.path.realpath(root))
    for d in (root, f"{root}/bin"):
        try: names = sorted(os.listdir(d))
        except OSError: continue
        cand = [f"{d}/{n}" for n in names if program_kind(f"{d}/{n}") and os.access(f"{d}/{n}", os.X_OK)]
        if len(cand) == 1: return os.path.relpath(cand[0], root)
        like = [c for c in cand if re.sub(r"[^a-z0-9]", "", os.path.basename(c).lower()) == re.sub(r"[^a-z0-9]", "", name.lower())]
        if len(like) == 1: return os.path.relpath(like[0], root)
        if cand:
            die("can't tell which file starts the program: " + ", ".join(os.path.relpath(c, root) for c in cand) + ". Say which: sbx install ... --launcher PATH")
    die("no program found at the top of the folder (or in bin/). Say which file starts it: sbx install ... --launcher PATH")


def link_escapes(root):
    """The first symbolic link under ROOT that points outside it, or None. An archive must not reach other files through one."""
    base = os.path.realpath(root)
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            if os.path.islink(p) and not (os.path.realpath(p) + "/").startswith(base + "/"): return p
    return None


def unpack(src, into):
    """Unpack a .zip or a tar archive (any compression tar knows) into the empty folder INTO."""
    if src.lower().endswith(".zip"):
        base = os.path.realpath(into)
        with zipfile.ZipFile(src) as z:
            for i in z.infolist():
                t = os.path.realpath(os.path.join(into, i.filename))
                if t != base and not t.startswith(base + "/"): die(f"the archive holds a path that leaves its folder: {i.filename}")
            z.extractall(into)
            for i in z.infolist():                                    # zipfile drops the permission bits
                mode = i.external_attr >> 16
                if mode & 0o100 and not i.is_dir():
                    p = os.path.join(into, i.filename); os.chmod(p, os.stat(p).st_mode | 0o111)
        return
    if not shutil.which("tar"): die(f"tar is missing: {install_hint('tar')}")
    r = subprocess.run(["tar", "-xf", src, "-C", into, "--no-same-owner"], stderr=subprocess.PIPE)
    if r.returncode != 0:
        why = r.stderr.decode(errors="replace").strip().splitlines()
        die("tar couldn't unpack it: " + " ".join(why[-2:]) + (f"; for .zst files install zstd: {install_hint('zstd')}" if src.lower().endswith(".zst") else ""))


def install_with_popup(args, src):
    """sbx install --notify ...: what "Install in sbx" in the file manager runs. There is no terminal there, so the result is a notification."""
    me = os.path.realpath(sys.argv[0]) if os.path.isfile(sys.argv[0]) else shutil.which("sbx")
    if not me: notify("sbx not found"); sys.exit(1)
    r = subprocess.run([sys.executable, me, "install", *args], capture_output=True, text=True, errors="replace")
    sys.stderr.write(r.stderr)
    if r.returncode == 0:
        got = re.search(r"^program is in (\S+)", r.stdout, re.M)
        what = os.path.basename(got[1]) if got else os.path.basename(src)
        notify(f"Installed {what}", "Start it from the app menu. Undo with: sbx uninstall " + what, "dialog-information")
        return
    notify(f"Can't install {os.path.basename(src.rstrip('/'))}", failure_reason(r.stderr, r.returncode) + " (try it in a terminal: sbx install ...)")
    sys.exit(r.returncode)


def find_icon(folder, name):
    """The icon a program brought along, for its menu entry: the Icon= of a .desktop file in its folder (a file of that name inside it, else
    the icon theme name), else an image named like the program (an svg, else the biggest png). A path inside FOLDER, a theme name, or None."""
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
    return best([p for p in images if n and stem(p).startswith(n)])


def cmd_install(a):
    """sbx install FOLDER|ARCHIVE [NAME] [--launcher PATH] [--link]: ~/Apps/NAME holds the program; its launcher loses the execute bit (so running
    it by path outside the sandbox fails, and sbx sets the bit just for its own run); an app-menu entry starts it through sbx. --link also makes
    NAME start it in a terminal (a link in ~/.local/bin, as sbx link does); nothing is put in your PATH without it.
    The program's folder is shared read-only (a settings file, read_only=...). Data goes to its private home, as for any program."""
    launcher, rest, i, want_link, popup = None, [], 0, False, False
    while i < len(a):
        if a[i] == "--link": want_link = True; i += 1
        elif a[i] == "--notify": popup = True; i += 1
        elif a[i] == "--launcher":
            if i + 1 >= len(a): die("--launcher needs a path")
            launcher = a[i + 1]; i += 2
        elif a[i].startswith("-"): die(f"unknown option {a[i]}\nusage: sbx install FOLDER|ARCHIVE [NAME] [--launcher PATH] [--link]")
        else: rest.append(a[i]); i += 1
    if not 1 <= len(rest) <= 2: die("usage: sbx install FOLDER|ARCHIVE [NAME] [--launcher PATH] [--link]")
    if popup: return install_with_popup([x for x in a if x != "--notify"], rest[0])
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    if os.geteuid() == 0: die("refusing to run as root")
    src = os.path.abspath(rest[0])
    if not os.path.exists(src): die(f"{rest[0]} not found")
    name = rest[1] if len(rest) == 2 else guess_name(src)
    if not name or clean(name) != name or name.startswith("sbx") or name in COMMANDS:
        die(f"'{name}' can't be the name of a program" + ("" if len(rest) == 2 else f"; give one: sbx install {rest[0]} NAME"))
    if os.path.isdir(src) and (os.path.realpath(src) in generic_folders() or os.path.realpath(src) == HOME):
        die(f"{tilde(src)} holds all sorts of things; install the program's own folder")
    dest, link = f"{APPS}/{name}", f"{HOME}/.local/bin/{name}"
    me = shutil.which("sbx") or os.path.realpath(sys.argv[0])
    if os.path.basename(me) != "sbx": die("can't tell where sbx is installed; start this command by the name sbx")
    for p, what in ((dest, "a program folder"), (link, "a command"), (f"{INSTALLED}/{name}", "an install record"), (f"{user_launchers()}/{name}.desktop", "a menu entry")):
        if p == link and not want_link: continue
        if os.path.lexists(p): die(f"{tilde(p)} already exists ({what}). Pick another NAME, or remove the old one first: sbx uninstall {name}")
    os.makedirs(APPS, exist_ok=True)
    tmp = None
    try:
        if os.path.isdir(src):
            root = src
        else:
            tmp = tempfile.mkdtemp(prefix=".extract.", dir=APPS)
            unpack(src, tmp)
            top = os.listdir(tmp)
            root = f"{tmp}/{top[0]}" if len(top) == 1 and os.path.isdir(f"{tmp}/{top[0]}") and not os.path.islink(f"{tmp}/{top[0]}") else tmp
            bad = link_escapes(root)
            if bad: die(f"the archive holds a link that points outside its folder ({os.path.relpath(bad, root)}); not installing it")
        rel = pick_launcher(root, name, launcher)
        if os.path.isdir(src): shutil.move(root, dest)
        else: os.rename(root, dest)
    finally:
        if tmp: shutil.rmtree(tmp, ignore_errors=True)
    print(f"program is in {tilde(dest)}" + (f" (moved from {tilde(src)})" if os.path.isdir(src) else ""))
    main = f"{dest}/{rel}"
    if elf_interpreter(main):
        os.chmod(main, os.stat(main).st_mode & ~0o111)
        print(f"took the execute bit off {rel}: starting it by its path outside sbx now fails; sbx sets the bit for its own run")
    else:
        warn(f"{rel} keeps its execute bit: sbx can only start a program without it if it is a dynamically linked ELF file")
    os.makedirs(INSTALLED, exist_ok=True)
    with open(f"{INSTALLED}/{name}", "w") as f: f.write(main + "\n")
    conf = f"{CONF}/apps/{name}.conf"
    if not os.path.exists(conf):
        os.makedirs(os.path.dirname(conf), exist_ok=True)
        with open(conf, "w") as f: f.write(f"read_only={tilde(dest)}\n")
        print(f"wrote {tilde(conf)}: the program's folder is shared read-only. If it must write there, change that line to folder=1")
    else:
        warn(f"{tilde(conf)} already exists and was left alone; it must share {tilde(dest)} (read_only=...) for the program to find its files")
    if want_link: cmd_link([name])
    q = lambda x: f'"{x}"' if re.search(r"\s", x) else x
    icon = find_icon(dest, name)
    text = f"[Desktop Entry]\nX-Sbx-Sandboxed=true\nType=Application\nName={name}\n" + (f"Icon={icon}\n" if icon else "") + f"Exec={q(me)} --name={name} {q(main)} %U\nTerminal=false\nCategories=Utility;\n"
    os.makedirs(user_launchers(), exist_ok=True)
    with open(f"{user_launchers()}/{name}.desktop", "w") as f: f.write(text)
    if shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", user_launchers()], stderr=subprocess.DEVNULL)
    print(f"app menu: wrote {tilde(user_launchers())}/{name}.desktop (it starts the program through sbx)" + (f", icon {tilde(icon)}" if icon else ", no icon found in its folder: add Icon=... to that file"))
    print("Start it from the app menu" + (f" or with: {name}" if want_link else f". For a terminal command too: sbx link {name}") + f"\nUndo with: sbx uninstall {name}")


def move_to_trash(path):
    """Move PATH to the Trash (restorable) with `gio trash`. Raises OSError with the reason when that can't be done."""
    gio = shutil.which("gio")
    if not gio: raise OSError("gio is missing (the glib command-line tools: libglib2.0-bin on Debian and Ubuntu, glib2 elsewhere)")
    top = os.stat(path).st_mode
    if os.path.isdir(path) and not os.path.islink(path): os.chmod(path, top | 0o700)    # moving a folder to another parent needs write access to it
    r = subprocess.run([gio, "trash", path], stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors="replace")
    if r.returncode != 0:
        if os.path.lexists(path) and not os.path.islink(path): os.chmod(path, top & 0o7777)
        raise OSError(r.stderr.strip() or f"gio trash exited with {r.returncode}")


def cmd_uninstall(a):
    """sbx uninstall [--yes] [--trash] NAME: remove a program made by sbx install, with its link, menu entry and install record. Its folder
    is deleted, or with --trash moved to the Trash (restorable). Its private home (what it saved) and its settings file stay; sbx clean NAME
    and sbx reset NAME remove those."""
    yes, trash = "--yes" in a, "--trash" in a
    a = [x for x in a if x not in ("--yes", "--trash")]
    if len(a) != 1 or a[0].startswith("-"): die("usage: sbx uninstall [--yes] [--trash] NAME")
    if os.environ.get("SBX_ACTIVE"): die("not from inside a sandbox")
    name = a[0]
    if clean(name) != name: die(bad_name(name))
    rec = f"{INSTALLED}/{name}"
    if not os.path.isfile(rec): die(f"'{name}' was not installed by sbx install")
    if running(name): die(f"'{name}' is running. Stop it first: sbx stop {name}")
    dest = f"{APPS}/{name}"
    made = installed_launcher(name)
    inside = bool(made) and os.path.isdir(dest) and not os.path.islink(dest) and os.path.realpath(made).startswith(os.path.realpath(dest) + "/")
    if not yes:
        if not sys.stdin.isatty(): die("not asking without a terminal; add --yes")
        if input(f"{'Move to the Trash' if trash and inside else 'Delete'} {tilde(dest) if inside else 'the install record'} of '{name}', its link and its menu entry? Its private home stays. [y/N] ").strip().lower() not in ("y", "yes"):
            die("nothing deleted", 0)
    if inside and trash:
        try: move_to_trash(dest)
        except OSError as e: die(f"couldn't move {tilde(dest)} to the Trash ({e}); nothing was changed. Without --trash it is deleted instead")
        print(f"moved {tilde(dest)} to the Trash")
    elif inside:
        def unlock(fn, p, _exc):
            os.chmod(os.path.dirname(p), 0o700)
            if not os.path.islink(p): os.chmod(p, 0o700)
            fn(p)
        if sys.version_info >= (3, 12): shutil.rmtree(dest, onexc=unlock)
        else: shutil.rmtree(dest, onerror=unlock)
        print(f"removed {tilde(dest)}")
    elif made: warn(f"{made} is not inside {tilde(dest)}; the program's files were left where they are")
    link = f"{HOME}/.local/bin/{name}"
    if is_sbx_link(link): os.unlink(link); print(f"removed {tilde(link)}")
    for p in our_launchers(name): os.unlink(p); print(f"removed {tilde(p)}")
    os.unlink(rec)
    if shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", user_launchers()], stderr=subprocess.DEVNULL)
    left = [x for x in (f"{HOMES}/{name}", f"{CONF}/apps/{name}.conf") if os.path.exists(x)]
    if left: print("kept " + " and ".join(tilde(x) for x in left) + f". Delete them with: sbx clean {name}; sbx reset {name}")
