#| 75_run: Starting bwrap, signal handling, cleanup of what build() created.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- running it
def sweep_stale():
    """Remove helper files that a killed sbx left behind in $XDG_RUNTIME_DIR/sbx (named PID.bus, PID.xauth, PID.wl)."""
    d = f"{runtime_dir()}/sbx"
    try: names = os.listdir(d)
    except OSError: return
    for n in names:
        m = re.fullmatch(r"(\d+)\.(bus|xauth|wl)", n)
        if not m: continue
        try: os.kill(int(m.group(1)), 0); continue          # still running
        except ProcessLookupError: pass
        except OSError: continue
        try: os.unlink(f"{d}/{n}")
        except OSError: pass


def teardown(st):
    stop_proxy(st["proxy"], st["bus"])
    if st["wl"]: st["wl"].close()
    for f in st["files"]:
        try: os.unlink(f)
        except OSError: pass
    for p in reversed(st["cleanup"]):           # mountpoints bwrap made in the private home, if still empty
        try:
            if os.path.isdir(p) and not os.path.islink(p): os.rmdir(p)
            elif os.path.isfile(p) and os.path.getsize(p) == 0: os.unlink(p)
        except OSError:
            pass


def give_exec_bit(path):
    """For a dynamically linked ELF file without the execute bit: set the bit for this run and return the old mode.
    Returns False if it can't be set (the caller then uses the loader), None if there is nothing to do. Running through the
    loader instead would make /proc/self/exe the loader, which breaks programs that read their own file (.NET single-file apps)."""
    if not os.path.isfile(path) or os.access(path, os.X_OK) or not elf_interpreter(path): return None
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | 0o100)
        return mode
    except OSError:
        return False


def undo_exec_bit(prog, mode):
    if mode is not None:
        try: os.chmod(os.path.abspath(prog), mode)
        except OSError: pass


def run(prog, args, opts):
    if os.geteuid() == 0: die("refusing to run as root")
    if os.environ.get("SBX_ACTIVE"): die("already inside an sbx sandbox")
    if not shutil.which("bwrap"): die(f"bubblewrap missing: {install_hint('bubblewrap')}")
    if platform.machine() not in ARCHES: die(f"no system-call filter for {platform.machine()}; not starting without it")
    sweep_stale()
    os.makedirs(HOMES, mode=0o700, exist_ok=True)
    restore = None
    if not opts.get("loader") and "/" in prog:
        restore = give_exec_bit(os.path.abspath(prog))      # a dynamically linked ELF file without the execute bit
        if restore is False:                                # can't change it: go through the loader instead
            opts, restore = {**opts, "loader": elf_interpreter(os.path.abspath(prog))}, None
    try:
        name, cmd, st = build(prog, args, True, opts)
    except BaseException:
        undo_exec_bit(prog, restore); raise
    if st["prefix"] and not shutil.which("systemd-run"):
        teardown(st); undo_exec_bit(prog, restore); die(f"memory/tasks/cpu limits need systemd-run: {install_hint('systemd')}")
    os.makedirs(f"{HOMES}/{name}", mode=0o700, exist_ok=True)
    try:
        p = subprocess.Popen(st["prefix"] + cmd, pass_fds=st["fds"])
        for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(sg, lambda *_: p.terminate())
        rc = p.wait()
    finally:
        teardown(st); undo_exec_bit(prog, restore)
    sys.exit(exit_code(rc))
