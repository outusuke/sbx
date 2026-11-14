#| 75_run: Starting bwrap, signal handling, cleanup of what build() created.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- running it
def sweep_stale():
    """Remove helper files that a killed sbx left behind in $XDG_RUNTIME_DIR/sbx (named PID.bus, PID.xauth, PID.wl, PID.gsettings, PID.boot_id, PID.hostname, PID.hosts, PID.passwd, PID.resolv.conf, PID.seccomp)."""
    d = f"{runtime_dir()}/sbx"
    try: names = os.listdir(d)
    except OSError: return
    for n in names:
        m = re.fullmatch(r"(\d+)\.(bus|xauth|wl|gsettings|boot_id|hostname|hosts|passwd|resolv\.conf|seccomp)", n)
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


def tee_stderr(fd, name):
    """Copy a program's stderr to our own stderr and to the log, minus control characters, at most LOG_RUN_MAX bytes per run.
    Chunks, not lines, so a prompt without a newline shows at once."""
    lf = open_log()
    n, pend = 0, ""                               # pend: the unfinished line, kept short so a flood without newlines can't fill memory
    dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            data = os.read(fd, 4096)
            text = CTRL.sub("", dec.decode(data, final=not data))
            try: sys.stderr.write(text); sys.stderr.flush()
            except (OSError, ValueError): pass
            lines = (pend + text).split("\n"); pend = lines.pop()
            lines = [x + "\n" for x in lines]
            while len(pend) > LOG_LINE_MAX: lines.append(pend[:LOG_LINE_MAX] + "\n"); pend = pend[LOG_LINE_MAX:]
            if not data and pend: lines.append(pend + "\n"); pend = ""
            for line in lines:
                if lf and n < LOG_RUN_MAX:
                    n += len(line)
                    log_write(lf, f"[{name}] {line}" + (f"[{name}] (more output not logged)\n" if n >= LOG_RUN_MAX else ""))
            if not data: break
    except Exception:
        pass
    finally:
        if lf: lf.close()


def run(prog, args, opts):
    if os.geteuid() == 0: die("refusing to run as root")
    if os.environ.get("SBX_ACTIVE"): die("already inside an sbx sandbox")
    LOG_NAME[0] = clean(opts.get("name") or os.path.basename(prog))      # same name build() will use; from here on warnings and errors are logged too
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
    cfg, _ = effective(name, opts)
    log("START allow=[" + ",".join(k for k in SCALARS if cfg[k]) + "]", name)
    try: mark_running(name)
    except OSError: pass                          # only `sbx stop`, `sbx list` and the running-check of `sbx clean` need it
    start, stopped, child = time.time(), [False], [None]
    piped = not sys.stderr.isatty()               # started from the app menu or a file manager: no terminal, so the log keeps stderr
    try:
        def stop(*_):
            stopped[0] = True
            if child[0]: child[0].terminate()
        for sg in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(sg, stop)      # before the child exists, so no signal is missed
        p = child[0] = subprocess.Popen(st["prefix"] + st["wrap"] + cmd, pass_fds=st["fds"], stderr=subprocess.PIPE if piped else None)
        if stopped[0]: p.terminate()                  # a signal that came while it was starting
        t = threading.Thread(target=tee_stderr, args=(p.stderr.fileno(), name), daemon=True) if piped else None
        if t: t.start()
        rc = p.wait()
        if t: t.join(2)
    finally:
        unmark_running(); teardown(st); undo_exec_bit(prog, restore)
    code, sig = exit_code(rc), crash_signal(rc)
    log(f"EXIT code={code}" + (f" ({sig})" if sig else ""), name)
    if not stopped[0] and not os.environ.get("SBX_NO_POPUP"):
        if sig: notify_crash(name, sig)                                  # at any time, not only right after starting
        elif rc != 0 and time.time() - start < 10: notify_failure(name)
        elif rc != 0 and piped: notify_ended(name, code)                 # no terminal to show the error, so say it
    sys.exit(code)
