#| 72_running: Which programs are running right now: one small file per sbx process, checked against the process's start time.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- running programs
RUNNING = f"{STATE}/running"      # RUNNING/PID holds "NAME\nSTARTTIME" for each live sbx process


def proc_start(pid):
    """When PID started: field 22 of /proc/PID/stat (clock ticks since boot, as text), or None. A reused PID gets another one."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f: rest = f.read().rsplit(b")", 1)[1].split()      # field 2, the name, may hold spaces and ')'
        return rest[19].decode()                                                                    # rest[0] is field 3
    except (OSError, IndexError, UnicodeDecodeError):
        return None


def live_sbx(pid):
    """True if this PID is really an sbx process (a stale file may point at a reused PID)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f: return b"sbx" in f.read()
    except OSError:
        return False


def pid_app(pid):
    """The program name in RUNNING/PID, if that file was written by the process that is still running under this PID."""
    try:
        with open(f"{RUNNING}/{pid}") as f: name, _, start = f.read().partition("\n")
    except OSError:
        return None
    start = start.strip()
    return name.strip() if start and start == proc_start(pid) and live_sbx(pid) else None


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


def unmark_running():
    try: os.unlink(f"{RUNNING}/{os.getpid()}")
    except OSError: pass


def stop_program(name):
    """SIGTERM every running sbx process of NAME; returns how many were signalled. A PID can be reused between listing the
    processes and signalling one, so each PID is first pinned with a pidfd and only then checked against its file: the
    signal goes to the process that was checked, never to a newcomer that took the number."""
    sent = 0
    use_fd = hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")
    for pid in [int(p) for p in running(name)]:
        fd = None
        try:
            if use_fd:
                try: fd = os.pidfd_open(pid)
                except ProcessLookupError: continue
                except OSError: pass                     # no pidfd on this kernel: plain kill below
            if pid_app(pid) != name: continue
            if fd is None: os.kill(pid, signal.SIGTERM)
            else: signal.pidfd_send_signal(fd, signal.SIGTERM)
            sent += 1
        except OSError:
            pass
        finally:
            if fd is not None: os.close(fd)
    return sent
