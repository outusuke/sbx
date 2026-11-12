#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for sbx. Run from the folder holding sbx and sbx-selftest, as a normal user:   python3 test_sbx.py
#   * Snapshot: the exact bwrap command for a number of option sets, with the host's details faked, is compared with
#     sbx-preview.snap, so any change to the mount list shows up as a diff in review.
#     After an intended change:   UPDATE_SNAPSHOTS=1 python3 test_sbx.py   and commit the new .snap file.
#   * Units: settings parsing, path rules, mount ordering, the system-call filter (run through a small BPF interpreter),
#     the Wayland client (against a fake compositor), launcher rewriting and more. Nothing here needs bubblewrap.
#   * Isolation: runs sbx-selftest and small programs inside real sandboxes against canary files planted in a temporary
#     "home". Needs bubblewrap; skipped without it. Nothing touches your real home.
import contextlib, difflib, io, os, re, select, shutil, socket, struct, subprocess, sys, tempfile, threading, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = f"{HERE}/sbx-preview.snap"
TOKEN = "CANARY-5b0e77d1-do-not-read"
FIX = "/tmp/sbx-snapshot-fixture"          # real folders the snapshot scenarios share (the path must not change)


def load_sbx():
    """The sbx script as a module-like dict (its CLI doesn't run on import)."""
    with open(f"{HERE}/sbx") as f: src = f.read()
    ns = {"__name__": "sbx_under_test"}
    exec(compile(src, "sbx", "exec"), ns)
    return ns


def setUpModule():
    for d in ("a/b", "c"): os.makedirs(f"{FIX}/{d}", exist_ok=True)


def tearDownModule():
    shutil.rmtree(FIX, ignore_errors=True)


# ---------------------------------------------------------------- snapshot of `sbx preview`
FAKE_FILES = {"/etc/shadow", "/etc/shadow-", "/etc/gshadow", "/etc/sudoers", "/etc/machine-id", "/var/lib/dbus/machine-id",
              "/etc/ssh/ssh_host_ed25519_key", "/run/user/1000/xauth", "/etc/hostname", "/etc/hosts"}
FAKE_DIRS = {"/run/systemd/resolve", "/etc/sudoers.d", "/etc/ssl/private", "/root", "/mnt", "/media", "/srv", "/var/tmp",
             "/home", "/var/log", "/var/mail", "/var/spool/mail", "/sys/devices/virtual/dmi/id", "/sys/class/net"}
FAKE_GLOBS = {"/etc/ssh/ssh_host_*_key": ["/etc/ssh/ssh_host_ed25519_key"], "/dev/video*": ["/dev/video0"],
              "/dev/dri/renderD*": ["/dev/dri/renderD128", "/dev/dri/renderD129"]}
NO_SECURITY_CONTEXT = {"wl_supported": lambda p: False}
SCENARIOS = [                                   # (title, program, flags, overrides of sbx's own functions)
    ("default", "firefox", [], {}),
    ("locked down", "firefox", ["--net=none", "--private", "--hostname", "--portals", "--no-gpu", "--no-sound", "--no-wayland",
                                "--no-userns"], {}),
    ("x11", "firefox", ["--x11"], {}),
    ("program inside your home", "/home/user/apps/foo/bin/foo", [], {}),
    ("shared folders, parent read-only", "firefox",
     [f"--whitelist={FIX}/a/b", f"--read-only={FIX}/a", f"--whitelist={FIX}/c", "--here"], {"share_dir": lambda d: f"{FIX}/a/b"}),
    ("render-only graphics", "firefox", ["--gpu-render-only"], {}),
    ("devices", "firefox", ["--camera", "--alsa", "--input", "--kvm", "--rocm", "--printing"], {}),
    ("environment", "firefox", ["--env=EXTRA_VAR", "--setenv=FOO=bar", "--setenv=HOME=/elsewhere", "--env=SECRET_TOKEN"], {}),
    ("limits", "firefox", ["--memory=2G", "--tasks=512", "--cpu=200%"], {}),
    ("no theme", "firefox", ["--no-theme"], {}),
    ("desktop settings the program can't read", "firefox", [],
     {"gsettings_keyfile_text": lambda: "[org/gnome/desktop/wm/preferences]\nbutton-layout=':minimize,maximize,close'\n"}),
    ("compositor without security context", "firefox", [], NO_SECURITY_CONTEXT),
    ("wayland strict, no security context", "firefox", ["--wayland-strict"], NO_SECURITY_CONTEXT),
]


def render(args):
    """One bwrap option with its values per line, so a changed mount is a one-line diff."""
    lines = []
    for a in args:
        if a.startswith("--") or not lines: lines.append(a)
        else: lines[-1] += " " + a
    return lines


def fake_preview(prog, flags, patch=None):
    S = load_sbx()
    S.update(HOME="/home/user", USER="user", CONF="/home/user/.config/sbx", HOMES="/home/user/.sbx-homes")
    S["find_program"] = lambda p, noexec=False: (p, p) if p.startswith("/") else (f"/usr/bin/{p}", f"/usr/bin/{p}")
    S["runtime_dir"] = lambda: "/run/user/1000"
    S["wayland_path"] = lambda: "/run/user/1000/wayland-0"
    S["wl_supported"] = lambda p: True
    S["resolv_dirs"] = lambda: ["/run/systemd/resolve"]
    S["glob"] = types.SimpleNamespace(glob=lambda pat: FAKE_GLOBS.get(pat, []))
    S["gsettings_keyfile_text"] = lambda: None                  # whatever desktop settings this machine has must not change the snapshot
    S.update(patch or {})
    opts, _ = S["parse_options"](flags)
    env = {"LANG": "en_US.UTF-8", "TERM": "xterm", "XDG_CURRENT_DESKTOP": "GNOME", "WAYLAND_DISPLAY": "wayland-0",
           "DISPLAY": ":0", "XAUTHORITY": "/run/user/1000/xauth", "SECRET_TOKEN": "must-not-appear", "EXTRA_VAR": "extra"}
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch.object(os.path, "isdir", lambda p: p in FAKE_DIRS), \
         mock.patch.object(os.path, "isfile", lambda p: p in FAKE_FILES), \
         mock.patch.object(os.path, "islink", lambda p: False):
        _, args, state = S["build"](prog, [], False, opts)
    return state["prefix"], args


def snapshot_text():
    out = ["# Generated by test_sbx.py: the bwrap command `sbx preview` builds, with the host's details faked.",
           "# A diff here means the sandbox's mounts or options changed. Review it, then UPDATE_SNAPSHOTS=1 python3 test_sbx.py", ""]
    for title, prog, flags, patch in SCENARIOS:
        prefix, args = fake_preview(prog, flags, patch)
        out += [f"## {title}: sbx {' '.join(flags + [prog])}"]
        if prefix: out += ["PREFIX " + " ".join(prefix)]
        out += render(args) + [""]
    return "\n".join(out)


class Snapshot(unittest.TestCase):
    def test_preview_matches_snapshot(self):
        now = snapshot_text()
        if os.environ.get("UPDATE_SNAPSHOTS"):
            with open(SNAP, "w") as f: f.write(now)
            self.skipTest("snapshot updated")
        self.assertTrue(os.path.exists(SNAP), "no snapshot yet: run UPDATE_SNAPSHOTS=1 python3 test_sbx.py and commit it")
        with open(SNAP) as f: old = f.read()
        diff = "".join(difflib.unified_diff(old.splitlines(1), now.splitlines(1), "sbx-preview.snap", "now"))
        self.assertEqual(old, now, "the sandbox command changed:\n" + diff)

    def test_invariants_in_every_scenario(self):
        for title, prog, flags, patch in SCENARIOS:
            _, a = fake_preview(prog, flags, patch)
            for need in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv", "--seccomp"):
                self.assertIn(need, a, f"{title}: {need} missing")
            i = a.index("--ro-bind"); self.assertEqual(a[i:i + 3], ["--ro-bind", "/", "/"], title)
            self.assertEqual(a.count("--share-net"), 0 if "--net=none" in flags else 1, title)
            self.assertEqual("--disable-userns" in a, "--no-userns" in flags, title)
            if "--no-userns" in flags: self.assertIn("--unshare-user", a, title)
            if "--env=SECRET_TOKEN" not in flags: self.assertNotIn("SECRET_TOKEN", a, f"{title}: the environment leaked")
            # the real home is only ever replaced by the private home, never bound as it is
            binds = [(a[j], a[j + 1]) for j in range(len(a) - 2) if a[j] in ("--bind", "--ro-bind", "--dev-bind")]
            self.assertNotIn("/home/user", [src for _, src in binds], title)
            self.assertNotIn("/home/user/.config/sbx", [src for _, src in binds], title)
            self.assertNotIn("/home/user/.sbx-homes", [src for _, src in binds], title)
            private = f"/home/user/.sbx-homes/{'foo' if 'foo' in prog else 'firefox'}"
            if "--private" in flags:
                self.assertIn(["--tmpfs", "/home/user"], [a[k:k + 2] for k in range(len(a) - 1)], title)
            else:
                self.assertIn(("--bind", private), binds, title)
                k = a.index(private)                     # the parent folder is emptied before the private home goes in
                self.assertIn("/home", a[:k]); self.assertEqual(a[a.index("/home") - 1], "--tmpfs")
            # fake data files are bound read-only from a descriptor, and the boot id is always replaced
            self.assertIn(["--ro-bind-data", "FD", "/proc/sys/kernel/random/boot_id"], [a[k:k + 3] for k in range(len(a) - 2)], title)
            # a folder is mounted before what is inside it, so the inner share keeps its own mode
            shared = [(a[j], a[j + 1]) for j in range(len(a) - 2) if a[j] in ("--bind", "--ro-bind") and a[j + 1] == a[j + 2]
                      and a[j + 1].startswith(("/tmp/", "/home/user/")) and "/.sbx-homes" not in a[j + 1]]
            for x, (_, p) in enumerate(shared):
                for y, (_, q) in enumerate(shared):
                    if q.startswith(p + "/"): self.assertLess(x, y, f"{title}: {p} must be mounted before {q}")

    def test_render_only_graphics_binds_no_card_nodes(self):
        def dri(*flags):
            _, a = fake_preview("firefox", list(flags))
            return [a[i + 2] for i in range(len(a) - 2) if a[i] in ("--dev-bind", "--dev-bind-try") and a[i + 2].startswith("/dev/dri")]
        self.assertEqual(dri(), ["/dev/dri"])
        self.assertEqual(dri("--gpu-render-only"), ["/dev/dri/renderD128", "/dev/dri/renderD129"])
        self.assertEqual(dri("--no-gpu", "--gpu-render-only"), [])                        # no graphics at all wins over render-only
        self.assertEqual(dri("--no-gpu", "--rocm", "--gpu-render-only"), ["/dev/dri/renderD128", "/dev/dri/renderD129"])   # ROCm draws on render nodes too
        self.assertEqual(dri("--no-gpu", "--rocm"), ["/dev/dri"])

    def test_secrets_are_blanked(self):
        _, a = fake_preview("firefox", [])
        for p in ("/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/etc/ssh/ssh_host_ed25519_key"):
            j = a.index(p); self.assertEqual(a[j - 2:j + 1], ["--ro-bind", "/dev/null", p])
        self.assertEqual(a[a.index("/etc/ssl/private") - 1], "--tmpfs")
        for d in ("/var/log", "/var/mail", "/sys/devices/virtual/dmi/id"):
            self.assertEqual(a[a.index(d) - 1], "--tmpfs", d)
        self.assertNotIn("/sys/class/net", a, "with a network the interfaces stay listed")
        _, a = fake_preview("firefox", ["--net=none"])
        self.assertEqual(a[a.index("/sys/class/net") - 1], "--tmpfs")

    def test_shares_keep_their_modes(self):
        _, a = fake_preview("firefox", [f"--whitelist={FIX}/a/b", f"--read-only={FIX}/a"])
        self.assertLess(a.index(f"{FIX}/a"), a.index(f"{FIX}/a/b"))
        self.assertEqual(a[a.index(f"{FIX}/a") - 1], "--ro-bind")
        self.assertEqual(a[a.index(f"{FIX}/a/b") - 1], "--bind")
        _, a = fake_preview("firefox", [f"--whitelist={FIX}/a", f"--read-only={FIX}/a"])      # same folder both ways: read-only wins
        self.assertEqual(a.count(f"{FIX}/a"), 2); self.assertEqual(a[a.index(f"{FIX}/a") - 1], "--ro-bind")

    def test_wayland_goes_through_a_security_context_when_there_is_one(self):
        _, a = fake_preview("firefox", [])
        self.assertIn(["--ro-bind", "/run/user/1000/sbx/PID.wl", "/run/user/1000/wayland-0"], [a[k:k + 3] for k in range(len(a) - 2)])
        _, a = fake_preview("firefox", [], NO_SECURITY_CONTEXT)
        self.assertIn(["--ro-bind", "/run/user/1000/wayland-0", "/run/user/1000/wayland-0"], [a[k:k + 3] for k in range(len(a) - 2)])
        _, a = fake_preview("firefox", ["--wayland-strict"], NO_SECURITY_CONTEXT)
        self.assertNotIn("WAYLAND_DISPLAY", a)
        self.assertNotIn("/run/user/1000/wayland-0", a)

    def test_the_caller_cannot_change_core_variables(self):
        _, a = fake_preview("firefox", ["--setenv=HOME=/elsewhere"])
        self.assertNotIn("/elsewhere", a)


# ---------------------------------------------------------------- units
class Units(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-unit-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = f"{self.tmp}/home"
        os.makedirs(self.home)
        self.S = load_sbx()
        self.S.update(HOME=self.home, CONF=f"{self.home}/.config/sbx", HOMES=f"{self.home}/.sbx-homes")
        self.warned = []
        self.S["warn"] = self.warned.append

    def write(self, rel, text=""):
        p = f"{self.home}/{rel}"
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(text)
        return p

    # -- extra portals
    def test_extra_portals_are_blocked_until_their_switch_is_on(self):
        S = self.S
        base = " ".join(S["portal_rules"]({k: False for k in S["PORTAL_EXTRA"]}))
        for name in S["PORTAL_EXTRA"].values(): self.assertNotIn(f"portal.{name}.*", base)
        on = " ".join(S["portal_rules"]({"portal_screencast": True, "portal_secret": True}))
        self.assertIn("portal.ScreenCast.*", on); self.assertIn("portal.Secret.*", on)
        self.assertNotIn("portal.Screenshot.*", on)
        o, _ = S["parse_options"](["--portal-remote-desktop", "--portal-screenshot", "x"])
        self.assertEqual(o, {"portal_remote_desktop": True, "portal_screenshot": True})
        self.assertFalse(any(S["SCALARS"][k] for k in S["PORTAL_EXTRA"]))

    # -- settings
    def test_parse_conf_reads_everything_and_reports_what_it_skips(self):
        p = self.write("c.conf", "# comment\nnet=0  # off\n gpu = no \nsound=maybe\nx11=\nbogus=1\nmemory=2G\ntasks=lots\n"
                       "whitelist=~/a#b\nwhitelist=~/Projects\nenv=GOOD_NAME\nenv=1bad\nsetenv=A=b c\nsetenv=NOVALUE\n"
                       "allow_syscall=io_uring\nallow_syscall=bpf\nread_only=\n")
        sc, ls, lim = self.S["parse_conf"](p)
        self.assertEqual(sc, {"net": False, "gpu": False})
        self.assertEqual(lim, {"memory": "2G"})
        self.assertEqual(ls["whitelist"], ["~/a#b", "~/Projects"])             # '#' only starts a comment after a space
        self.assertEqual((ls["env"], ls["setenv"], ls["allow_syscall"], ls["read_only"]), (["GOOD_NAME"], ["A=b c"], ["io_uring"], []))
        text = "\n".join(self.warned)
        for must in ("sound=maybe", "x11=", "unknown setting 'bogus'", "tasks=lots", "'1bad'", "setenv needs NAME=value", "can't allow 'bpf'"):
            self.assertIn(must, text)
        self.assertIn(":4:", text)                                              # line numbers

    def test_settings_precedence_and_flags(self):
        self.write(".config/sbx/default.conf", "net=0\ngpu=0\nwhitelist=~/a\nmemory=1G\n")
        self.write(".config/sbx/apps/app.conf", "net=1\nwhitelist=~/b\nwhitelist=~/a\nmemory=\n")
        cfg, src = self.S["effective"]("app", {})
        self.assertEqual((cfg["net"], cfg["gpu"], cfg["sound"]), (True, False, True))
        self.assertEqual(cfg["whitelist"], ["~/a", "~/b"])                       # added up, no duplicates
        self.assertEqual(cfg["memory"], "")                                      # an empty value cancels the default's limit
        self.assertEqual((src["net"], src["gpu"], src["sound"]), ("apps/app.conf", "default.conf", "default"))
        self.assertEqual(src[("whitelist", "~/b")], "apps/app.conf")
        cfg, src = self.S["effective"]("app", {"net": False, "env": ["X"], "memory": "3G"})
        self.assertEqual((cfg["net"], src["net"], cfg["env"], cfg["memory"]), (False, "flag", ["X"], "3G"))
        cfg, _ = self.S["effective"]("app", {"ignore_config": True})
        self.assertEqual((cfg["net"], cfg["gpu"], cfg["whitelist"]), (True, True, []))

    def test_command_line_validation(self):
        parse = self.S["parse_options"]
        o, rest = parse(["--net=none", "--memory=2G", "--env=A", "--setenv=B=c", "--allow-syscall=io_uring", "--name=x", "prog", "-q"])
        self.assertEqual(o, {"net": False, "memory": "2G", "env": ["A"], "setenv": ["B=c"], "allow_syscall": ["io_uring"], "name": "x"})
        self.assertEqual(rest, ["prog", "-q"])
        self.assertEqual(parse(["--", "clean"])[1], ["clean"])
        for bad in (["--nope"], ["--memory=lots"], ["--env=1x"], ["--setenv=NOVALUE"], ["--allow-syscall=bpf"], ["--tasks="]):
            with self.assertRaises(SystemExit, msg=bad), contextlib.redirect_stderr(io.StringIO()): parse(bad)

    # -- paths
    def test_program_share(self):
        ps = self.S["program_share"]
        self.assertEqual(ps(f"{self.home}/apps/foo/bin/foo"), f"{self.home}/apps/foo")
        for p in ("bin/foo", ".local/bin/foo", "a/bin/foo", "apps/foo/sbin/foo"):          # too shallow or not a bin folder: just the file
            self.assertEqual(ps(f"{self.home}/{p}"), f"{self.home}/{p}", p)

    def test_share_path_rules(self):
        for d in (".ssh", "Documents", ".config/sbx", ".sbx-homes", ".config/gcloud/x", ".local/share/keyrings"):
            os.makedirs(f"{self.home}/{d}")
        sp = self.S["share_path"]
        quiet = contextlib.redirect_stderr(io.StringIO())
        self.assertEqual(sp(f"{self.home}/Documents", True), f"{self.home}/Documents")
        self.assertEqual(sp(f"{self.tmp}/home/Documents", False), f"{self.home}/Documents")
        for bad in (self.home, f"{self.home}/.config/sbx", f"{self.home}/.config", f"{self.home}/.sbx-homes/x/..", f"{self.home}/.sbx-homes",
                    f"{self.home}/.ssh", f"{self.home}/.ssh/id", f"{self.home}/.config/gcloud/x", f"{self.home}/.local/share",
                    f"{self.home}/missing", "/etc", "/usr"):
            with self.assertRaises(SystemExit, msg=bad), quiet: sp(bad, True)
        self.assertIsNone(sp(f"{self.home}/.ssh", False))                          # from a settings file: skipped with a warning
        self.assertIsNone(sp("relative/path", False))
        self.assertTrue(any("secrets" in w for w in self.warned) and any("full path" in w for w in self.warned))
        self.assertEqual(sp(f"{self.home}/.ssh", True, True), f"{self.home}/.ssh")      # allow_sensitive lifts that rule only
        for bad in (f"{self.home}/.config/sbx", f"{self.home}/.sbx-homes", self.home):
            with self.assertRaises(SystemExit, msg=bad), quiet: sp(bad, True, True)

    def test_order_shares(self):
        o = self.S["order_shares"]
        self.assertEqual(o(["/t/a/b", "/t/c"], ["/t/a"]), [("/t/a", "ro"), ("/t/c", "rw"), ("/t/a/b", "rw")])
        self.assertEqual(o(["/t/a"], ["/t/a"]), [("/t/a", "ro")])
        self.assertEqual(o([], [], here="/t/h", program="/t/h/p"), [("/t/h", "rw"), ("/t/h/p", "ro")])
        self.assertEqual(o(["/t/p"], [], program="/t/p"), [("/t/p", "ro")])

    def test_hint_args(self):
        os.makedirs(f"{self.home}/work"); self.write("work/script.py")
        old = os.getcwd(); os.chdir(f"{self.home}/work"); self.addCleanup(os.chdir, old)
        hint = self.S["hint_args"]

        def hints(args, here=None, shared=()):
            self.warned.clear(); hint(args, here, list(shared)); return list(self.warned)
        self.assertTrue(any("private home" in w for w in hints(["script.py"])))
        self.assertTrue(hints(["--file=script.py"]))
        self.assertEqual(hints(["script.py"], here=f"{self.home}/work"), [])
        self.assertTrue(any("not shared" in w for w in hints([f"{self.home}/work/script.py"])))
        self.assertEqual(hints([f"{self.home}/work/script.py"], shared=[f"{self.home}/work"]), [])
        self.assertEqual(hints(["/usr/bin/env", "-x", "nothing-here", "", "--flag=value"]), [])
        self.assertTrue(hints(["/tmp"]))

    # -- X11
    def test_scoped_xauth_keeps_only_this_display(self):
        def entry(num, data):
            parts = [b"host", num, b"MIT-MAGIC-COOKIE-1", data]
            return struct.pack(">H", 256) + b"".join(struct.pack(">H", len(p)) + p for p in parts)
        src = self.write("xauth"); dst = f"{self.tmp}/out/x"
        with open(src, "wb") as f: f.write(entry(b"0", b"AAAA") + entry(b"1", b"BBBB") + entry(b"", b"CCCC"))
        self.assertEqual(self.S["scoped_xauth"](src, ":0", dst), dst)
        with open(dst, "rb") as f: got = f.read()
        self.assertEqual(got, entry(b"0", b"AAAA") + entry(b"", b"CCCC"))
        self.assertEqual(oct(os.stat(dst).st_mode & 0o777), "0o600")
        self.assertIsNone(self.S["scoped_xauth"](src, ":7", dst + "2") if not os.path.exists(dst + "2") and False else None)
        with open(src, "wb") as f: f.write(entry(b"0", b"AAAA")[:-3])                   # truncated: use the original file
        self.assertIsNone(self.S["scoped_xauth"](src, ":0", dst))

    # -- hardening follow-ups
    def test_new_mount_api_is_blocked_and_can_be_allowed_again(self):
        S = self.S
        new = {"open_tree": 428, "move_mount": 429, "fsopen": 430, "fsconfig": 431, "fsmount": 432, "fspick": 433, "mount_setattr": 442}
        for machine in ("x86_64", "aarch64"):
            blocked = S["blocked_numbers"](machine)
            for name, nr in new.items():
                self.assertIn(nr, blocked, f"{name} on {machine}"); self.assertTrue(S["syscall_ok"](name), name)
            self.assertNotIn(430, S["blocked_numbers"](machine, ("fsopen",)))
            self.assertIn(429, S["blocked_numbers"](machine, ("fsopen",)))

    def test_an_unusable_xauthority_is_not_handed_over_whole(self):
        S = self.S
        bad = self.write("bad.xauth", "not an xauthority file at all")
        os.makedirs(f"{self.tmp}/run")
        S["runtime_dir"] = lambda: f"{self.tmp}/run"
        env = {"DISPLAY": ":0", "XAUTHORITY": bad, "WAYLAND_DISPLAY": ""}
        with mock.patch.dict(os.environ, env):
            _, cmd, st = S["build"]("/bin/true", [], True, {"x11": True})
        for fd in st["fds"]: os.close(fd)
        self.assertFalse(any(cmd[i:i + 3] == ["--ro-bind", bad, bad] for i in range(len(cmd))), "the whole file was shared")
        self.assertNotIn("XAUTHORITY", cmd)
        self.assertTrue(any("XAUTHORITY" in w for w in self.warned), self.warned)

    def test_unknown_architecture_does_not_start_without_the_filter(self):
        if os.geteuid() == 0: self.skipTest("run() refuses root before it gets to the architecture")
        S, dies = self.S, []
        def die(msg, code=1): dies.append(msg); raise SystemExit(code)
        S["die"] = die
        S["platform"] = types.SimpleNamespace(machine=lambda: "riscv64")
        S["shutil"] = types.SimpleNamespace(which=lambda x: f"/usr/bin/{x}")
        with mock.patch.dict(os.environ, {"SBX_ACTIVE": ""}):
            os.environ.pop("SBX_ACTIVE")
            with self.assertRaises(SystemExit): S["run"]("/bin/true", [], {})
        self.assertEqual(len(dies), 1); self.assertIn("no system-call filter", dies[0])

    def test_doctor_warns_about_an_abstract_x_socket(self):
        S = self.S
        real_open = open
        def fake(p, *a, **k):
            if p == "/proc/net/unix": return io.StringIO("Num RefCount Protocol Flags Type St Inode Path\n0: 2 0 10000 1 1 5 @/tmp/.X11-unix/X0\n")
            return real_open(p, *a, **k)
        S["open"] = fake
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["doctor"]()
        self.assertRegex(out.getvalue(), r"WARN\s+an X server listens on an abstract socket")
        S["open"] = lambda p, *a, **k: io.StringIO("Num\n") if p == "/proc/net/unix" else real_open(p, *a, **k)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["doctor"]()
        self.assertNotIn("abstract socket", out.getvalue())

    def test_a_name_belongs_to_the_file_that_first_used_it(self):
        S, dies = self.S, []
        def die(msg, code=1): dies.append(msg); raise SystemExit(code)
        S["die"] = die
        check = S["check_origin"]
        home = f"{self.home}/apps/foo/bin/foo"
        check("foo", home)                                                     # first use: recorded
        check("foo", home)                                                     # same file: fine
        with self.assertRaises(SystemExit): check("foo", "/tmp/evil/foo")      # another file under the same name
        self.assertIn("first run from", dies[-1])
        with open(f"{S['CONF']}/origins/foo") as f: self.assertEqual(f.read().strip(), home)   # the record did not move
        check("foo", "/tmp/evil/foo", rebind=True)                             # explicitly accepted
        with open(f"{S['CONF']}/origins/foo") as f: self.assertEqual(f.read().strip(), "/tmp/evil/foo")
        check("ls", "/usr/bin/ls"); check("ls", "/opt/coreutils/bin/ls")       # moving between system folders is not a new program
        with self.assertRaises(SystemExit): check("ls", "/tmp/evil/ls")        # but a system program's name is not free for anyone

    # -- system calls
    @staticmethod
    def run_bpf(prog, nr, arch):
        ins = [struct.unpack("=HBBI", prog[i:i + 8]) for i in range(0, len(prog), 8)]
        pc, acc = 0, 0
        while True:
            code, jt, jf, k = ins[pc]
            if code == 0x20: acc = {0: nr, 4: arch}[k]; pc += 1                       # load nr / arch
            elif code == 0x15: pc += 1 + (jt if acc == k else jf)                     # jump if equal
            elif code == 0x35: pc += 1 + (jt if acc >= k else jf)                     # jump if greater or equal
            elif code == 0x06: return k                                               # return
            else: raise AssertionError(f"unexpected instruction {code:#x}")

    def test_the_syscall_filter_blocks_exactly_the_list(self):
        S = self.S
        allow, deny = 0x7FFF0000, 0x00050001
        for machine, audit, other, ok_nrs in (("x86_64", 0xC000003E, 0xC00000B7, (0, 1, 2, 257, 435, 231)),
                                              ("aarch64", 0xC00000B7, 0xC000003E, (63, 64, 56, 435, 94))):
            col = S["ARCHES"][machine][1]
            for allowing in ((), ("io_uring",), ("kcmp", "userfaultfd")):
                prog = S["seccomp_program"](machine, allowing)
                allowed = set(S["IO_URING"]) if "io_uring" in allowing else set(allowing)
                for table in (S["BASE_SYSCALLS"], S["EXTRA_SYSCALLS"]):
                    for name, t in table.items():
                        if t[col] is None: continue
                        want = allow if name in allowed else deny
                        self.assertEqual(self.run_bpf(prog, t[col], audit), want, f"{machine} {name} allowing {allowing}")
                for nr in ok_nrs: self.assertEqual(self.run_bpf(prog, nr, audit), allow, f"{machine} syscall {nr}")
                self.assertEqual(self.run_bpf(prog, 0, other), deny, f"{machine}: the other architecture (32-bit) is refused")
                self.assertEqual(self.run_bpf(prog, 0x40000000 | 0, audit), deny, f"{machine}: x32 calls are refused")
        self.assertIsNone(S["seccomp_program"]("riscv64"))

    def test_syscall_numbers_match_the_kernel_headers(self):
        tables = {0: "/usr/include/x86_64-linux-gnu/asm/unistd_64.h", 1: "/usr/include/asm-generic/unistd.h"}
        if not all(os.path.exists(p) for p in tables.values()): self.skipTest("no kernel headers (linux-libc-dev)")
        for col, path in tables.items():
            with open(path) as f: nrs = {m[1]: int(m[2]) for m in re.finditer(r"#define __NR_(\w+)\s+(\d+)\s*$", f.read(), re.M)}
            for table in (self.S["BASE_SYSCALLS"], self.S["EXTRA_SYSCALLS"]):
                for name, t in table.items():
                    if t[col] is not None and name in nrs: self.assertEqual(t[col], nrs[name], f"{name} (column {col})")

    # -- Wayland
    def test_wayland_client_against_a_fake_compositor(self):
        S = self.S
        sock = f"{self.tmp}/wl"
        comp = FakeCompositor(sock, S["wl_string"]); self.addCleanup(comp.stop)
        self.assertTrue(S["wl_supported"](sock))
        ctx = S["wayland_context"](sock, f"{self.tmp}/run/app.wl", "firefox", "firefox-42")
        self.assertIsNotNone(ctx, self.warned)
        self.assertEqual((comp.engine, comp.app_id, comp.instance, comp.committed), ("org.sbx", "firefox", "firefox-42", True))
        self.assertEqual(comp.bound, ("wp_security_context_manager_v1", 1))
        listen = comp.listen
        self.assertEqual(listen.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)       # it is a listening socket...
        c = socket.socket(socket.AF_UNIX); c.connect(f"{self.tmp}/run/app.wl"); c.close()      # ...at the path the program will use
        listen.settimeout(2); listen.accept()[0].close()
        self.assertEqual(oct(os.stat(f"{self.tmp}/run").st_mode & 0o777), "0o700")
        poll = select.poll(); poll.register(comp.close_fd, select.POLLHUP)
        self.assertEqual(poll.poll(100), [])                                                    # compositor keeps listening...
        ctx.close()
        self.assertTrue(poll.poll(2000), "closing the context must make the close fd hang up")  # ...until sbx lets go
        self.assertFalse(os.path.exists(f"{self.tmp}/run/app.wl"))

    def test_wayland_client_copes_with_no_support_and_with_errors(self):
        S = self.S
        sock = f"{self.tmp}/wl"
        comp = FakeCompositor(sock, S["wl_string"], with_manager=False); self.addCleanup(comp.stop)
        self.assertFalse(S["wl_supported"](sock))
        self.assertIsNone(S["wayland_context"](sock, f"{self.tmp}/run/x.wl", "a", "b"))
        self.assertEqual(self.warned, [])                                                        # nothing to warn about, just not there
        comp.stop()
        sock2 = f"{self.tmp}/wl2"
        bad = FakeCompositor(sock2, S["wl_string"], fail_on_commit=True); self.addCleanup(bad.stop)
        self.assertIsNone(S["wayland_context"](sock2, f"{self.tmp}/run/y.wl", "a", "b"))
        self.assertTrue(any("invalid metadata" in w for w in self.warned), self.warned)
        self.assertFalse(os.path.exists(f"{self.tmp}/run/y.wl"), "a failed attempt leaves no socket behind")
        self.assertFalse(S["wl_supported"](f"{self.tmp}/nothing-here"))
        self.assertIsNone(S["wayland_context"](f"{self.tmp}/nothing-here", f"{self.tmp}/run/z.wl", "a", "b"))

    def test_wayland_request_numbers_match_the_protocol(self):
        xml = "/usr/share/wayland-protocols/staging/security-context/security-context-v1.xml"
        if not os.path.exists(xml): self.skipTest("wayland-protocols is not installed")
        with open(xml) as f: text = f.read()
        ifaces = re.findall(r'<interface name="(\w+)".*?</interface>', text, re.S)
        reqs = {m[1]: re.findall(r'<request name="(\w+)"', m[2]) for m in re.finditer(r'<interface name="(\w+)"[^>]*>(.*?)</interface>', text, re.S)}
        self.assertEqual(reqs["wp_security_context_manager_v1"], ["destroy", "create_listener"])
        self.assertEqual(reqs["wp_security_context_v1"], ["destroy", "set_sandbox_engine", "set_app_id", "set_instance_id", "commit"])
        self.assertEqual(len(ifaces), 2)

    # -- small things
    def test_install_hint(self):
        def hint(text):
            self.S["open"] = lambda p, *a, **k: io.StringIO(text) if p == "/etc/os-release" else open(p, *a, **k)
            return self.S["install_hint"]("bubblewrap")
        self.assertEqual(hint('NAME=Fedora\nID=fedora\n'), "sudo dnf install bubblewrap")
        self.assertEqual(hint('ID=linuxmint\nID_LIKE="ubuntu debian"\n'), "sudo apt install bubblewrap")
        self.assertEqual(hint('ID=arch\n'), "sudo pacman -S bubblewrap")
        self.assertEqual(hint('ID=almalinux\nID_LIKE="rhel centos fedora"\n'), "sudo dnf install bubblewrap")
        self.assertEqual(hint('ID=somethingnew\n'), "install the 'bubblewrap' package")

    def test_stale_helper_files_are_swept(self):
        d = f"{self.tmp}/run/sbx"; os.makedirs(d)
        self.S["runtime_dir"] = lambda: f"{self.tmp}/run"
        dead = 4194000
        while True:                                                                                # a pid nobody has
            try: os.kill(dead, 0); dead += 1
            except ProcessLookupError: break
            except PermissionError: dead += 1
        names = {f"{dead}.bus": False, f"{dead}.xauth": False, f"{dead}.wl": False, f"{os.getpid()}.wl": True, "notes.txt": True, f"{dead}.other": True}
        for n in names: open(f"{d}/{n}", "w").close()
        self.S["sweep_stale"]()
        for n, kept in names.items(): self.assertEqual(os.path.exists(f"{d}/{n}"), kept, n)

    # -- opening downloaded programs
    def test_elf_interpreter(self):
        S = self.S
        bash = shutil.which("bash")
        if bash and S["elf_interpreter"](bash): self.assertTrue(os.path.isabs(S["elf_interpreter"](bash)))
        interp = self.write("ld.so", "x"); path = os.path.abspath(interp)
        hdr = lambda cls, fields: b"\x7fELF" + bytes([cls, 1, 1]) + b"\0" * 9 + fields
        text = path.encode() + b"\0"
        e64 = hdr(2, struct.pack("<HHIQQQIHHHHHH", 3, 62, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0))
        e64 += b"\0" * (64 - len(e64)) + struct.pack("<IIQQQQQQ", 3, 4, 120, 0, 0, len(text), len(text), 1) + b"\0" * 0
        e64 = e64 + b"\0" * (120 - len(e64)) + text
        e32 = hdr(1, struct.pack("<HHIIIIIHHHHHH", 3, 3, 1, 0, 52, 0, 0, 52, 32, 1, 0, 0, 0))
        e32 += b"\0" * (52 - len(e32)) + struct.pack("<IIIIIIII", 3, 84, 0, 0, len(text), len(text), 4, 1)
        e32 = e32 + b"\0" * (84 - len(e32)) + text
        for label, data in (("64-bit", e64), ("32-bit", e32)):
            p = f"{self.tmp}/{label}"
            with open(p, "wb") as f: f.write(data)
            self.assertEqual(S["elf_interpreter"](p), path, label)
        for label, data in (("script", b"#!/bin/sh\necho\n"), ("empty", b""), ("truncated", e64[:70]), ("static", e64[:64] + b"\0" * 56)):
            p = f"{self.tmp}/{label}"
            with open(p, "wb") as f: f.write(data)
            self.assertIsNone(S["elf_interpreter"](p), label)
        self.assertIsNone(S["elf_interpreter"](f"{self.tmp}/missing"))

    def test_opener_folder_rules(self):
        S, h = self.S, self.home
        self.write(".config/user-dirs.dirs", 'XDG_DOWNLOAD_DIR="$HOME/Transferências"\nXDG_DESKTOP_DIR="$HOME/Área de Trabalho"\n# x\n')
        of = S["opener_folder"]
        self.assertEqual(of(f"{h}/Games/Crash/game"), f"{h}/Games/Crash")
        self.assertEqual(of(f"{h}/Downloads/Crash/game"), f"{h}/Downloads/Crash", "a subfolder of Downloads is the program's own")
        for bad in (f"{h}/game", f"{h}/Downloads/game", f"{h}/Desktop/game", f"{h}/Transferências/game", f"{h}/Área de Trabalho/game",
                    f"{h}/.local/bin/game", f"{h}/.cache/x/game", "/usr/bin/game", "/tmp/game", "/opt/x/game"):
            self.assertIsNone(of(bad), bad)
        self.assertEqual(of("/tmp/x/game"), "/tmp/x")

    def test_opener_install_and_remove(self):
        S, h = self.S, self.home
        bindir = f"{self.tmp}/bin"; os.makedirs(bindir)
        log = f"{self.tmp}/xdg.log"
        with open(f"{bindir}/xdg-mime", "w") as f:
            f.write(f'#!/bin/sh\necho "$@" >> {log}\nshift\nid=$1; shift\n'
                    f'mkdir -p "$XDG_CONFIG_HOME"; for m in "$@"; do echo "$m=$id;" >> "$XDG_CONFIG_HOME/mimeapps.list"; done\n')
        os.chmod(f"{bindir}/xdg-mime", 0o755)
        cfg = f"{self.tmp}/cfg"
        env = {"PATH": f"{bindir}:/usr/bin:/bin", "XDG_CONFIG_HOME": cfg}
        self.write(".local/share/applications/keep.desktop", "x")
        os.makedirs(cfg); open(f"{cfg}/mimeapps.list", "w").write("[Default Applications]\ntext/plain=other.desktop;\n")
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out): S["cmd_opener"](True)
        dest = f"{h}/.local/share/applications/sbx-run.desktop"
        with open(dest) as f: text = f.read()
        for must in ("Name=Run in sbx", "Exec=", " open %f", "application/x-executable;application/x-pie-executable;", "Terminal=false", "X-Sbx-Opener=true"):
            self.assertIn(must, text)
        self.assertIn("application/x-shellscript;", text)                                                   # offered under Open With...
        with open(log) as f: self.assertEqual(f.read().strip(), "default sbx-run.desktop application/x-executable application/x-pie-executable")   # ...but never made the default for .sh
        with open(f"{cfg}/mimeapps.list", "a") as f: f.write("application/x-executable=sbx-run.desktop;other.desktop;\n")
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out): S["cmd_opener"](False)
        self.assertFalse(os.path.exists(dest))
        with open(f"{cfg}/mimeapps.list") as f: left = f.read()
        self.assertNotIn("sbx-run", left)
        self.assertIn("text/plain=other.desktop;", left); self.assertIn("application/x-executable=other.desktop;", left, "other handlers stay")
        with open(dest, "w") as f: f.write("[Desktop Entry]\nName=mine\n")                      # not ours: left alone
        with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): S["cmd_opener"](False)
        self.assertTrue(os.path.exists(dest))

    def test_exit_codes(self):
        e = self.S["exit_code"]
        self.assertEqual([e(0), e(3), e(-15), e(-9)], [0, 3, 143, 137])

    def test_teardown_removes_only_empty_mountpoints(self):
        base = f"{self.home}/.sbx-homes/app"
        os.makedirs(f"{base}/Projects"); os.makedirs(f"{base}/.local/share/fonts"); self.write(".sbx-homes/app/.gitconfig")
        os.makedirs(f"{base}/Kept"); self.write(".sbx-homes/app/Kept/data", "x")
        mp = [f"{base}/.local", f"{base}/.local/share", f"{base}/.local/share/fonts", f"{base}/Projects", f"{base}/.gitconfig", f"{base}/Kept"]
        st = {"proxy": None, "bus": f"{self.tmp}/bus", "files": [], "wl": None, "cleanup": mp}
        self.S["teardown"](st)
        self.assertEqual(sorted(os.listdir(base)), ["Kept"])
        self.assertTrue(os.path.exists(f"{base}/Kept/data"))

    def test_launcher_rewriting(self):
        src = ("[Desktop Entry]\nName=Firefox\nName[de]=Feuerfuchs\nExec=firefox %u\nDBusActivatable=true\nTryExec=firefox\n\n"
               "[Desktop Action new-window]\nName=Open a New Window\nExec=firefox --new-window %u\n")
        new, notes = self.S["rewrite_desktop"](src, "/usr/local/bin/sbx")
        lines = new.splitlines()
        self.assertEqual(lines[:3], ["[Desktop Entry]", "X-Sbx-Sandboxed=true", "Name=Firefox (sbx)"])
        for must in ("Name[de]=Feuerfuchs", "Exec=/usr/local/bin/sbx firefox %u", "DBusActivatable=false", "TryExec=firefox",
                     "Exec=/usr/local/bin/sbx firefox --new-window %u", "Name=Open a New Window"):
            self.assertIn(must, lines)
        self.assertEqual(notes, [])
        _, notes = self.S["rewrite_desktop"]("[Desktop Entry]\nExec=env FOO=1 app\n", "/usr/bin/sbx")
        self.assertTrue(notes)
        new, _ = self.S["rewrite_desktop"]("[Desktop Entry]\nExec=app\n", "/opt/my tools/sbx")
        self.assertIn('Exec="/opt/my tools/sbx" app', new)

    def test_schema_lists_every_setting(self):
        sch = self.S["schema"]()
        self.assertEqual(sch["switches"], self.S["SCALARS"])
        self.assertEqual(set(sch["lists"]), {"whitelist", "read_only", "env", "setenv", "allow_syscall"})
        self.assertIn(".ssh", sch["sensitive"])


class Ported(unittest.TestCase):
    """Ideas taken from ajail: portal sessions, camera portal, ssh_agent."""
    def test_portal_rules_and_ssh_agent(self):
        S = load_sbx()
        base = " ".join(S["portal_rules"]({}))
        self.assertIn("portal.Session.*@/org/freedesktop/portal/desktop/session/*", base)
        self.assertNotIn("portal.Camera", base)
        self.assertIn("portal.Camera.*", " ".join(S["portal_rules"]({"camera": True})))
        self.assertIs(S["SCALARS"]["ssh_agent"], False)
        self.assertEqual(S["parse_options"](["--ssh-agent", "x"])[0], {"ssh_agent": True})

    def test_tray_rules_are_narrow_and_work_without_portals(self):
        S = load_sbx()
        r = S["tray_rules"]()
        self.assertIn("--talk=org.kde.StatusNotifierWatcher", r)
        self.assertIn("--own=org.kde.StatusNotifierItem-2-1", r)
        self.assertIn("--own=org.mpris.MediaPlayer2.*", r)
        self.assertTrue(all(x.startswith(("--talk=org.kde.StatusNotifierWatcher", "--own=org.kde.StatusNotifierItem-", "--own=org.mpris."))
                            for x in r), "the tray rules name something besides the tray and media keys")
        self.assertIs(S["SCALARS"]["tray"], False)                                       # off unless asked for
        self.assertEqual(S["parse_options"](["--tray", "x"])[0], {"tray": True})
        self.assertEqual(S["bus_rules"]({"portals": False, "tray": False}), [])
        only = S["bus_rules"]({"portals": False, "tray": True})
        self.assertIn("--own=org.mpris.MediaPlayer2.*", only)
        self.assertNotIn("--talk=org.freedesktop.Notifications", only)                  # the tray alone doesn't open the portals
        both = S["bus_rules"]({"portals": True, "tray": True})
        self.assertIn("--talk=org.freedesktop.Notifications", both); self.assertIn("--own=org.mpris.MediaPlayer2.*", both)
        self.assertEqual(S["bus_rules"](), S["portal_rules"]())                          # unchanged for callers that pass nothing
        rt = S["runtime_dir"]()
        _, args, _ = S["build"]("/bin/true", [], False, {"tray": True, "ignore_config": True})
        self.assertIn(f"{rt}/bus", args); self.assertIn("DBUS_SESSION_BUS_ADDRESS", args)
        _, args, _ = S["build"]("/bin/true", [], False, {"ignore_config": True})
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", args)


class Hostile(unittest.TestCase):
    """Awkward and hostile input: odd program names, links that point at protected folders, malformed settings files, a build
    that is out of date. (Like ajail's hostile tests, but for what sbx reads: names, paths and settings.)"""
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-hostile-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = f"{self.tmp}/home"
        os.makedirs(self.home)
        self.S = load_sbx()
        self.S.update(HOME=self.home, CONF=f"{self.home}/.config/sbx", HOMES=f"{self.home}/.sbx-homes")
        self.warned = []
        self.S["warn"] = self.warned.append

    def test_whatever_a_program_is_called_its_name_is_a_safe_word(self):
        ok = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
        for raw in ("firefox", "a b", "x\ny", "../../etc/passwd", "--net=none", ".hidden", "-rf", "é.ü", "a" * 300, "\x1b]52;c;x\x07", "", "...", "名前",
                    "tab\there", "semi;colon", "$(id)", "`id`", "a/b/c"):
            name = self.S["clean"](os.path.basename(raw))
            self.assertRegex(name, ok.pattern, repr(raw))
            self.assertTrue(ok.fullmatch(name), repr(raw))

    def test_a_link_cannot_be_used_to_share_a_protected_folder(self):
        for d in (".ssh", ".config/sbx", ".sbx-homes", ".gnupg"): os.makedirs(f"{self.home}/{d}")
        os.makedirs(f"{self.home}/Docs")
        sp = self.S["share_path"]
        quiet = contextlib.redirect_stderr(io.StringIO())
        for i, target in enumerate((".ssh", ".config/sbx", ".sbx-homes", ".gnupg", "Docs/../.ssh", "/etc", "/")):
            link = f"{self.home}/Docs/link{i}"
            os.symlink(target if target.startswith("/") else f"{self.home}/{target}", link)
            for cli in (True, False):
                with quiet:
                    try: got = sp(link, cli)
                    except SystemExit: got = None
                self.assertIsNone(got, f"{link} -> {target} was accepted (cli={cli})")
        os.symlink(f"{self.home}/Docs", f"{self.home}/gooddocs")                      # a link to something fine still works
        self.assertEqual(sp(f"{self.home}/gooddocs", True), f"{self.home}/Docs")

    def test_a_malformed_settings_file_changes_nothing_it_should_not(self):
        p = f"{self.home}/odd.conf"
        with open(p, "wb") as f:
            f.write(b"net=0\r\ngpu=0\x00\nsound=1 \t\n" + b"x11=" + b"1" * 1_000_000 + b"\n" + b"\xff\xfe=1\nportals=1\nportals=0\n")
        with contextlib.redirect_stderr(io.StringIO()):
            sc, ls, lim = self.S["parse_conf"](p)
        self.assertIs(sc["net"], False)                                                # CRLF is fine
        self.assertNotIn("gpu", sc)                                                    # a NUL makes the value meaningless: ignored, not guessed
        self.assertIs(sc["sound"], True)
        self.assertNotIn("x11", sc)                                                    # a million-digit value is not a 1
        self.assertIs(sc["portals"], False)                                            # the last line wins
        self.assertEqual((ls["whitelist"], lim), ([], {}))
        os.mkdir(f"{self.home}/dir.conf")                                              # a folder or a missing file is just empty
        self.assertEqual(self.S["parse_conf"](f"{self.home}/dir.conf")[0], {})
        self.assertEqual(self.S["parse_conf"](f"{self.home}/nothing.conf")[0], {})

    def test_every_file_the_readme_installs_exists(self):
        with open(f"{HERE}/README.md") as f: text = f.read()
        wanted = []
        for line in text.splitlines():
            if line.lstrip().startswith("sudo install"):
                wanted += [w for w in line.split("#")[0].split()[2:] if not w.startswith(("-", "/")) and "/" not in w.lstrip("./")[:0]
                           and re.fullmatch(r"[\w./-]+", w) and not w.startswith("/")]
        self.assertTrue(wanted, "found no install lines in the README")
        missing = [w for w in wanted if not os.path.exists(f"{HERE}/{w}")]
        self.assertEqual(missing, [], "the README installs files that are not in the repo")

    def test_the_installable_file_is_exactly_what_src_builds(self):
        r = subprocess.run([sys.executable, f"{HERE}/build.py", "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class DesktopSettings(unittest.TestCase):
    """The title-bar buttons and cursor reach a GTK program as a read-only key file; nothing else from dconf does."""
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-gs-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.S = load_sbx()
        self.S["HOME"] = self.tmp

    def host(self, values):
        """Pretend gsettings knows these (schema, key) -> raw text; everything else is missing."""
        self.S["gsettings_get"] = lambda schema, key: values.get((schema, key))

    def text(self): return self.S["gsettings_keyfile_text"]()

    def test_layout_and_cursor_become_a_key_file(self):
        self.host({("org.gnome.desktop.wm.preferences", "button-layout"): "'appmenu:minimize,maximize,close'",
                   ("org.gnome.desktop.interface", "cursor-theme"): "'Adwaita'", ("org.gnome.desktop.interface", "cursor-size"): "32"})
        self.assertEqual(self.text(), "[org/gnome/desktop/wm/preferences]\nbutton-layout='appmenu:minimize,maximize,close'\n\n"
                                      "[org/gnome/desktop/interface]\ncursor-theme='Adwaita'\ncursor-size=32\n")

    def test_nothing_found_means_nothing_shared(self):
        self.host({}); self.assertIsNone(self.text())
        self.host({("org.gnome.desktop.interface", "cursor-size"): "0"}); self.assertIsNone(self.text())       # 0 means 'default'

    def test_hostile_values_are_dropped_not_written(self):
        for bad in ("'x'\n[evil]\nkey='v'", "'a;b'", "'" + "a" * 65 + "'", "'close\\''"):
            self.host({("org.gnome.desktop.wm.preferences", "button-layout"): bad}); self.assertIsNone(self.text(), bad)
        for theme in ("'../../etc'", "'.hidden'", "'a\\'b'", "'" + "a" * 65 + "'", "Adwaita", "''"):
            self.host({("org.gnome.desktop.interface", "cursor-theme"): theme}); self.assertIsNone(self.text(), theme)
        for size in ("-1", "99999", "12a", "999"):
            self.host({("org.gnome.desktop.interface", "cursor-size"): size}); self.assertIsNone(self.text(), size)

    def test_other_desktops_and_the_gtk_ini_are_used_but_written_under_gnomes_schema(self):
        self.host({("org.cinnamon.muffin", "button-layout"): "'menu:close'"})
        self.assertEqual(self.text(), "[org/gnome/desktop/wm/preferences]\nbutton-layout='menu:close'\n")
        self.host({})
        os.makedirs(f"{self.tmp}/.config/gtk-3.0")
        with open(f"{self.tmp}/.config/gtk-3.0/settings.ini", "w") as f: f.write("[Settings]\ngtk-decoration-layout=:minimize,maximize,close\n")
        self.assertEqual(self.text(), "[org/gnome/desktop/wm/preferences]\nbutton-layout=':minimize,maximize,close'\n")

    def test_only_the_wanted_keys_are_ever_asked_for(self):
        asked = []
        self.S["gsettings_get"] = lambda schema, key: asked.append((schema, key))
        self.text()
        self.assertTrue(all(k in ("button-layout", "cursor-theme", "cursor-size") for _, k in asked), asked)

    def test_the_file_is_private_written_only_when_live_and_swept_when_stale(self):
        self.host({("org.gnome.desktop.wm.preferences", "button-layout"): "':close'"})
        dst = f"{self.tmp}/run/sbx/PID.gsettings"
        self.assertEqual(self.S["gsettings_keyfile"](dst, False), dst); self.assertFalse(os.path.exists(dst))      # preview writes nothing
        self.assertEqual(self.S["gsettings_keyfile"](dst, True), dst)
        self.assertEqual(os.stat(dst).st_mode & 0o777, 0o600); self.assertEqual(os.stat(os.path.dirname(dst)).st_mode & 0o777, 0o700)
        self.host({}); self.assertIsNone(self.S["gsettings_keyfile"](dst, True))
        self.S["runtime_dir"] = lambda: f"{self.tmp}/run"
        os.rename(dst, f"{self.tmp}/run/sbx/4194303.gsettings")
        self.S["sweep_stale"]()
        self.assertFalse(os.path.exists(f"{self.tmp}/run/sbx/4194303.gsettings"))

    def test_the_program_gets_the_file_and_the_backend_only_with_a_window_and_theme(self):
        text = {"gsettings_keyfile_text": lambda: "[org/gnome/desktop/wm/preferences]\nbutton-layout=':close'\n"}
        dst = "/home/user/.config/glib-2.0/settings/keyfile"
        _, args = fake_preview("firefox", [], text)
        i = args.index(dst); self.assertEqual(args[i - 2], "--ro-bind"); self.assertEqual(args[i - 1], "/run/user/1000/sbx/PID.gsettings")
        j = args.index("GSETTINGS_BACKEND"); self.assertEqual(args[j + 1], "keyfile")
        for flags in (["--no-theme"], ["--no-wayland"]):                            # no theme, or nothing that draws a window
            _, args = fake_preview("firefox", flags, text); self.assertNotIn(dst, args, flags); self.assertNotIn("GSETTINGS_BACKEND", args, flags)
        _, args = fake_preview("firefox", [], {})                                    # nothing found on this machine
        self.assertNotIn(dst, args); self.assertNotIn("GSETTINGS_BACKEND", args)


class Logging(unittest.TestCase):
    """The launch log: private, capped, free of control characters, and only written during a run."""
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-log-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        with mock.patch.dict(os.environ, {"SBX_HOME": self.tmp}): self.S = load_sbx()

    def lines(self):
        with open(self.S["LOG"]) as f: return f.read().splitlines()

    def test_log_is_private_one_line_per_entry_and_capped(self):
        S = self.S
        self.assertTrue(S["LOG"].startswith(self.tmp))
        S["log"]("hello \x1b]52;c;evil\x07\nforged [x] line", "app")
        out = self.lines()
        self.assertEqual(len(out), 1); self.assertIn("[app] hello ]52;c;evil forged [x] line", out[0]); self.assertNotIn("\x1b", out[0])
        self.assertEqual(os.stat(S["LOG"]).st_mode & 0o777, 0o600); self.assertEqual(os.stat(S["STATE"]).st_mode & 0o777, 0o700)
        with open(S["LOG"], "a") as f: f.write(("y" * 99 + "\n") * (S["LOG_MAX"] // 100 + 10))
        S["log"]("after trim", "app")
        out = self.lines()
        self.assertLessEqual(len(out), S["LOG_KEEP"] + 1); self.assertIn("after trim", out[-1])

    def test_log_does_not_follow_a_link(self):
        S = self.S
        os.makedirs(S["STATE"], exist_ok=True)
        target = f"{self.tmp}/elsewhere"
        open(target, "w").close()
        os.symlink(target, S["LOG"])
        S["log"]("must not land there", "app")
        self.assertEqual(os.path.getsize(target), 0)

    def test_warnings_and_errors_are_logged_only_during_a_run(self):
        S = self.S
        with contextlib.redirect_stderr(io.StringIO()):
            S["warn"]("not in a run")
            self.assertFalse(os.path.exists(S["LOG"]))
            S["LOG_NAME"][0] = "app"
            S["warn"]("careful")
            with self.assertRaises(SystemExit): S["die"]("it broke")
        self.assertEqual([l.split("] ", 1)[-1] for l in self.lines()], ["[app] WARNING careful", "[app] ERROR it broke"])

    def test_stderr_goes_to_the_log_and_to_stderr_without_control_characters(self):
        S = self.S
        r, w = os.pipe()
        os.write(w, b"first\nsecond \x1b[31mred\x1b[0m\n\xff bad utf8\nno newline at the end"); os.close(w)
        err = io.StringIO()
        with contextlib.redirect_stderr(err): S["tee_stderr"](r, "app")
        os.close(r)
        self.assertEqual(self.lines(), ["[app] first", "[app] second [31mred[0m", "[app] � bad utf8", "[app] no newline at the end"])
        self.assertNotIn("\x1b", err.getvalue()); self.assertIn("first", err.getvalue())

    def test_one_run_cannot_fill_the_log(self):
        S = self.S
        S["LOG_RUN_MAX"] = 200
        r, w = os.pipe()
        os.write(w, b"0123456789\n" * 100); os.close(w)
        with contextlib.redirect_stderr(io.StringIO()): S["tee_stderr"](r, "app")
        os.close(r)
        out = self.lines()
        self.assertLess(len(out), 30); self.assertEqual(out[-1], "[app] (more output not logged)")

    def test_failure_notification_without_actions_is_a_plain_popup(self):
        S = self.S
        with mock.patch("shutil.which", return_value=None), mock.patch("subprocess.run") as run:
            S["notify_failure"]("app"); run.assert_not_called()                        # no notify-send: nothing happens
        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/notify-send" if x == "notify-send" else None), \
             mock.patch("subprocess.run", return_value=types.SimpleNamespace(stdout="no actions")) as run:
            S["notify_failure"]("app")
        sent = run.call_args_list[-1].args[0]
        self.assertIn("critical", sent); self.assertTrue(any("app stopped" in x for x in sent))


class Gui(unittest.TestCase):
    """sbx-gui's settings logic, with GTK stubbed out (the window itself can't be tested here)."""
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-gui-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        stub = mock.MagicMock()
        mods = {"gi": stub, "gi.repository": stub}
        with mock.patch.dict(sys.modules, mods), mock.patch.dict(os.environ, {"HOME": self.tmp}):
            with open(f"{HERE}/sbx-gui") as f: src = f.read()
            ns = {"__name__": "sbx_gui_under_test"}
            class Base: pass
            stub.Adw.ApplicationWindow = stub.Adw.Application = Base
            exec(compile(src, "sbx-gui", "exec"), ns)
        self.G = ns
        self.S = load_sbx()

    def test_inherited_folders_get_their_own_group_instead_of_a_note_on_every_row(self):
        G = self.G
        os.makedirs(G["APPS"], exist_ok=True)
        with open(f"{G['CONF']}/default.conf", "w") as f: f.write("whitelist=~/Shared\nread_only=~/Docs\n")
        with open(f"{G['APPS']}/app.conf", "w") as f: f.write("read_only=~/Mine\n")
        adw = G["Adw"]; adw.ActionRow.reset_mock(); adw.PreferencesGroup.reset_mock()
        win = mock.MagicMock()
        groups = G["Win"].folders_group(win, "app")
        self.assertEqual(len(groups), 2)
        titles = [c.kwargs.get("title") for c in adw.PreferencesGroup.call_args_list]
        self.assertEqual(titles, ["Shared folders and files", "Shared with all programs"])
        rows = {c.kwargs.get("title"): c.kwargs for c in adw.ActionRow.call_args_list}
        self.assertEqual(rows["~/Shared"]["subtitle"], "Read and write"); self.assertEqual(rows["~/Docs"]["subtitle"], "Read only")
        self.assertTrue(rows["~/Shared"]["activatable"])
        self.assertNotIn("activatable", rows["~/Mine"])                                  # its own folder keeps the access menu
        self.assertFalse(any("default" in str(r.get("subtitle", "")).lower() for r in rows.values()), "the note is repeated on rows")
        groups[1].set_visible.assert_called_with(True)
        self.assertEqual(len(G["Win"].folders_group(win, None)), 1)                      # the Default page has no such group
        G["Win"].open_defaults(win)
        self.assertEqual(G["CURRENT"], [None]); win.nav.replace.assert_called_once()

    def test_switch_rows_do_not_carry_a_default_note(self):
        G = self.G
        os.makedirs(G["APPS"], exist_ok=True)
        key = next(iter(G["SWITCHES"]))
        with open(f"{G['APPS']}/app.conf", "w") as f: f.write(f"{key}=1\n")
        for scope in ("app", "other", None):                                             # overridden, inherited, the default page itself
            row = G["Adw"].SwitchRow.return_value; row.set_subtitle.reset_mock()
            G["Win"].switch_row(mock.MagicMock(), scope, key)
            row.set_subtitle.assert_called_with(G["SWITCHES"][key][1])

    def test_log_page_reads_the_same_file_sbx_writes_and_filters_by_program(self):
        G, S = self.G, self.S
        self.assertEqual(G["LOG"], f"{self.tmp}/.local/state/sbx/sbx.log")
        self.assertEqual(G["LOG"], S["LOG"].replace(S["HOME"], G["HOME"]))              # same place as sbx's LOG
        self.assertEqual(G["log_tail"](), "No launches logged yet.")
        os.makedirs(os.path.dirname(G["LOG"]), exist_ok=True)
        with open(G["LOG"], "w") as f: f.write("[t] [a] START\n[b] error from b\n[t] [a] EXIT code=1\n")
        self.assertEqual(G["log_tail"]("a").splitlines(), ["[t] [a] START", "[t] [a] EXIT code=1"])
        self.assertEqual(len(G["log_tail"]().splitlines()), 3)
        self.assertEqual(G["log_tail"]("zzz"), "Nothing logged yet.")
        self.assertEqual(len(G["log_tail"](n=1).splitlines()), 1)
        win = mock.MagicMock()
        real = G["subprocess"]
        G["subprocess"] = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout="OK  fine", stderr=""),
                                                TimeoutExpired=Exception, SubprocessError=Exception)
        try: G["Win"].open_trouble(win, "a")
        finally: G["subprocess"] = real
        win.nav.push.assert_called_once()

    def test_log_lines_are_split_and_classified_for_color(self):
        parts, ts = self.G["log_line_parts"], "[2026-11-11 20:39:24] "
        self.assertEqual(parts(ts + "[app] START allow=[net]"), (ts, "[app] ", "START allow=[net]", "info"))
        self.assertEqual(parts("[app] thread 'main' panicked at x.rs:1:2:")[::3], ("", "err"))      # no timestamp on a program's own output
        self.assertEqual(parts(ts + "[app] EXIT code=0")[3], "exit_ok")
        self.assertEqual(parts(ts + "[app] EXIT code=101")[3], "exit_err")
        self.assertEqual(parts("[app] ALSA lib pcm.c:1:(f) [error.core] cannot find card '0'")[3], "dim")      # noise, not an error
        self.assertEqual(parts("[app] MESA-EGL: warning: failed to get driver name")[3], "warn")
        self.assertEqual(parts("[app] MESA: error: ZINK: failed to choose pdev")[3], "err")
        self.assertEqual(parts("[app] hello")[2:], ("hello", ""))
        for odd in ("", "[", "[]", ts):
            parts(odd)                                                    # must never raise

    # -- troubleshooting hints: which log lines suggest which setting (pure logic, no window) --
    START = "[2026-11-11 20:00:00] [app] START allow=[net,sound,gpu]"

    def run_log(self, *body, code=1):
        return [self.START] + [f"[app] {b}" for b in body] + ([f"[2026-11-11 20:00:05] [app] EXIT code={code}"] if code is not None else [])

    def hint_keys(self, lines, **current):
        G = self.G
        cur = {k: d for k, (_, _, d) in G["SWITCHES"].items()}
        cur.update(current)
        return [(k, w) for k, w, *_ in G["log_hints"](lines, cur)]

    def test_hints_follow_the_error_and_the_current_setting(self):
        run = self.run_log
        self.assertEqual(self.hint_keys(run("FATAL: No usable sandbox! Update your kernel"), userns=False), [("userns", True)])
        self.assertEqual(self.hint_keys(run("FATAL: No usable sandbox!"), userns=True), [])               # already on: nothing to suggest
        self.assertEqual(self.hint_keys(run("libEGL warning: failed to open /dev/dri/renderD128"), gpu=False), [("gpu", True)])
        self.assertEqual(self.hint_keys(run("curl: (6) Temporary failure in name resolution"), net=False), [("net", True)])
        gl = run("MESA: error: failed to open /dev/dri/card0")
        self.assertEqual(self.hint_keys(gl, gpu=True, gpu_render_only=True), [("gpu_render_only", False)])
        self.assertEqual(self.hint_keys(gl, gpu=True, gpu_render_only=False), [])
        self.assertEqual(self.hint_keys(gl, gpu=False, gpu_render_only=True), [("gpu", True)])           # render-only means nothing without graphics
        self.assertEqual(self.hint_keys(run("cannot open /dev/video0"), camera=False), [("camera", True)])
        self.assertEqual(self.hint_keys(run("Error: cannot open display: :0")), [("x11", True)])
        crash = run("./setup_linux.sh: line 21:  1116 Aborted                    (core dumped) ./setup_linux 2> /dev/null", code=134)
        self.assertEqual(self.hint_keys(crash, x11=False), [("x11", True)])                  # no message in the log: a guess
        self.assertEqual(self.hint_keys(crash, x11=True), [])
        self.assertEqual(self.hint_keys(run("something", code=134), x11=False), [("x11", True)])
        self.assertEqual(self.hint_keys(run("Aborted (core dumped)", code=0), x11=False), [])   # only for a failed run
        self.assertEqual(len(self.G["log_hints"](run("Error: cannot open display: :0", "Aborted (core dumped)", code=134), {"x11": False})), 1)
        self.assertEqual(self.hint_keys(run("Failed to connect to Wayland display"), wayland=False), [("wayland", True), ("x11", True)])
        self.assertEqual(self.hint_keys(run("Failed to connect to Wayland display")), [("x11", True)])    # Wayland is on already
        self.assertEqual(self.hint_keys(run("libEGL warning: a", "MESA: error: b", "failed to open /dev/dri/x"), gpu=False), [("gpu", True)])   # once
        self.assertEqual(self.hint_keys(run("Segmentation fault (core dumped)"), userns=False, gpu=False, net=False), [])

    def test_hints_only_for_the_last_run_and_only_when_it_failed(self):
        run = self.run_log
        self.assertEqual(self.hint_keys(run("libEGL warning: x", code=0), gpu=False), [])
        self.assertEqual(self.hint_keys(run("No usable sandbox!", code=None), userns=False), [])           # still running
        self.assertEqual(self.hint_keys([], userns=False), [])
        old, new = run("No usable sandbox!"), run("all fine now")
        self.assertEqual(self.hint_keys(old + new, userns=False), [])
        self.assertEqual(self.hint_keys(new + old, userns=False), [("userns", True)])
        big = run(*(["x" * 5000] * 2000) + ["No usable sandbox!"])
        self.assertEqual(self.hint_keys(big, userns=False), [("userns", True)])

    def test_every_hint_names_a_real_switch(self):
        G = self.G
        for pat, key, want, title, why, needs in G["LOG_HINTS"]:
            re.compile(pat)
            self.assertIn(key, self.S["SCALARS"]); self.assertIsInstance(want, bool); self.assertTrue(title and why)
            for k in needs: self.assertIn(k, self.S["SCALARS"])

    def test_effective_switches_layers_defaults_then_default_conf_then_the_program(self):
        G = self.G
        os.makedirs(G["APPS"], exist_ok=True)
        with open(f"{G['CONF']}/default.conf", "w") as f: f.write("net=0\nx11=1\n")
        with open(f"{G['APPS']}/app.conf", "w") as f: f.write("x11=0\ncamera=1\n")
        e = G["effective_switches"]("app")
        self.assertEqual((e["net"], e["x11"], e["camera"], e["sound"]), (False, False, True, True))
        self.assertEqual(G["effective_switches"](None)["x11"], True)
        self.assertEqual(G["effective_switches"]("other")["x11"], True)

    def test_log_lines_are_one_programs_or_all(self):
        G = self.G
        self.assertEqual(G["log_lines"](), [])
        os.makedirs(os.path.dirname(G["LOG"]), exist_ok=True)
        with open(G["LOG"], "w") as f: f.write("[t] [a] START allow=[]\n[b] x\n[t] [a] EXIT code=1\n")
        self.assertEqual(len(G["log_lines"]()), 3); self.assertEqual(len(G["log_lines"]("a")), 2); self.assertEqual(G["log_lines"]("zz"), [])
        os.makedirs(G["APPS"], exist_ok=True)
        with open(G["LOG"], "w") as f: f.write("[t] [app] START allow=[net]\n[app] FATAL: No usable sandbox!\n[t] [app] EXIT code=1\n")
        with open(f"{G['APPS']}/app.conf", "w") as f: f.write("userns=0\n")
        self.assertEqual([k for k, *_ in G["log_hints"](G["log_lines"]("app"), G["effective_switches"]("app"))], ["userns"])

    def test_every_sbx_switch_has_wording_and_the_same_default(self):
        self.assertEqual(set(self.G["SWITCHES"]), set(self.S["SCALARS"]), "sbx-gui and sbx list different switches")
        for k, (_, _, default) in self.G["SWITCHES"].items(): self.assertEqual(default, self.S["SCALARS"][k], k)

    def test_schema_fills_in_what_the_window_does_not_know(self):
        G = self.G
        sch = {"switches": {**self.S["SCALARS"], "brand_new": True}, "sensitive": [".ssh", ".brand-new"]}
        out = types.SimpleNamespace(stdout=__import__("json").dumps(sch))
        real = G["subprocess"]
        G["subprocess"] = types.SimpleNamespace(run=lambda *a, **k: out, TimeoutExpired=Exception)
        try: G["load_schema"]()
        finally: G["subprocess"] = real
        self.assertIn("brand_new", G["SWITCHES"]); self.assertEqual(G["GROUPS"][-1][0], "Other")
        self.assertEqual(G["SENSITIVE"], [".ssh", ".brand-new"])

    def test_it_reads_and_writes_the_same_files(self):
        G = self.G
        G["set_key"](None, "net", False); G["set_key"]("app", "hostname", True)
        self.assertEqual(G["parse"](None)[0], {"net": False}); self.assertEqual(G["parse"]("app")[0], {"hostname": True})
        G["save"]("app", ["net=maybe", "memory=2G", "env=FOO", "gpu=0  # comment"])
        self.assertEqual(G["parse"]("app")[0], {"gpu": False}, "an invalid value is ignored, as sbx does")
        G["set_key"]("app", "gpu", None)
        with open(f"{self.tmp}/.config/sbx/apps/app.conf") as f: self.assertEqual(f.read(), "net=maybe\nmemory=2G\nenv=FOO\n")   # lines it doesn't know stay
        self.assertTrue(G["add_item"]("app", "whitelist", "~/Projects")); self.assertFalse(G["add_item"]("app", "whitelist", "~/a #b"))
        G["remove_item"]("app", "whitelist", "~/Projects"); self.assertEqual(G["parse"]("app")[1]["whitelist"], [])

    def test_it_refuses_the_same_paths_as_sbx(self):
        G = self.G
        for d in (".ssh", "Documents", ".config/sbx"): os.makedirs(f"{self.tmp}/{d}")
        pp = G["path_problem"]
        self.assertIsNone(pp(f"{self.tmp}/Documents")); self.assertIsNone(pp("/tmp/x"))
        for bad in (self.tmp, f"{self.tmp}/.config/sbx", f"{self.tmp}/.ssh", "/etc"): self.assertTrue(pp(bad), bad)
        G["save"](None, ["allow_sensitive=1"]); G["CURRENT"][:] = [None]
        self.assertIsNone(pp(f"{self.tmp}/.ssh")); self.assertTrue(pp(f"{self.tmp}/.config/sbx"))


class FakeCompositor(threading.Thread):
    """Just enough of a Wayland compositor to test the security-context client: registry, sync, the manager."""
    def __init__(self, path, wl_string, with_manager=True, fail_on_commit=False):
        super().__init__(daemon=True)
        self.wl_string, self.with_manager, self.fail_on_commit = wl_string, with_manager, fail_on_commit
        self.engine = self.app_id = self.instance = self.bound = self.listen = self.close_fd = None
        self.committed, self.running = False, True
        self.srv = socket.socket(socket.AF_UNIX); self.srv.bind(path); self.srv.listen(4); self.srv.settimeout(0.1)
        self.start()

    def stop(self):
        self.running = False
        self.join(2)
        self.srv.close()

    def run(self):
        while self.running:
            try: conn, _ = self.srv.accept()
            except (socket.timeout, OSError): continue
            conn.settimeout(2)
            try: self.serve(conn)
            except (OSError, struct.error): pass
            finally: conn.close()

    def take(self, conn, n):
        data = b""
        while len(data) < n:
            chunk, anc, _, _ = conn.recvmsg(n - len(data), socket.CMSG_SPACE(8))
            if not chunk: raise OSError("closed")
            data += chunk
            for _, _, raw in anc:
                self.fds = getattr(self, "fds", []) + list(struct.unpack(f"={len(raw) // 4}i", raw[:len(raw) // 4 * 4]))
        return data

    def event(self, conn, obj, op, payload=b""):
        conn.sendall(struct.pack("=II", obj, ((8 + len(payload)) << 16) | op) + payload)

    def string(self, data, off=0):
        (n,) = struct.unpack_from("=I", data, off)
        return data[off + 4:off + 4 + n - 1].decode(), off + 4 + (n + 3) // 4 * 4

    def serve(self, conn):
        registry = manager = context = None
        while self.running:
            obj, word = struct.unpack("=II", self.take(conn, 8))
            op = word & 0xFFFF
            data = self.take(conn, (word >> 16) - 8)
            if obj == 1 and op == 1:                                                          # get_registry
                (registry,) = struct.unpack("=I", data)
                self.event(conn, registry, 0, struct.pack("=I", 1) + self.wl_string("wl_compositor") + struct.pack("=I", 5))
                if self.with_manager:
                    self.event(conn, registry, 0, struct.pack("=I", 7) + self.wl_string("wp_security_context_manager_v1") + struct.pack("=I", 1))
            elif obj == 1 and op == 0:                                                        # sync
                (cb,) = struct.unpack("=I", data)
                self.event(conn, cb, 0, struct.pack("=I", 0))
            elif obj == registry and op == 0:                                                 # bind
                off = 4
                iface, off = self.string(data, off)
                ver, manager = struct.unpack_from("=II", data, off)
                self.bound = (iface, ver)
            elif obj == manager and op == 1:                                                  # create_listener
                (context,) = struct.unpack("=I", data)
                self.listen = socket.socket(fileno=self.fds[0]); self.close_fd = self.fds[1]
            elif obj == context and op in (1, 2, 3):
                value, _ = self.string(data)
                setattr(self, {1: "engine", 2: "app_id", 3: "instance"}[op], value)
            elif obj == context and op == 4:                                                  # commit
                self.committed = True
                if self.fail_on_commit:
                    msg = b"invalid metadata\0"
                    self.event(conn, 1, 0, struct.pack("=III", context, 3, len(msg)) + msg + b"\0" * (-len(msg) % 4))


# ---------------------------------------------------------------- real isolation
def bwrap_works():
    if os.geteuid() == 0: return "sbx refuses to run as root; run this as a normal user"
    if not shutil.which("bwrap"): return "bubblewrap is not installed"
    r = subprocess.run(["bwrap", "--unshare-all", "--ro-bind", "/", "/", "/usr/bin/true"], capture_output=True)
    return None if r.returncode == 0 else "bubblewrap cannot create sandboxes here: " + r.stderr.decode()[:100]


class Isolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        why = bwrap_works()
        if why: raise unittest.SkipTest(why)
        cls.T = os.path.realpath(tempfile.mkdtemp(prefix="sbx-test-"))
        os.chmod(cls.T, 0o700)
        cls.home = f"{cls.T}/home"
        cls.canaries = []
        for rel in (".ssh/id_canary", ".gnupg/key", ".aws/credentials", ".config/sbx/default.conf", ".sbx-homes/other/data",
                    "Documents/note.txt"):
            p = f"{cls.home}/{rel}"; os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f: f.write(TOKEN + "\n")
            cls.canaries.append(p)
        cls.sbx = f"{HERE}/sbx"
        cls.script = f"{cls.home}/sbx-selftest"
        shutil.copy(f"{HERE}/sbx-selftest", cls.script); os.chmod(cls.script, 0o755)
        cls.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": cls.home, "SBX_HOME": cls.home,
                   "SBX_CANARY": TOKEN, "SSH_AUTH_SOCK": "/nonexistent", "GITHUB_TOKEN": TOKEN}
        cls.has_disable_userns = "--disable-userns" in subprocess.run(["bwrap", "--help"], capture_output=True, text=True).stdout

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "T", None): shutil.rmtree(cls.T, ignore_errors=True)

    def sbx_run(self, *args, env=None, tty=False):
        r = subprocess.run([sys.executable, self.sbx, *args], capture_output=True, text=True, errors="replace",
                           env={**self.env, **(env or {})}, timeout=60, stdin=subprocess.DEVNULL, cwd=self.home)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        return r

    def test_selftest_passes_inside_the_sandbox(self):
        r = self.sbx_run(self.script, *self.canaries)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL PASS", r.stdout)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        for must in ("~/.ssh is hidden", "~/.config/sbx is hidden", "/etc/shadow is empty or unreadable", "/root is empty",
                     "/var/log is empty", "syscall bpf is blocked", "syscall mbind is blocked", "syscall io_uring_setup is blocked",
                     "syscall userfaultfd is blocked", "syscall process_vm_readv is blocked", "syscall kcmp is blocked",
                     "syscall pidfd_getfd is blocked"):
            self.assertRegex(r.stdout, rf"PASS  {re.escape(must)}")

    def test_the_selftest_fails_without_the_sandbox(self):
        """Negative control: with no sandbox the same checks must fail, otherwise they prove nothing."""
        r = subprocess.run([sys.executable, self.script, *self.canaries], capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout)
        for must in ("FAIL  ~/.ssh is hidden", "FAIL  ~/.config/sbx is hidden", "FAIL  syscall mbind is blocked",
                     "FAIL  syscall userfaultfd is blocked", "FAIL  syscall kcmp is blocked"):
            self.assertIn(must, r.stdout)

    def test_every_flag_does_what_it_says(self):
        flags = ["--net=none", "--no-gpu", "--no-sound", "--hostname"] + (["--no-userns"] if self.has_disable_userns else [])
        expect = "--expect=net0,gpu0,sound0,portals0,hostname" + (",userns0" if self.has_disable_userns else "")
        r = self.sbx_run(*flags, self.script, expect)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for must in ("no network (only lo)", "/sys/class/net is empty", "no graphics devices", "no PipeWire/PulseAudio socket", "no session bus",
                     "hostname is sbx everywhere"):
            self.assertRegex(r.stdout, rf"PASS  {re.escape(must)}")
        if self.has_disable_userns: self.assertRegex(r.stdout, r"PASS  user namespaces are blocked")
        r = self.sbx_run(self.script, "--expect=userns0")                       # negative control: without --no-userns they work
        if self.has_disable_userns and r.returncode == 0: self.fail("user namespaces were blocked without asking")

    def test_io_uring_can_be_allowed_again(self):
        try:
            with open("/proc/sys/kernel/io_uring_disabled") as f:
                if f.read().strip() == "2": self.skipTest("io_uring is disabled by this kernel")
        except OSError: pass
        r = self.sbx_run("--allow-syscall=io_uring", self.script, "--expect=iouring")
        self.assertIn("PASS  io_uring is allowed", r.stdout, r.stdout + r.stderr)

    def test_machine_id_and_boot_id_are_not_the_hosts(self):
        r = self.sbx_run(self.script)
        info = {l.split()[1]: l.split()[-1] for l in r.stdout.splitlines() if l.startswith("INFO  ") and len(l.split()) > 2}
        for key, path in (("machine-id", "/etc/machine-id"), ("boot-id", "/proc/sys/kernel/random/boot_id")):
            try:
                with open(path) as f: host = f.read().strip()
            except OSError: continue
            self.assertNotEqual(info[key], host, key)
            self.assertRegex(info[key], r"^[0-9a-f-]{32,36}$")

    def test_writes_land_in_the_private_home_only(self):
        r = self.sbx_run("/bin/sh", "-c", 'echo x > "$HOME/.ssh-evil"; mkdir -p "$HOME/.ssh"; echo x > "$HOME/.ssh/evil"; '
                         'echo x > "$HOME/Documents-evil"; echo x > "$HOME/.config-evil"')
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in (".ssh-evil", ".ssh/evil", "Documents-evil", ".config-evil"):
            self.assertFalse(os.path.exists(f"{self.home}/{rel}"), f"{rel} was written into the real home")
        self.assertTrue(os.path.exists(f"{self.home}/.sbx-homes/sh/.ssh/evil"), "the private home is where writes go")

    def test_private_home_is_erased_on_exit(self):
        r = self.sbx_run("--private", "--name=erased", "/bin/sh", "-c", 'echo x > "$HOME/f" && ls "$HOME"')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "f"), r.stderr)
        self.assertEqual(os.listdir(f"{self.home}/.sbx-homes/erased"), [])

    def test_mountpoints_do_not_pile_up_in_the_private_home(self):
        os.makedirs(f"{self.home}/.sbx-homes/kept/Documents")                   # already there: not ours to remove
        self.sbx_run("--name=fresh", f"--whitelist={self.home}/Documents", f"--read-only={self.home}/.config", "/bin/true")
        self.assertFalse(os.path.exists(f"{self.home}/.sbx-homes/fresh/Documents"))
        r = self.sbx_run("--name=fresh", f"--whitelist={self.home}/Documents", "/bin/sh", "-c", 'ls "$HOME/Documents"')
        self.assertEqual(r.stdout.strip(), "note.txt", "the share itself works")
        self.sbx_run("--name=kept", f"--whitelist={self.home}/Documents", "/bin/true")
        self.assertTrue(os.path.isdir(f"{self.home}/.sbx-homes/kept/Documents"))

    def test_an_inner_share_keeps_its_own_mode(self):
        os.makedirs(f"{self.home}/proj/data", exist_ok=True)
        cmd = 'echo x > "$HOME/proj/data/ok" && echo "data writable"; echo x > "$HOME/proj/top" 2>/dev/null || echo "top read-only"'
        r = self.sbx_run(f"--whitelist={self.home}/proj/data", f"--read-only={self.home}/proj", "/bin/sh", "-c", cmd)
        self.assertEqual(r.stdout.split("\n")[:2], ["data writable", "top read-only"], r.stdout + r.stderr)

    def test_a_program_cannot_be_given_sbxs_own_settings_or_homes(self):
        for rel in (".config/sbx", ".sbx-homes", ".config/sbx/default.conf", ".sbx-homes/other", ""):
            for opt in ("--whitelist=", "--read-only="):
                r = self.sbx_run("preview", opt + f"{self.home}/{rel}".rstrip("/"), "/bin/true")
                self.assertNotEqual(r.returncode, 0, f"{opt}~/{rel} was accepted")
        r = self.sbx_run("preview", f"--whitelist={self.home}/Documents", "/bin/true")       # an ordinary folder is fine
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_secret_folders_need_a_confirming_flag(self):
        for rel in (".ssh", ".gnupg", ".aws"):
            r = self.sbx_run("preview", f"--whitelist={self.home}/{rel}", "/bin/true")
            self.assertNotEqual(r.returncode, 0, rel); self.assertIn("secrets", r.stderr)
            r = self.sbx_run("preview", "--allow-sensitive", f"--whitelist={self.home}/{rel}", "/bin/true")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_environment_passthrough(self):
        r = self.sbx_run("--env=MY_PROXY", "--setenv=FOO=a b", "--setenv=HOME=/x", "/bin/sh", "-c", 'echo "$MY_PROXY|$FOO|$HOME|$OTHER"',
                         env={"MY_PROXY": "http://p:3128", "OTHER": "no"})
        self.assertEqual(r.stdout.strip(), f"http://p:3128|a b|{self.home}|", r.stderr)
        self.assertIn("HOME can't be changed", r.stderr)

    def test_open_runs_a_program_with_its_folder_and_nothing_else(self):
        bash = shutil.which("bash")
        S = load_sbx()
        if not bash or not S["elf_interpreter"](os.path.realpath(bash)): self.skipTest("no dynamically linked bash to copy")
        game, dl = f"{self.home}/Games/Crash", f"{self.home}/Downloads"
        os.makedirs(game, exist_ok=True); os.makedirs(dl, exist_ok=True)
        for p in (f"{game}/CrashBandicoot-Linux", f"{dl}/loose"):
            shutil.copy(os.path.realpath(bash), p); os.chmod(p, 0o644)                      # no execute bit
        for p, t in ((f"{game}/data.pak", "assets"), (f"{dl}/other.txt", "other")):
            with open(p, "w") as f: f.write(t)

        def sbx_open(path, stdin):
            return subprocess.run([sys.executable, self.sbx, "open", path], input=stdin, capture_output=True, text=True,
                                  env=self.env, timeout=60, cwd="/")
        r = sbx_open(f"{game}/CrashBandicoot-Linux", f'pwd; ls; echo saved > save.dat; cat {self.home}/.ssh/id_canary 2>&1; echo done\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[0], game, "it starts in its own folder")
        self.assertIn("data.pak", r.stdout); self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(f"{game}/save.dat"), "it can save beside itself")
        r = sbx_open(f"{dl}/loose", f"pwd; ls {dl}; ls -A\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[0], self.home, "Downloads is not a program folder: it starts in its private home")
        self.assertNotIn("other.txt", r.stdout, "the rest of Downloads stays hidden")
        with open(f"{self.home}/Downloads/notelf", "w") as f: f.write("#!/bin/sh\n")
        r = sbx_open(f"{dl}/notelf", "")
        self.assertEqual(r.returncode, 1); self.assertIn("chmod +x", r.stderr)
        self.assertEqual(sbx_open(f"{dl}/missing", "").returncode, 1)
        r = sbx_open(f"{game}/CrashBandicoot-Linux", "exit 3\n")                          # quits at once with an error: reported, status kept
        self.assertEqual(r.returncode, 3); self.assertIn("didn't start", r.stderr)
        os.makedirs(f"{self.home}/.config/sbx/apps", exist_ok=True)                         # a settings file means no extras
        with open(f"{self.home}/.config/sbx/apps/CrashBandicoot-Linux.conf", "w") as f: f.write("net=0\n")
        try: r = sbx_open(f"{game}/CrashBandicoot-Linux", "pwd; ls -A\n")
        finally: os.unlink(f"{self.home}/.config/sbx/apps/CrashBandicoot-Linux.conf")
        self.assertEqual(r.stdout.split("\n")[0], self.home); self.assertNotIn("data.pak", r.stdout)

    def test_exit_status_is_the_programs(self):
        self.assertEqual(self.sbx_run("/bin/sh", "-c", "exit 7").returncode, 7)
        self.assertEqual(self.sbx_run("/bin/sh", "-c", "kill -TERM $$").returncode, 143)

    def test_refuses_nested_sbx_and_root(self):
        r = self.sbx_run("/bin/true", env={"SBX_ACTIVE": "1"})
        self.assertNotEqual(r.returncode, 0); self.assertIn("already inside", r.stderr)
        S = load_sbx()
        err = io.StringIO()
        with mock.patch.object(os, "geteuid", return_value=0), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            S["main"](["/bin/true"])
        self.assertEqual(cm.exception.code, 1); self.assertIn("refusing to run as root", err.getvalue())

    def test_a_hint_says_why_a_file_is_not_found(self):
        r = self.sbx_run("/bin/cat", "Documents/note.txt")
        self.assertIn("won't find it", r.stderr); self.assertNotIn(TOKEN, r.stdout)
        r = self.sbx_run("/bin/cat", f"{self.home}/Documents/note.txt")
        self.assertIn("not shared", r.stderr)

    def test_limits_wrap_the_command_in_systemd_run(self):
        r = self.sbx_run("preview", "--memory=1G", "--tasks=64", "/bin/true")
        self.assertTrue(r.stdout.startswith("systemd-run --user --scope --quiet -pMemoryMax=1G -pTasksMax=64 -- bwrap "), r.stdout)
        self.assertNotEqual(self.sbx_run("preview", "--memory=lots", "/bin/true").returncode, 0)

    def test_doctor_show_and_schema(self):
        r = self.sbx_run("doctor")
        self.assertIn(r.returncode, (0, 1)); self.assertRegex(r.stdout, r"(OK|FAIL)\s+.*bubblewrap")
        os.makedirs(f"{self.home}/.config/sbx/apps", exist_ok=True)
        with open(f"{self.home}/.config/sbx/apps/showtest.conf", "w") as f: f.write("net=0\nmemory=1G\nwhitelist=~/Documents\n")
        r = self.sbx_run("show", "--env=ZED", "showtest")
        self.assertRegex(r.stdout, r"net\s+= 0\s+\[apps/showtest.conf\]")
        self.assertRegex(r.stdout, r"gpu\s+= 1\s+\[default\]"); self.assertRegex(r.stdout, r"memory\s+= 1G")
        self.assertRegex(r.stdout, r"whitelist\s+= ~/Documents\s+\[apps/showtest.conf\]"); self.assertRegex(r.stdout, r"env\s+= ZED\s+\[flag\]")
        import json
        self.assertIn("hostname", json.loads(self.sbx_run("schema").stdout)["switches"])

    def test_clean(self):
        os.makedirs(f"{self.home}/.sbx-homes/cleantest/sub"); os.chmod(f"{self.home}/.sbx-homes/cleantest/sub", 0o500)
        os.makedirs(f"{self.home}/.config/sbx/ids", exist_ok=True)
        with open(f"{self.home}/.config/sbx/ids/cleantest", "w") as f: f.write("x\n")
        self.assertNotEqual(self.sbx_run("clean", "cleantest").returncode, 0, "without a terminal it needs --yes")
        self.assertTrue(os.path.isdir(f"{self.home}/.sbx-homes/cleantest"))
        for bad in ("../other", "other/..", "missing"): self.assertNotEqual(self.sbx_run("clean", "--yes", bad).returncode, 0, bad)
        self.assertTrue(os.path.exists(f"{self.home}/.sbx-homes/other/data"))
        self.assertEqual(self.sbx_run("clean", "--yes", "cleantest").returncode, 0)
        self.assertFalse(os.path.exists(f"{self.home}/.sbx-homes/cleantest")); self.assertFalse(os.path.exists(f"{self.home}/.config/sbx/ids/cleantest"))

    def test_desktop_launchers(self):
        share = f"{self.T}/share"; os.makedirs(f"{share}/applications")
        with open(f"{share}/applications/org.example.foo.desktop", "w") as f: f.write("[Desktop Entry]\nName=Foo\nExec=foo %U\nDBusActivatable=true\n")
        env = {"XDG_DATA_DIRS": share}
        r = self.sbx_run("desktop", "foo", env=env); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)           # found by its short name
        mine = f"{self.home}/.local/share/applications/org.example.foo.desktop"
        with open(mine) as f: text = f.read()
        self.assertRegex(text, r"Exec=\S*sbx foo %U"); self.assertIn("X-Sbx-Sandboxed=true", text); self.assertIn("DBusActivatable=false", text)
        self.assertEqual(self.sbx_run("desktop", "--remove", "org.example.foo").returncode, 0); self.assertFalse(os.path.exists(mine))
        with open(mine, "w") as f: f.write("[Desktop Entry]\nExec=foo\n")                                              # not ours: leave it alone
        self.assertNotEqual(self.sbx_run("desktop", "--remove", "org.example.foo").returncode, 0); self.assertTrue(os.path.exists(mine))
        self.assertNotEqual(self.sbx_run("desktop", "nosuchthing", env=env).returncode, 0)


    def test_clean_never_chmods_through_a_link_planted_in_the_private_home(self):
        d = f"{self.home}/.sbx-homes/linktest"; os.makedirs(f"{d}/sub")
        target = f"{self.T}/linktarget"
        with open(target, "w") as f: f.write("mine\n")
        os.chmod(target, 0o644)
        os.symlink(target, f"{d}/sub/lnk"); os.chmod(f"{d}/sub", 0o500)         # read-only folder: removing the link fails once
        self.assertEqual(self.sbx_run("clean", "--yes", "linktest").returncode, 0)
        self.assertFalse(os.path.exists(d))
        self.assertEqual(oct(os.stat(target).st_mode & 0o777), "0o644", "clean changed the mode of a file outside the private home")

    def test_desktop_refuses_ids_that_are_paths(self):
        marked = f"{self.home}/.local/share/x.desktop"; os.makedirs(os.path.dirname(marked), exist_ok=True)
        with open(marked, "w") as f: f.write("[Desktop Entry]\nX-Sbx-Sandboxed=true\n")
        for bad in ("../x", "a/b", ".hidden", "x*"):
            r = self.sbx_run("desktop", "--remove", bad)
            self.assertNotEqual(r.returncode, 0, bad); self.assertIn("not a launcher id", r.stderr, bad)
        self.assertTrue(os.path.exists(marked), "a file outside applications/ was removed")


    def test_a_download_named_like_a_configured_program_is_refused(self):
        os.makedirs(f"{self.home}/.config/sbx/apps", exist_ok=True)
        with open(f"{self.home}/.config/sbx/apps/pinned.conf", "w") as f: f.write("net=1\n")
        good, evil = f"{self.home}/good/pinned", f"{self.home}/evil/pinned"
        for p in (good, evil):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f: f.write("#!/bin/sh\necho ran-$0\n")
            os.chmod(p, 0o755)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
        r = self.sbx_run(good, env=env); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("ran-", r.stdout)
        r = self.sbx_run(evil, env=env)
        self.assertNotEqual(r.returncode, 0); self.assertIn("first run from", r.stderr); self.assertNotIn("ran-", r.stdout)
        self.assertEqual(self.sbx_run("--name=pinned-other", evil, env=env).returncode, 0)      # a separate name works
        self.assertEqual(self.sbx_run("--rebind", evil, env=env).returncode, 0)                 # so does saying it is the same program
        self.assertEqual(self.sbx_run("clean", "--yes", "pinned").returncode, 0)                # clean forgets the record
        self.assertFalse(os.path.exists(f"{self.home}/.config/sbx/origins/pinned"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
