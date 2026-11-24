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


def progress(msg, final=False):
    """Show where an install is, on stderr (stdout is read by the file manager's popup). One updating line on a terminal."""
    tty = sys.stderr.isatty()
    sys.stderr.write(("\r\033[K" + msg if tty else msg) + ("\n" if final or not tty else "")); sys.stderr.flush()


def unpack(src, into):
    """Unpack a .zip or a tar archive (any compression tar knows) into the empty folder INTO, telling how far it is."""
    size = os.path.getsize(src) / 1048576
    progress(f"unpacking {os.path.basename(src)} ({size:.0f} MB)...", final=not sys.stderr.isatty())
    if src.lower().endswith(".zip"):
        base = os.path.realpath(into)
        with zipfile.ZipFile(src) as z:
            items = z.infolist()
            for i in items:
                t = os.path.realpath(os.path.join(into, i.filename))
                if t != base and not t.startswith(base + "/"): die(f"the archive holds a path that leaves its folder: {i.filename}")
            step = max(1, len(items) // 20)
            for n, i in enumerate(items, 1):
                z.extract(i, into)
                mode = i.external_attr >> 16                          # zipfile drops the permission bits
                if mode & 0o100 and not i.is_dir():
                    p = os.path.join(into, i.filename); os.chmod(p, os.stat(p).st_mode | 0o111)
                if n % step == 0 or n == len(items): progress(f"unpacking: {n * 100 // len(items)}% ({n}/{len(items)} files)", final=n == len(items))
        return
    if not shutil.which("tar"): die(f"tar is missing: {install_hint('tar')}")
    pr = subprocess.Popen(["tar", "-xvf", src, "-C", into, "--no-same-owner"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    n, last = 0, []
    for line in pr.stdout:
        n += 1; last = (last + [line.strip()])[-3:]
        if n % 200 == 0: progress(f"unpacking: {n} files...")
    pr.wait()
    if pr.returncode != 0:
        die("tar couldn't unpack it: " + " ".join(last[-2:]) + (f"; for .zst files install zstd: {install_hint('zstd')}" if src.lower().endswith(".zst") else ""))
    progress(f"unpacked {n} files", final=True)


def install_with_popup(args, src):
    """sbx install --notify ...: what "Install in sbx" in the file manager runs. There is no terminal there, so the result is a notification."""
    me = os.path.realpath(sys.argv[0]) if os.path.isfile(sys.argv[0]) else shutil.which("sbx")
    if not me: notify("sbx not found"); sys.exit(1)
    pr = subprocess.Popen([sys.executable, me, "install", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    err, title = [], f"Installing {os.path.basename(src.rstrip('/'))}"
    for line in pr.stderr:                                                 # the install's progress lines: one notification, updated in place
        err.append(line); sys.stderr.write(line)
        if not line.startswith("sbx:"): send_notification(title, line.strip()[:200], "system-software-install", "sbx-install")
    out = pr.communicate()[0]
    r = subprocess.CompletedProcess(pr.args, pr.wait(), out, "".join(err))
    got = re.search(r"^program is in (\S+)", r.stdout, re.M)
    if r.returncode == 0 and not got:
        notify(f"Not installed: {os.path.basename(src.rstrip('/'))}", "Nothing was changed.", "dialog-information", replace="sbx-install"); return
    if r.returncode == 0:
        what = os.path.basename(got[1]) if got else os.path.basename(src)
        notes = [l[5:].strip() for l in r.stderr.splitlines() if l.startswith("sbx:")] + [l for l in r.stdout.splitlines() if l.startswith("added read_only")]
        notify(f"Installed {what}", "Start it from the app menu. Undo with: sbx uninstall " + what + ("".join(" " + n + "." for n in notes)), "dialog-information", replace="sbx-install")
        return
    notify(f"Can't install {os.path.basename(src.rstrip('/'))}", failure_reason(r.stderr, r.returncode) + " (try it in a terminal: sbx install ...)", replace="sbx-install")
    sys.exit(r.returncode)


def conf_shares(conf, folder):
    """True if the settings file CONF already shares FOLDER (or a folder above it) through whitelist= or read_only=."""
    base = os.path.realpath(folder)
    try:
        with open(conf, errors="replace") as f: lines = f.read().splitlines()
    except OSError:
        return False
    for line in lines:
        k, _, v = (x.strip() for x in re.sub(r"(^|\s)#.*$", "", line).partition("="))
        if k in ("whitelist", "read_only") and v:
            q = os.path.realpath(HOME + v[1:] if v == "~" or v.startswith("~/") else v)
            if q == "/" or (base + "/").startswith(q.rstrip("/") + "/"): return True
    return False


def ask_about_conf(name, conf, dest):
    """The settings file of NAME is left from an earlier install and doesn't share DEST. Ask: 'add' its folder line, start 'fresh', or None
    (don't install). On a terminal it asks there; from the file manager it uses zenity or kdialog; with neither it adds the line (nothing is lost)."""
    q = f"Old settings for {name} don't share {tilde(dest)}."
    if sys.stdin.isatty() and sys.stderr.isatty():
        a = input(f"{q}\n  [a] add it, keep your other settings   [f] fresh settings   [n] don't install\nChoice [A/f/n] ").strip().lower()
        return "fresh" if a.startswith("f") else None if a.startswith("n") else "add"
    for cmd in (["zenity", "--question", "--title=sbx", "--text=" + q + "\nAdd it?", "--ok-label=Add it", "--cancel-label=Cancel"],
                ["kdialog", "--yesno", q + "\nAdd it?", "--title", "sbx", "--yes-label", "Add it", "--no-label", "Cancel"]):
        if shutil.which(cmd[0]):
            try: return "add" if subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=300).returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired): return None
    return "add"


def ask_without_terminal(q, title, ok):
    """A yes/no question where there is no terminal (the file manager's Install in sbx): zenity, kdialog, or a notification with a button
    (notify-send --action). True = yes, False = no or closed, None = nothing here can ask."""
    for cmd in (["zenity", "--question", "--title=sbx", "--text=" + q, "--ok-label=" + ok, "--cancel-label=Cancel"],
                ["kdialog", "--yesno", q, "--title", "sbx", "--yes-label", ok, "--no-label", "Cancel"]):
        if shutil.which(cmd[0]):
            try: return subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=300).returncode == 0
            except (OSError, subprocess.TimeoutExpired): return False
    ns = shutil.which("notify-send")
    if ns:
        try:
            if "--action" in subprocess.run([ns, "--help"], capture_output=True, text=True, timeout=5).stdout:
                r = subprocess.run([ns, "-u", "critical", "-a", "sbx", "--wait", f"--action=yes={ok}", title, q],
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
                return r.stdout.strip() == "yes"
        except (OSError, subprocess.SubprocessError): pass
    return None


def ask_about_same_name(name, other):
    """A different program called NAME (OTHER, in your PATH) is already on this computer; the installed one would share its private home and
    settings. Ask whether to go on: True = install, False = don't. With no way to ask (no terminal, no dialog) it doesn't install."""
    q = (f"A program called {name} is already on this computer ({tilde(other)}). The one you install would share its private home "
         f"({tilde(HOMES)}/{name}) and settings; two versions of one program can damage each other's profile.")
    if sys.stdin.isatty() and sys.stderr.isatty():
        return input(f"{q}\nInstall it anyway? [y/N] ").strip().lower() in ("y", "yes")
    got = ask_without_terminal(q + "\nInstall it anyway?", f"Install {name}?", "Install anyway")
    if got is None:
        die(f"a program called {name} is already on this computer ({tilde(other)}), and there is no way to ask here. Install in a terminal (sbx install ...) or add --yes")
    return got


def ask_to_install(name, src, dest):
    """The question before a normal install started from the file manager (on a terminal you typed the command, so nothing is asked).
    True = install. Where nothing can ask, it installs, as before."""
    q = f"Move {tilde(src)} to {tilde(dest)} and start it only through sbx (an app menu entry is added)?"
    got = ask_without_terminal(q, f"Install {name}?", "Install")
    return True if got is None else got


def ask_to_replace(name, dest):
    """Ask whether to replace an sbx install: True, False, or None when nothing can ask."""
    q = f"{name} is already installed in {tilde(dest)}. Replace it?"
    if sys.stdin.isatty() and sys.stderr.isatty(): return input(f"{q} [y/N] ").strip().lower() in ("y", "yes")
    return ask_without_terminal(q, f"Update {name}?", "Update")


def archive_stamp(src):
    """size:mtime of an archive, the way ajail stamps an AppImage; None for a folder."""
    if not os.path.isfile(src): return None
    st = os.stat(src); return f"{st.st_size}:{st.st_mtime_ns}"


def installed_stamp(name):
    try:
        with open(f"{INSTALLED}/{name}") as f: return (f.read().splitlines() + ["", ""])[1] or None
    except OSError:
        return None


def is_our_install(name, src, dest, link, want_link):
    made, menu = installed_launcher(name), f"{user_launchers()}/{name}.desktop"
    base = os.path.realpath(dest)
    if not made or not os.path.isdir(dest) or os.path.islink(dest) or not os.path.realpath(made).startswith(base + "/"): return False
    if (os.path.realpath(src) + "/").startswith(base + "/"): return False
    if os.path.lexists(menu) and "X-Sbx-Sandboxed=true" not in launcher_text(menu): return False
    return not (want_link and os.path.lexists(link) and not is_sbx_link(link))


def cmd_install(a):
    """sbx install FOLDER|ARCHIVE [NAME] [--launcher PATH] [--link] [--yes]: ~/Apps/NAME holds the program; its launcher loses the execute bit (so running
    it by path outside the sandbox fails, and sbx sets the bit just for its own run); an app-menu entry starts it through sbx. --link also makes
    NAME start it in a terminal (a link in ~/.local/bin, as sbx link does); nothing is put in your PATH without it.
    The program's folder is shared read-only (a settings file, read_only=...). Data goes to its private home, as for any program."""
    launcher, rest, i, want_link, popup, yes = None, [], 0, False, False, False
    while i < len(a):
        if a[i] == "--link": want_link = True; i += 1
        elif a[i] == "--notify": popup = True; i += 1
        elif a[i] == "--yes": yes = True; i += 1
        elif a[i] == "--launcher":
            if i + 1 >= len(a): die("--launcher needs a path")
            launcher = a[i + 1]; i += 2
        elif a[i].startswith("-"): die(f"unknown option {a[i]}\nusage: sbx install FOLDER|ARCHIVE [NAME] [--launcher PATH] [--link] [--yes]")
        else: rest.append(a[i]); i += 1
    if not 1 <= len(rest) <= 2: die("usage: sbx install FOLDER|ARCHIVE [NAME] [--launcher PATH] [--link] [--yes]")
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
    replace = False
    if not yes and is_our_install(name, src, dest, link, want_link):    # --yes or no way to ask: falls through to the refusal
        if archive_stamp(src) and archive_stamp(src) == installed_stamp(name):
            print(f"{name} is already installed in {tilde(dest)}"); return
        if running(name): die(f"{name} is running. Stop it first: sbx stop {name}")
        got = ask_to_replace(name, dest)
        if got is False: die("nothing installed", 0)
        replace = got is True
        want_link = want_link or (replace and is_sbx_link(link))
    for p, what in (() if replace else ((dest, "a program folder"), (link, "a command"), (f"{INSTALLED}/{name}", "an install record"), (f"{user_launchers()}/{name}.desktop", "a menu entry"))):
        if p == link and not want_link: continue
        if os.path.lexists(p): die(f"{tilde(p)} already exists ({what}). Pick another NAME, or remove the old one first: sbx uninstall {name}")
    other = None if yes else find_in_path(name)                         # a program of that name that is not this one: they would share a home
    if other:
        if not ask_about_same_name(name, other): die("nothing installed", 0)
    elif not yes and not replace and not (sys.stdin.isatty() and sys.stderr.isatty()):   # from the file manager nobody typed the command: ask once
        if not ask_to_install(name, src, dest): die("nothing installed", 0)
    conf = f"{CONF}/apps/{name}.conf"
    fix = None
    if os.path.exists(conf) and not conf_shares(conf, dest):
        fix = "add" if yes else ask_about_conf(name, conf, dest)
        if not fix: die("nothing installed", 0)
    os.makedirs(APPS, exist_ok=True)
    from_dir = os.path.isdir(src)
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
        if replace: cmd_uninstall([name, "--yes"] + (["--trash"] if shutil.which("gio") else []))    # after unpacking, so a bad archive keeps the old one
        progress(f"moving it to {tilde(dest)}...", final=not sys.stderr.isatty())
        if os.path.isdir(src): shutil.move(root, dest)
        else: os.rename(root, dest)
    finally:
        if tmp: shutil.rmtree(tmp, ignore_errors=True)
    made_conf = not os.path.exists(conf)
    try:
        print(f"program is in {tilde(dest)}" + (f" (moved from {tilde(src)})" if from_dir else ""))
        main = f"{dest}/{rel}"
        if elf_interpreter(main):
            os.chmod(main, os.stat(main).st_mode & ~0o111)
            print(f"took the execute bit off {rel}: starting it by its path outside sbx now fails; sbx sets the bit for its own run")
        else:
            warn(f"{rel} keeps its execute bit: sbx can only start a program without it if it is a dynamically linked ELF file")
        os.makedirs(INSTALLED, exist_ok=True)
        stamp = archive_stamp(src)
        with open(f"{INSTALLED}/{name}", "w") as f: f.write(main + "\n" + (stamp + "\n" if stamp else ""))
        if fix == "fresh" or not os.path.exists(conf):
            os.makedirs(os.path.dirname(conf), exist_ok=True)
            with open(conf, "w") as f: f.write(f"read_only={tilde(dest)}\n")
            print(f"wrote {tilde(conf)}: the program's folder is shared read-only. If it must write there, change that line to folder=1")
        elif conf_shares(conf, dest):
            print(f"kept your settings in {tilde(conf)} (they already share {tilde(dest)})")
        else:                                                              # left over from an earlier install: add the line the program needs, keep the rest
            with open(conf) as f: old = f.read()
            with open(conf, "a") as f: f.write(("" if not old or old.endswith("\n") else "\n") + f"read_only={tilde(dest)}\n")
            print(f"added read_only={tilde(dest)} to {tilde(conf)} (your other settings there are unchanged), so the program finds its files")
        if want_link: cmd_link([name])
        path, icon = write_install_launcher(name, main, me)
        print(f"app menu: wrote {tilde(user_launchers())}/{name}.desktop (it starts the program through sbx)" + (f", icon {tilde(icon)}" if icon else ", no icon found in its folder: add Icon=... to that file"))
        print("Start it from the app menu" + (f" or with: {name}" if want_link else f". For a terminal command too: sbx link {name}") + f"\nUndo with: sbx uninstall {name}")
    except BaseException:                                                  # nothing half-installed stays behind
        progress(f"install of {name} failed; undoing it", final=True)
        for p in (f"{INSTALLED}/{name}", f"{user_launchers()}/{name}.desktop") + ((f"{CONF}/apps/{name}.conf",) if made_conf else ()):
            try: os.unlink(p)
            except OSError: pass
        remove_icons(name)
        if want_link and is_sbx_link(link): os.unlink(link)
        try:
            if from_dir: shutil.move(dest, src)                         # a folder goes back where it came from
            else: shutil.rmtree(dest, ignore_errors=True)
        except OSError: pass
        raise



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
    os.unlink(rec); remove_icons(name)
    if shutil.which("update-desktop-database"): subprocess.run(["update-desktop-database", user_launchers()], stderr=subprocess.DEVNULL)
    left = [x for x in (f"{HOMES}/{name}", f"{CONF}/apps/{name}.conf") if os.path.exists(x)]
    if left: print("kept " + " and ".join(tilde(x) for x in left) + f". Delete them with: sbx clean {name}; sbx reset {name}")
