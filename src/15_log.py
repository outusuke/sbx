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
    """The log, opened for appending (owner-only, never through a link), or None. Never replaced or renamed: several sbx
    processes share it, and a handle that outlives a replaced file would keep writing into a file nobody reads."""
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        fd = os.open(LOG, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a+", errors="replace")
    except OSError:
        return None


def log_write(f, text):
    """Append TEXT to the open log F under a lock. When the file is over LOG_MAX it is cut down to its last LOG_KEEP lines
    first, in place (truncate, not replace), so another sbx cannot cut a line in half and no handle is left on an old file."""
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            if os.fstat(f.fileno()).st_size > LOG_MAX:
                f.seek(0); tail = f.readlines()[-LOG_KEEP:]
                f.truncate(0); f.writelines(tail)
            f.write(text); f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    except OSError:
        pass


def log(msg, name=None):
    f = open_log()
    if not f: return
    with f:
        msg = CTRL.sub("", msg).replace("\n", " ")
        log_write(f, f"[{time.strftime('%F %T')}] " + (f"[{name}] " if name else "") + f"{msg}\n")


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
                except (OSError, ValueError): pass
            for fd in fds: os.dup2(s.fileno(), fd)
        st = os.fstat(fds[0])
        os.environ["JOURNAL_STREAM"] = f"{st.st_dev}:{st.st_ino}"
    except OSError:
        pass


# ---------------------------------------------------------------- notification
def notify_click(plain, clicked, label, gui_args, urgency="critical"):
    """A desktop notification. Where notify-send can do actions, it says CLICKED and a click on the LABEL button opens sbx-gui
    with GUI_ARGS; the waiting for the click happens in a detached child, so sbx can exit at once. Without actions (an old
    notify-send, or no sbx-gui to open) it is a plain popup saying PLAIN. Nothing happens without notify-send."""
    ns = shutil.which("notify-send")
    if not ns: return
    gui = shutil.which("sbx-gui") or os.path.join(os.path.dirname(os.path.realpath(sys.argv[0])), "sbx-gui")
    try: actions = "--action" in subprocess.run([ns, "--help"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError): actions = False
    if not actions or not os.access(gui, os.X_OK):
        try: subprocess.run([ns, "-u", urgency, "-a", "sbx", "sbx", plain], timeout=5, stdin=subprocess.DEVNULL)
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
        r = subprocess.run([ns, "-u", urgency, "-a", "sbx", "-A", f"default={label}", "--wait", "sbx", clicked],
                           capture_output=True, text=True, timeout=900)
        if r.stdout.strip() == "default": subprocess.Popen([gui, *gui_args], start_new_session=True)
    except Exception:
        pass
    finally:
        os._exit(0)


def notify_failure(name, reason=""):
    """A critical notification that the program stopped right after starting, with the REASON when there is one. Where
    notify-send can do actions, clicking it opens sbx-gui on this program's log."""
    why = f" {reason[:300]}" if reason else ""
    notify_click(f"{name} stopped right after starting.{why} See the log in sbx-gui.",
                 f"{name} stopped right after starting.{why} Click to open the log.", "Open log", ["--log", name])
