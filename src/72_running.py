#| 72_running: Which programs are running right now: one small file per sbx process, checked against the process's start time.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- running programs
RUNNING = f"{STATE}/running"      # RUNNING/PID: "NAME\nSTARTTIME" of an sbx process, then "\nPID\nSTARTTIME" of its sandbox, then the same for the sandbox's init
STOP_GRACE = 5      # seconds between SIGTERM and SIGKILL when sbx is told to stop


def proc_start(pid):
    """When PID started: field 22 of /proc/PID/stat (clock ticks since boot, as text), or None. A reused PID gets another one."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f: rest = f.read().rsplit(b")", 1)[1].split()      # field 2, the name, may hold spaces and ')'
        return rest[19].decode()                                                                    # rest[0] is field 3
    except (OSError, IndexError, UnicodeDecodeError):
        return None


def live_sbx(pid):
    """True if this PID is really an sbx process (a stale file may point at a reused PID). Started by its name (`sbx`, `python3
    sbx`) or through a link made by `sbx link`, whose command line has only the link's name (`python3 ~/.local/bin/firefox`)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f: argv = [x.decode(errors="replace") for x in f.read().split(b"\0") if x]
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return False
    return any(os.path.basename(x) == "sbx" or is_sbx_link(os.path.join(cwd, x)) for x in argv[:3])


def live_pid(entry):
    """The PID to signal for the run in RUNNING/ENTRY: its sbx process, else its sandbox if sbx is gone; None when neither is alive."""
    try:
        with open(f"{RUNNING}/{entry}") as f: lines = [x.strip() for x in f.read().split("\n")] + [""] * 6
    except OSError:
        return None
    if lines[1] and lines[1] == proc_start(entry) and live_sbx(entry): return int(entry)
    for i in (2, 4):
        if lines[i].isdigit() and lines[i + 1] and lines[i + 1] == proc_start(lines[i]): return int(lines[i])
    return None


def pid_app(pid):
    """The program name in RUNNING/PID while that run is alive (see live_pid)."""
    if live_pid(pid) is None: return None
    try:
        with open(f"{RUNNING}/{pid}") as f: return f.readline().strip()
    except OSError:
        return None


def running(name=None):
    """PIDs of the sbx processes running NAME (every program when NAME is None)."""
    try: names = [f for f in os.listdir(RUNNING) if f.isdigit()]
    except OSError: return []
    return [p for p in names if (pid_app(p) == name if name else pid_app(p))]


def mark_running(name):
    """Note that this process runs NAME, and drop the files of processes that are gone."""
    os.makedirs(RUNNING, mode=0o700, exist_ok=True)
    for f in os.listdir(RUNNING):
        if f.isdigit() and not pid_app(f):
            try: os.unlink(f"{RUNNING}/{f}")
            except OSError: pass
    with open(f"{RUNNING}/{os.getpid()}", "w") as f: f.write(f"{name}\n{proc_start(os.getpid()) or ''}")


def mark_child(pid):
    """Add the sandbox process to our file, so the run still counts if sbx dies before it."""
    try:
        with open(f"{RUNNING}/{os.getpid()}", "a") as f: f.write(f"\n{pid}\n{proc_start(pid) or ''}")
    except OSError: pass


def unmark_running():
    try: os.unlink(f"{RUNNING}/{os.getpid()}")
    except OSError: pass


def descendants(pid):
    """[(pid, start)] of every process below PID, found through the parent links in /proc."""
    kids = {}
    for f in os.listdir("/proc"):
        if not f.isdigit(): continue
        try:
            with open(f"/proc/{f}/stat", "rb") as h: rest = h.read().rsplit(b")", 1)[1].split()
            kids.setdefault(int(rest[1]), []).append((int(f), rest[19].decode()))
        except (OSError, IndexError, ValueError): pass
    found, todo = [], [int(pid)]
    while todo:
        for c in kids.get(todo.pop(), []): found.append(c); todo.append(c[0])
    return found


def sandbox_init(path):
    """(pid, start) of the sandbox's init, from the child-pid bwrap wrote to PATH; None if it isn't there yet or isn't pid 1 of its namespace."""
    try:
        with open(path) as f: pid = int(json.loads(f.readline())["child-pid"])
        with open(f"/proc/{pid}/status") as f: nspid = [l.split() for l in f if l.startswith("NSpid:")]
    except (OSError, ValueError, KeyError, TypeError): return None
    start = proc_start(pid)
    return (pid, start) if start and nspid and nspid[0][-1] == "1" else None


def wait_exit(pid, start, grace):
    """True if PID (the process with this START) ends within GRACE seconds; returns at the moment it ends."""
    fd = None
    if hasattr(os, "pidfd_open"):
        try: fd = os.pidfd_open(pid)
        except OSError: pass
    try:
        if proc_start(pid) != start: return True
        if fd is not None: return bool(select.select([fd], [], [], grace)[0])
        end = time.time() + grace
        while proc_start(pid) == start:
            if time.time() >= end: return False
            time.sleep(0.02)
        return True
    finally:
        if fd is not None: os.close(fd)


def signal_tree(tree, sig):
    for pid, start in tree:
        if proc_start(pid) != start: continue
        try: os.kill(pid, sig)
        except OSError: pass


def finish_tree(tree, grace):
    """Give the processes in TREE GRACE seconds to end, then SIGKILL the rest."""
    end = time.time() + grace
    while time.time() < end and any(proc_start(pid) == start for pid, start in tree): time.sleep(0.1)
    signal_tree(tree, signal.SIGKILL)


def stop_program(name):
    """SIGTERM every running sbx process of NAME; returns how many were signalled. A PID can be reused between listing the
    processes and signalling one, so each PID is first pinned with a pidfd and only then checked against its file: the
    signal goes to the process that was checked, never to a newcomer that took the number."""
    sent, left = 0, []
    use_fd = hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")
    for entry in running(name):
        pid = live_pid(entry)
        if pid is None: continue
        below = descendants(pid) if pid != int(entry) else []          # sbx is gone: what its sandbox started must be stopped too
        fd = None
        try:
            if use_fd:
                try: fd = os.pidfd_open(pid)
                except ProcessLookupError: continue
                except OSError: pass                     # no pidfd on this kernel: plain kill below
            if live_pid(entry) != pid: continue
            if fd is None: os.kill(pid, signal.SIGTERM)
            else: signal.pidfd_send_signal(fd, signal.SIGTERM)
            sent += 1
            if pid != int(entry): left += [(pid, proc_start(pid))] + below      # no sbx left to follow up with SIGKILL
            signal_tree(below, signal.SIGTERM)
        except OSError:
            pass
        finally:
            if fd is not None: os.close(fd)
    finish_tree(left, STOP_GRACE)
    return sent
