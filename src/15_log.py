#| 15_log: The launch log (~/.local/state/sbx/sbx.log) and the "failed to start" notification. No sandbox knowledge.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---------------------------------------------------------------- log
STATE = f"{HOME}/.local/state/sbx"
LOG = f"{STATE}/sbx.log"
LOG_KEEP = 500                  # lines kept when the file passes LOG_MAX
LOG_MAX = 1_000_000             # bytes
LOG_RUN_MAX = 2_000_000         # bytes of one run's output kept in the log
LOG_LINE_MAX = 65_536           # longest log line: longer output is split, so one unbroken stream can't pile up in memory
# Control characters (C0 except tab/newline, DEL and C1) are stripped from program output before it reaches the log or our
# own stderr, so a program can't inject terminal escape sequences (OSC 52 and the like).
CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
LOG_NAME = [None]               # set by run(): warnings and errors of this run are logged under this name. None: not logging
                                # (preview, show, doctor and the GUI's calls must not write to the log)


def open_log():
    """The log, opened for appending (owner-only, never through a link), or None."""
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        try:
            if os.path.getsize(LOG) > LOG_MAX:
                with open(LOG, errors="replace") as f: tail = f.readlines()[-LOG_KEEP:]
                fd = os.open(LOG + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w") as f: f.writelines(tail)
                os.replace(LOG + ".tmp", LOG)
        except OSError:
            pass
        return os.fdopen(os.open(LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600), "a", errors="replace")
    except OSError:
        return None


def log(msg, name=None):
    f = open_log()
    if not f: return
    with f:
        msg = CTRL.sub("", msg).replace("\n", " ")
        f.write(f"[{time.strftime('%F %T')}] " + (f"[{name}] " if name else "") + f"{msg}\n")


JOURNAL_SOCK = "/run/systemd/journal/stdout"


def journal_tag(ident, sock=JOURNAL_SOCK):
    """Started from the app menu or a file manager, our output goes to the journal under the launcher's name (sbx-run.desktop).
    Reconnect those streams, and only those, so lines carry IDENT (the program's name) instead. Anything else is left alone."""
    mine = os.environ.get("JOURNAL_STREAM", "")      # not always set (GNOME's launcher doesn't), so the socket's address counts too

    def is_journal(fd):
        st = os.fstat(fd)
        if mine == f"{st.st_dev}:{st.st_ino}": return True
        if st.st_mode & 0o170000 != 0o140000: return False      # not a socket
        try:
            with socket.socket(fileno=os.dup(fd)) as c: return c.getpeername() == sock
        except OSError: return False
    try:
        fds = [fd for fd in (1, 2) if is_journal(fd)]
        if not fds: return
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with s:
            s.connect(sock)
            s.sendall(f"{ident.replace(chr(10), ' ')}\n\n6\n0\n0\n0\n0\n".encode())      # name, unit, priority, then four flags off
            s.shutdown(socket.SHUT_RD)
            for f in (sys.stdout, sys.stderr):
                try: f.flush()
                except Exception: pass
            for fd in fds: os.dup2(s.fileno(), fd)
        st = os.fstat(fds[0])
        os.environ["JOURNAL_STREAM"] = f"{st.st_dev}:{st.st_ino}"
    except OSError:
        pass


# ---------------------------------------------------------------- notification
def notify_failure(name, reason=""):
    """A critical notification that the program stopped right after starting, with the REASON when there is one. Where
    notify-send can do actions, clicking it opens sbx-gui on this program's log. The waiting for the click happens in a
    detached child, so sbx can exit at once."""
    ns = shutil.which("notify-send")
    if not ns: return
    why = f" {reason[:300]}" if reason else ""
    msg = f"{name} stopped right after starting.{why} Click to open the log."
    gui = shutil.which("sbx-gui") or os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), "sbx-gui")
    try: actions = "--action" in subprocess.run([ns, "--help"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError): actions = False
    if not actions or not os.access(gui, os.X_OK):
        try: subprocess.run([ns, "-u", "critical", "-a", "sbx", "sbx", f"{name} stopped right after starting.{why} See the log in sbx-gui."], timeout=5, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError): pass
        return
    try:
        if os.fork(): return                      # the parent carries on and exits
    except OSError:
        return
    try:                                          # the child waits for the click, detached from the terminal
        os.setsid()
        dn = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2): os.dup2(dn, fd)
        r = subprocess.run([ns, "-u", "critical", "-a", "sbx", "-A", "default=Open log", "--wait", "sbx", msg],
                           capture_output=True, text=True, timeout=900)
        if r.stdout.strip() == "default": subprocess.Popen([gui, "--log", name], start_new_session=True)
    except Exception:
        pass
    finally:
        os._exit(0)
