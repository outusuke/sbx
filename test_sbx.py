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
import contextlib, difflib, hashlib, io, os, re, select, shutil, socket, struct, subprocess, sys, tempfile, threading, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = f"{HERE}/sbx-preview.snap"
TOKEN = "CANARY-5b0e77d1-do-not-read"
FIX = "/tmp/sbx-snapshot-fixture"          # real folders the snapshot scenarios share (the path must not change)


def xentry(family, host, num, data):
    """One record of an Xauthority file."""
    parts = [host, num, b"MIT-MAGIC-COOKIE-1", data]
    return struct.pack(">H", family) + b"".join(struct.pack(">H", len(p)) + p for p in parts)


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
              "/etc/ssh/ssh_host_ed25519_key", "/run/user/1000/xauth", "/etc/hostname", "/etc/hosts", "/etc/resolv.conf"}
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
    ("private network (pasta)", "firefox", ["--net-private"], {}),
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
    S["resolv_target"] = lambda: "/run/systemd/resolve/stub-resolv.conf"      # /etc/resolv.conf is a link, as on most desktops
    S["glob"] = types.SimpleNamespace(glob=lambda pat: FAKE_GLOBS.get(pat, []))
    S["gsettings_keyfile_text"] = lambda: None                  # whatever desktop settings this machine has must not change the snapshot
    S.update(patch or {})
    opts, _ = S["parse_options"](flags)
    env = {"LANG": "en_US.UTF-8", "TERM": "xterm", "XDG_CURRENT_DESKTOP": "GNOME", "WAYLAND_DISPLAY": "wayland-0",
           "DISPLAY": ":0", "XAUTHORITY": "/run/user/1000/xauth", "SECRET_TOKEN": "must-not-appear", "EXTRA_VAR": "extra"}
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch.object(os.path, "isdir", lambda p: p in FAKE_DIRS), \
         mock.patch.object(os.path, "isfile", lambda p: p in FAKE_FILES), \
         mock.patch.object(os.path, "islink", lambda p: False), \
         mock.patch.object(os, "getuid", lambda: 1000), mock.patch.object(os, "getgid", lambda: 1000):
        _, args, state = S["build"](prog, [], False, opts)
    return state["prefix"] + state["wrap"], args


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
            # fake data files are bound read-only from private files, and the boot id is always replaced
            self.assertIn(["--ro-bind", "/run/user/1000/sbx/PID.boot_id", "/proc/sys/kernel/random/boot_id"], [a[k:k + 3] for k in range(len(a) - 2)], title)
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

    def test_net_private_runs_bwrap_inside_pasta_and_changes_only_what_it_must(self):
        wrap, a = fake_preview("firefox", ["--net-private"])
        self.assertEqual(wrap, ["pasta", "--quiet", "--config-net", "--no-map-gw", "--dns-forward", "169.254.1.1",
                                "-t", "none", "-u", "none", "-T", "none", "-U", "none", "--",
                                "/bin/sh", "-c", 'exec 9<"$1"; shift; exec "$@"', "sh", "/run/user/1000/sbx/PID.seccomp"])
        self.assertEqual(a[a.index("--seccomp") + 1], "9")                                  # the shell opens the file as descriptor 9
        self.assertNotIn("FD", a)
        self.assertEqual(a[a.index("--uid"):a.index("--uid") + 4], ["--uid", "1000", "--gid", "1000"])     # pasta's namespace maps you to root
        self.assertEqual(a.count("--share-net"), 1)                                         # bwrap shares pasta's network, not the host's
        triples = [a[k:k + 3] for k in range(len(a) - 2)]
        self.assertIn(["--ro-bind", "/run/user/1000/sbx/PID.resolv.conf", "/run/systemd/resolve/stub-resolv.conf"], triples)   # onto the link's target: bwrap can't mount on a symlink
        self.assertNotIn("/etc/resolv.conf", a)
        self.assertEqual(a[a.index("/sys/class/net") - 1], "--tmpfs")                       # the host's cards stay hidden, network on or not
        self.assertLess(a.index("/run/systemd/resolve"), a.index("/run/user/1000/sbx/PID.resolv.conf"))   # after the folders resolv.conf may point into
        wrap, a = fake_preview("firefox", ["--net-private"], {"resolv_target": lambda: "/etc/resolv.conf"})   # a plain file: bound where it is
        self.assertIn(["--ro-bind", "/run/user/1000/sbx/PID.resolv.conf", "/etc/resolv.conf"], [a[k:k + 3] for k in range(len(a) - 2)])
        # without the switch none of that appears
        for flags in ([], ["--net=none"]):
            wrap, a = fake_preview("firefox", flags)
            self.assertEqual(wrap, [], flags)
            self.assertNotIn("--uid", a); self.assertNotIn("/run/systemd/resolve/stub-resolv.conf", a)
            self.assertEqual(a[a.index("--seccomp") + 1], "FD")
        # with the network off the switch does nothing at all
        wrap, a = fake_preview("firefox", ["--net=none", "--net-private"])
        self.assertEqual(wrap, []); self.assertNotIn("--uid", a); self.assertEqual(a.count("--share-net"), 0)

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

    def test_a_line_without_an_equals_sign_is_reported(self):
        p = self.write("d.conf", "net 0\nwhitelist ~/x\n# fine\n\nnet=0\nwhitelist=\n")
        sc, ls, _ = self.S["parse_conf"](p)
        self.assertEqual((sc, ls["whitelist"]), ({"net": False}, []))
        text = "\n".join(self.warned)
        for must in (":1:", ":2:", "expected key=value", "whitelist has no value"): self.assertIn(must, text)
        self.assertNotIn("net 0", text)                                    # the text of a line is never echoed (it may be anything)

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
    def test_scoped_xauth_keeps_only_this_computers_cookies(self):
        me = os.fsencode(socket.gethostname())
        src = self.write("xauth2"); dst = f"{self.tmp}/out/x2"
        with open(src, "wb") as f:
            f.write(xentry(256, me, b"0", b"AAAA") + xentry(256, b"remote-box", b"0", b"BBBB") + xentry(65535, b"", b"0", b"CCCC"))
        self.assertEqual(self.S["scoped_xauth"](src, ":0", dst), dst)
        with open(dst, "rb") as f: got = f.read()
        self.assertEqual(got, xentry(256, me, b"0", b"AAAA") + xentry(65535, b"", b"0", b"CCCC"))      # not remote-box's
        self.assertIsNone(self.S["scoped_xauth"](src, "localhost:10.0", dst + "x"))                   # SSH-style DISPLAY: nothing

    def test_scoped_xauth_keeps_only_this_display(self):
        def entry(num, data):
            return xentry(256, os.fsencode(socket.gethostname()), num, data)
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
        for f in st["keep"]: f.close()
        for fd in st["fds"]:
            try: os.close(fd)                      # any descriptor the files above did not own
            except OSError: pass
        self.assertFalse(any(cmd[i:i + 3] == ["--ro-bind", bad, bad] for i in range(len(cmd))), "the whole file was shared")
        self.assertNotIn("XAUTHORITY", cmd)
        self.assertTrue(any("XAUTHORITY" in w for w in self.warned), self.warned)

    def test_a_forwarded_display_does_not_get_the_whole_cookie_file(self):
        S = self.S
        xa = self.write("fwd.xauth", "x")
        os.makedirs(f"{self.tmp}/run2")
        S["runtime_dir"] = lambda: f"{self.tmp}/run2"
        with mock.patch.dict(os.environ, {"DISPLAY": "localhost:10.0", "XAUTHORITY": xa, "WAYLAND_DISPLAY": ""}):
            _, cmd, st = S["build"]("/bin/true", [], True, {"x11": True})
        for f in st["keep"]: f.close()
        self.assertFalse(any(cmd[i:i + 3] == ["--ro-bind", xa, xa] for i in range(len(cmd))), "the whole file was shared")

    def test_fake_files_are_private_files_not_descriptors_and_are_removed_after(self):
        S = self.S
        os.makedirs(f"{self.tmp}/run3")
        S["runtime_dir"] = lambda: f"{self.tmp}/run3"
        S["bwrap_has"] = lambda flag: True
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}):
            _, cmd, st = S["build"]("/bin/true", [], True, {"hostname": True})
        self.addCleanup(lambda: [f.close() for f in st["keep"]])
        self.assertNotIn("--ro-bind-data", cmd)                         # nothing a wrapper like pasta would close
        boot = f"{self.tmp}/run3/sbx/{os.getpid()}.boot_id"
        self.assertIn(["--ro-bind", boot, "/proc/sys/kernel/random/boot_id"], [cmd[i:i + 3] for i in range(len(cmd) - 2)])
        with open(boot) as f: self.assertRegex(f.read(), r"^[0-9a-f-]{36}\n$")
        self.assertEqual(os.stat(boot).st_mode & 0o777, 0o600)
        self.assertIn(boot, st["files"])
        S["teardown"](st)
        self.assertFalse(os.path.exists(boot))

    def test_privacy_binds_a_minimal_passwd_without_the_full_name(self):
        S = self.S
        os.makedirs(f"{self.tmp}/run4")
        S["runtime_dir"] = lambda: f"{self.tmp}/run4"
        S["bwrap_has"] = lambda flag: True
        text = S["fake_passwd"]()
        lines = text.splitlines()
        self.assertEqual(len(lines), 2 if os.getuid() == 0 else 3)       # you, root (which may be you), nobody: no other account
        mine = [l for l in lines if l.split(":")[0] == S["USER"]]
        self.assertEqual(len(mine), 1)
        for l in lines: self.assertEqual(len(l.split(":")), 7)           # a valid passwd line, GECOS field empty for you
        self.assertEqual(mine[0].split(":")[4], "")
        self.assertEqual(sorted(l.split(":")[0] for l in lines), sorted({S["USER"], "root", "nobody"}))
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}):
            _, off, st0 = S["build"]("/bin/true", [], True, {})
            _, on, st1 = S["build"]("/bin/true", [], True, {"privacy": True})
        self.addCleanup(lambda: [f.close() for f in st0["keep"] + st1["keep"]])
        real = os.path.realpath("/etc/passwd")
        triples = lambda cmd: [cmd[i:i + 3] for i in range(len(cmd) - 2)]
        self.assertFalse(any(t[0] == "--ro-bind" and t[2] == real for t in triples(off)), "privacy is off by default")
        fake = f"{self.tmp}/run4/sbx/{os.getpid()}.passwd"
        self.assertIn(["--ro-bind", fake, real], triples(on))
        with open(fake) as f: self.assertEqual(f.read(), text)
        self.assertEqual(os.stat(fake).st_mode & 0o777, 0o600)
        S["teardown"](st1); S["teardown"](st0)
        self.assertFalse(os.path.exists(fake))

    def test_privacy_is_in_the_tables_and_its_stale_helper_file_is_swept(self):
        S = self.S
        self.assertIs(S["SCALARS"]["privacy"], False)                    # off by default
        self.assertEqual(S["FLAGS"]["--privacy"], ("privacy", True))
        os.makedirs(f"{self.tmp}/run5/sbx")
        S["runtime_dir"] = lambda: f"{self.tmp}/run5"
        dead = 2 ** 22 + 12345                                           # above pid_max: no such process
        stale = f"{self.tmp}/run5/sbx/{dead}.passwd"
        open(stale, "w").close()
        S["sweep_stale"]()
        self.assertFalse(os.path.exists(stale))

    def test_net_private_is_a_switch_a_flag_and_needs_the_network(self):
        S = self.S
        self.assertIs(S["SCALARS"]["net_private"], False)                                 # off unless asked for
        self.assertEqual(S["parse_options"](["--net-private", "x"])[0], {"net_private": True})
        sc, _, _ = S["parse_conf"](self.write("n.conf", "net_private=1\n"))
        self.assertEqual(sc, {"net_private": True})
        self.assertIn("net_private", S["schema"]()["switches"])
        self.assertTrue(S["private_net"]({"net": True, "net_private": True}))
        self.assertFalse(S["private_net"]({"net": False, "net_private": True}))
        self.assertFalse(S["private_net"]({"net": True, "net_private": False}))

    def test_net_private_files_are_private_files_not_descriptors_and_are_removed_after(self):
        S = self.S
        os.makedirs(f"{self.tmp}/run4")
        S["runtime_dir"] = lambda: f"{self.tmp}/run4"
        S["resolv_target"] = lambda: "/run/systemd/resolve/stub-resolv.conf"
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}), mock.patch.object(shutil, "which", lambda x: f"/usr/bin/{x}"):
            _, cmd, st = S["build"]("/bin/true", [], True, {"net_private": True, "allow_syscall": ["io_uring"]})
        self.addCleanup(lambda: [f.close() for f in st["keep"]])
        d = f"{self.tmp}/run4/sbx"
        sec, resolv = f"{d}/{os.getpid()}.seccomp", f"{d}/{os.getpid()}.resolv.conf"
        self.assertEqual(st["fds"], []); self.assertEqual(st["keep"], [])                 # pasta would close anything inherited
        self.assertEqual(st["wrap"][-2:], ["sh", sec])
        self.assertEqual(cmd[cmd.index("--seccomp") + 1], "9")
        with open(sec, "rb") as f: self.assertEqual(f.read(), S["seccomp_program"](S["platform"].machine(), ["io_uring"]))
        with open(resolv) as f: self.assertEqual(f.read(), "nameserver 169.254.1.1\n")
        for p in (sec, resolv):
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600); self.assertIn(p, st["files"])
        if os.path.isfile("/etc/resolv.conf"):                 # (a machine without one gets no name server file)
            self.assertIn(["--ro-bind", resolv, "/run/systemd/resolve/stub-resolv.conf"], [cmd[i:i + 3] for i in range(len(cmd) - 2)])
        S["teardown"](st)
        self.assertFalse(os.path.exists(sec) or os.path.exists(resolv))

    def test_net_private_refuses_to_start_without_pasta_and_warns_when_the_network_is_off(self):
        S = self.S
        os.makedirs(f"{self.tmp}/run5")
        S["runtime_dir"] = lambda: f"{self.tmp}/run5"
        real_which = shutil.which
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}), \
             mock.patch.object(shutil, "which", lambda x: None if x == "pasta" else real_which(x)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as r: S["build"]("/bin/true", [], True, {"net_private": True})
            self.assertNotEqual(r.exception.code, 0)
            self.assertRegex(err.getvalue(), r"net_private=1 needs pasta"); self.assertIn("passt", err.getvalue())
            self.assertFalse(os.path.exists(f"{self.tmp}/run5/sbx"), "nothing was made before refusing")
            self.assertEqual(self.warned, [])
            _, cmd, st = S["build"]("/bin/true", [], True, {"net_private": True, "net": False})      # no pasta needed: no network at all
        self.addCleanup(lambda: [f.close() for f in st["keep"]])
        self.assertEqual(st["wrap"], []); self.assertNotIn("--uid", cmd); self.assertNotIn("--share-net", cmd)
        self.assertEqual(len(self.warned), 1); self.assertIn("net_private does nothing while net=0", self.warned[0])
        S["teardown"](st)

    def test_preview_prints_the_pasta_command_first(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.S["main"](["preview", "--ignore-config", "--net-private", "/bin/true"])
        words = __import__("shlex").split(out.getvalue())
        self.assertEqual(words[:2], ["pasta", "--quiet"]); self.assertLess(words.index("pasta"), words.index("bwrap"))

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

    def test_friendly_messages(self):
        S = self.S
        def dies(fn, *a):
            msgs = []
            S["die"] = lambda m, code=1: (msgs.append(m), (_ for _ in ()).throw(SystemExit(code)))[1]
            try: fn(*a)
            except SystemExit: pass
            return msgs[0]
        self.assertIn("did you mean --net=none?", dies(S["parse_options"], ["--net-none", "x"]))
        self.assertIn("needs a value", dies(S["parse_options"], ["--whitelist", "x"]))
        self.assertIn("doesn't take a value", dies(S["parse_options"], ["--private=1", "x"]))
        self.assertIn("not found in your PATH", dies(S["find_program"], "no-such-program-xyz"))
        d = tempfile.mkdtemp(); f = f"{d}/plain"
        with open(f, "w") as fh: fh.write("x")
        self.assertIn("is not executable", dies(S["find_program"], f))
        old = os.getcwd()
        try:
            os.chdir(d)
            self.assertIn("Write ./plain", dies(S["find_program"], "plain"))
        finally: os.chdir(old); shutil.rmtree(d)

    def test_list_says_so_when_empty(self):
        S = self.S
        S["HOMES"] = S["CONF"] = tempfile.mkdtemp()
        S["running"] = lambda *a: []
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_list"]([])
        self.assertIn("No programs yet", out.getvalue())

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

    # -- which file a program name belongs to (check_origin) and the decisions it leaves pending
    def _origins(self):
        """Real files in the temp home; die raises, notifications and sbx's own messages are recorded, and stdin is not a terminal."""
        S = self.S
        self.said, self.pinged = [], []
        S["warn"] = self.said.append
        def die(msg, code=1): self.said.append("DIE " + msg); raise SystemExit(code)
        S["die"] = die
        S["notify_click"] = lambda plain, clicked, label, args, urgency="critical": self.pinged.append((label, args))
        quiet = mock.patch.object(sys, "stdin", io.StringIO()); quiet.start(); self.addCleanup(quiet.stop)
        nowl = mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": ""}); nowl.start(); self.addCleanup(nowl.stop)     # a desktop's compositor adds warnings

    def origin(self, name, real, **kw):
        """Run check_origin: True if the program may start."""
        try: self.S["check_origin"](name, real, **kw); return True
        except SystemExit: return False

    def recorded(self, name):
        with open(f"{self.S['CONF']}/origins/{name}") as f: return f.read().split("\n")

    def test_the_first_run_records_the_path_and_a_fingerprint_of_the_file(self):
        self._origins()
        p = self.write("Downloads/Crash", "version one")
        self.assertTrue(self.origin("game", p))
        lines = self.recorded("game")
        self.assertEqual(lines[0], p)
        size, mtime, sha = lines[1].split()
        self.assertEqual((int(size), int(mtime)), (11, os.stat(p).st_mtime_ns))
        self.assertEqual(sha, hashlib.sha256(b"version one").hexdigest())            # the whole file, in full
        self.assertEqual(self.said + self.pinged, [])

    def test_a_program_moved_with_the_same_content_is_accepted_silently(self):
        self._origins()
        old = self.write("Downloads/Crash", "same bytes")
        self.origin("game", old)
        new = f"{self.home}/Games/Crash"; os.makedirs(os.path.dirname(new)); shutil.move(old, new)
        self.assertTrue(self.origin("game", new))
        self.assertEqual(self.said + self.pinged, [], "rule a says nothing")
        lines = self.recorded("game")
        self.assertEqual((lines[0], lines[2]), (new, f"from {old}"))                 # the record moved with it, and remembers where from
        self.assertEqual(self.S["read_origin"]("game")[2], old)

    def test_a_copy_is_the_same_program_even_while_the_original_exists(self):
        self._origins()
        old = self.write("apps/firefox", "same bytes")
        self.origin("ff", old)
        copy = self.write("Downloads/firefox", "same bytes")
        self.assertTrue(self.origin("ff", copy))
        self.assertEqual(self.said + self.pinged, [])
        self.assertEqual(self.recorded("ff")[0], copy)
        self.assertEqual(self.S["origin_source"]("ff")["moved_exists"], True)           # shown as a copy, not as a move

    def test_a_copy_of_an_original_that_has_patched_itself_since_is_still_the_same_program(self):
        self._origins()
        old = self.write("apps/tool", "version one")
        self.origin("tool", old)
        with open(old, "w") as f: f.write("version one, patched")                        # the record's hash is out of date now
        os.utime(old, ns=(5, 5))
        copy = self.write("copy/tool", "version one, patched")
        self.assertTrue(self.origin("tool", copy)); self.assertEqual(self.said + self.pinged, [])

    def test_moved_and_updated_is_accepted_with_one_line(self):
        self._origins()
        old = self.write("Downloads/Crash", "version one")
        self.origin("game", old)
        os.unlink(old)
        new = self.write("Games/Crash", "version two, longer")                          # another content, same file name, the old one gone
        self.assertTrue(self.origin("game", new))
        self.assertEqual(self.said, ["'game' moved from ~/Downloads/Crash to ~/Games/Crash"])
        self.assertEqual(self.pinged, [])
        self.assertEqual(self.recorded("game")[:1], [new])
        self.assertEqual(self.recorded("game")[1].split()[2], hashlib.sha256(b"version two, longer").hexdigest())
        self.assertIsNone(self.S["read_pending"]("game"))

    def test_an_old_file_that_is_gone_and_a_new_name_is_asked_about_on_a_terminal(self):
        self._origins(); S = self.S
        old = self.write("Downloads/Crash", "one"); self.origin("game", old); os.unlink(old)
        new = self.write("Games/CrashDeluxe", "two")
        tty = mock.Mock(); tty.isatty.return_value = True
        for answer in ("n", "", "no"):                                                  # anything but y or yes is no
            with mock.patch.object(sys, "stdin", tty), mock.patch("builtins.input", return_value=answer) as ask:
                self.assertFalse(self.origin("game", new), answer)
            self.assertIn("Same program? [y/N]", ask.call_args[0][0]); self.assertIn("~/Downloads/Crash", ask.call_args[0][0])
        with mock.patch.object(sys, "stdin", tty), mock.patch("builtins.input", side_effect=EOFError):
            self.assertFalse(self.origin("game", new))                                  # the terminal closed under it: no
        self.assertIn("--name=OTHER", self.said[-1])
        self.assertEqual(self.recorded("game")[0], old, "a no changes nothing")
        self.assertIsNone(S["read_pending"]("game"), "somebody just said no: nothing is left waiting"); self.assertEqual(self.pinged, [])
        with mock.patch.object(sys, "stdin", tty), mock.patch("builtins.input", return_value="y"):
            self.assertTrue(self.origin("game", new))
        self.assertEqual(self.recorded("game")[0], new)

    def test_an_old_file_that_is_gone_and_a_new_name_without_a_terminal_is_left_pending(self):
        self._origins(); S = self.S
        old = self.write("Downloads/Crash", "one"); self.origin("game", old); os.unlink(old)
        new = self.write("Games/CrashDeluxe", "two")
        self.assertFalse(self.origin("game", new), "it must not start")
        p = S["read_pending"]("game")
        self.assertEqual((p["old"], p["new"], p["rule"]), (old, new, "c"))
        self.assertIn("~/Downloads/Crash", p["reason"])
        self.assertEqual(self.recorded("game")[0], old, "nothing is decided yet")
        self.assertEqual(self.pinged, [("Open sbx", ["game"])])                          # the click opens sbx-gui on that program
        self.assertIn("sbx rebind game", self.said[-1])
        self.assertEqual(S["pending_names"](), ["game"])

    def test_a_different_program_while_the_original_exists_is_refused_and_left_pending(self):
        self._origins(); S = self.S
        old = self.write("apps/firefox", "the real one"); self.origin("ff", old)
        evil = self.write("Downloads/firefox", "something else entirely")
        self.assertFalse(self.origin("ff", evil))
        p = S["read_pending"]("ff"); self.assertEqual((p["old"], p["new"], p["rule"]), (old, evil, "d"))
        self.assertIn("--name=OTHER", self.said[-1]); self.assertEqual(self.pinged, [("Open sbx", ["ff"])])
        self.assertEqual(self.recorded("ff")[0], old)
        tty = mock.Mock(); tty.isatty.return_value = True
        with mock.patch.object(sys, "stdin", tty), mock.patch("builtins.input") as ask:
            self.assertFalse(self.origin("ff", evil))
        ask.assert_not_called()                                                          # on a terminal too: rule d is not a question

    def test_the_same_size_with_other_content_is_a_different_program(self):
        self._origins()
        a = self.write("one/z", "1234"); self.origin("z", a)
        b = self.write("two/z", "5678")
        self.assertEqual(os.stat(a).st_size, os.stat(b).st_size)
        self.assertFalse(self.origin("z", b)); self.assertEqual(self.S["read_pending"]("z")["rule"], "d")

    def test_a_program_changed_in_place_always_starts_and_only_the_record_follows(self):
        self._origins(); S = self.S
        p = self.write("g/game", "AAAA"); self.origin("game", p)
        before = self.recorded("game")[1]
        hashed = []
        real = S["fingerprint"]; S["fingerprint"] = lambda path: hashed.append(path) or real(path)
        self.assertTrue(self.origin("game", p)); self.assertEqual(hashed, [], "unchanged size and mtime: the file is not read again")
        with open(p, "w") as f: f.write("BBBBBB")                                         # patched itself
        self.assertTrue(self.origin("game", p)); self.assertEqual(hashed, [p])
        self.assertNotEqual(self.recorded("game")[1], before)
        self.assertEqual(self.recorded("game")[1].split()[2], hashlib.sha256(b"BBBBBB").hexdigest())
        self.assertEqual(self.said + self.pinged, [])
        with open(p, "w") as f: f.write("CCCCCC")
        S["write_origin"] = mock.Mock(side_effect=OSError("read-only"))                  # even a record that can't be written is no reason to stop
        self.assertTrue(self.origin("game", p))

    def test_an_origin_file_from_an_older_version_keeps_working(self):
        self._origins(); S = self.S
        def legacy(name, path):
            os.makedirs(f"{S['CONF']}/origins", exist_ok=True)
            with open(f"{S['CONF']}/origins/{name}", "w") as f: f.write(path + "\n")
        a = self.write("apps/leg", "same"); legacy("leg", a)
        self.assertIsNone(S["read_origin"]("leg")[1])
        self.assertTrue(self.origin("leg", a))                                          # path matches: line 2 is added, nothing else
        self.assertEqual(self.recorded("leg")[1].split()[2], hashlib.sha256(b"same").hexdigest()); self.assertEqual(self.said, [])
        b = self.write("apps/leg2", "same"); legacy("two", a)                           # path differs, the old file is still there: it is hashed now
        self.assertTrue(self.origin("two", b)); self.assertEqual(self.recorded("two")[0], b)
        c = self.write("elsewhere/other", "other"); legacy("three", a)
        self.assertFalse(self.origin("three", c)); self.assertEqual(S["read_pending"]("three")["rule"], "d")
        os.unlink(a); legacy("four", a)                                                  # old gone, nothing to compare with: by name, as for any other change
        self.assertFalse(self.origin("four", c)); self.assertEqual(S["read_pending"]("four")["rule"], "c")
        d = self.write("elsewhere/leg", "x"); legacy("five", a.replace("leg", "leg"))
        self.assertTrue(self.origin("five", self.write("elsewhere/leg", "x2"))); self.assertIn("moved from", self.said[-1]); del d

    def test_sbx_rebind_accepts_the_pending_decision(self):
        self._origins(); S = self.S
        old = self.write("Downloads/Crash", "one"); self.origin("game", old); os.unlink(old)
        new = self.write("Games/CrashDeluxe", "two"); self.origin("game", new)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_rebind"]([ "game"])
        self.assertIn("~/Games/CrashDeluxe", out.getvalue())
        self.assertIsNone(S["read_pending"]("game")); self.assertFalse(os.path.exists(f"{S['CONF']}/pending/game"))
        self.assertEqual((self.recorded("game")[0], self.recorded("game")[2]), (new, f"from {old}"))
        self.assertTrue(self.origin("game", new)); self.assertEqual(self.recorded("game")[1].split()[2], hashlib.sha256(b"two").hexdigest())   # the next run adds line 2
        with self.assertRaises(SystemExit): S["cmd_rebind"]([ "game"])                    # nothing left to accept
        self.assertIn("nothing is waiting", self.said[-1])

    def test_sbx_rebind_dismiss_drops_the_decision_and_changes_nothing_else(self):
        self._origins(); S = self.S
        old = self.write("apps/ff", "real"); self.origin("ff", old)
        evil = self.write("Downloads/ff", "evil"); self.origin("ff", evil)
        with contextlib.redirect_stdout(io.StringIO()): S["cmd_rebind"](["--dismiss", "ff"])
        self.assertIsNone(S["read_pending"]("ff")); self.assertEqual(self.recorded("ff")[0], old)
        with self.assertRaises(SystemExit): S["cmd_rebind"](["--dismiss", "ff"])
        self.assertFalse(self.origin("ff", evil)); self.assertEqual(S["read_pending"]("ff")["rule"], "d")      # the next run asks again

    def test_a_pending_decision_goes_away_when_the_situation_does(self):
        self._origins(); S = self.S
        old = self.write("Downloads/Crash", "one"); self.origin("game", old); os.unlink(old)
        new = self.write("Games/CrashDeluxe", "two"); self.origin("game", new)
        self.assertEqual(S["pending_names"](), ["game"])
        self.write("Downloads/Crash", "one")                                              # the file went back to the recorded path
        self.assertEqual(S["pending_names"](), [], "no longer listed even before the next run")
        p = S["read_pending"]("game"); self.assertIn("is back", S["pending_stale"]("game", p))
        self.assertTrue(self.origin("game", old))                                         # and the next run from there clears it
        self.assertFalse(os.path.exists(f"{S['CONF']}/pending/game"))
        # rule d: the original is deleted, or the other file is
        other = self.write("Downloads/Crash2", "z"); self.origin("game", other)
        p = S["read_pending"]("game"); self.assertEqual(p["rule"], "d")
        os.unlink(other); self.assertIn("is gone", S["pending_stale"]("game", p))
        self.write("Downloads/Crash2", "z"); os.unlink(old)
        with self.assertRaises(SystemExit): S["cmd_rebind"](["game"])                     # accepting a stale one is refused and cleans up
        self.assertIn("no longer applies", self.said[-1]); self.assertIsNone(S["read_pending"]("game"))
        self.assertEqual(self.recorded("game")[0], old)
        # being accepted by another way clears it too: --rebind, or a copy with the same content
        self.write("Downloads/Crash", "one"); self.origin("game", self.write("a/Crash", "q")); self.assertIsNotNone(S["read_pending"]("game"))
        self.assertTrue(self.origin("game", self.write("a/Crash", "q"), rebind=True)); self.assertIsNone(S["read_pending"]("game"))

    def test_rebind_flag_is_still_the_override_for_a_different_file(self):
        self._origins()
        a = self.write("apps/ff", "real"); self.origin("ff", a)
        b = self.write("Downloads/ff", "evil")
        self.assertFalse(self.origin("ff", b)); self.assertTrue(self.origin("ff", b, rebind=True))
        self.assertEqual(self.recorded("ff")[0], b); self.assertIsNone(self.S["read_pending"]("ff"))

    def test_moves_between_system_folders_are_fine_and_a_system_name_is_not_free(self):
        self._origins(); S = self.S
        S["SYSTEM_PROGRAMS"] = (f"{self.home}/sys1/", f"{self.home}/sys2/")
        a = self.write("sys1/ls", "coreutils 9"); self.origin("ls", a)
        b = self.write("sys2/bin-ls", "coreutils 10")                                    # an update that also changed the folder and the file name
        self.assertTrue(self.origin("ls", b)); self.assertEqual(self.said + self.pinged, [])
        evil = self.write("evil/ls", "not coreutils")
        self.assertFalse(self.origin("ls", evil)); self.assertEqual(S["read_pending"]("ls")["rule"], "d")

    def test_a_file_name_with_a_line_break_cannot_be_recorded(self):
        self._origins()
        p = self.write("odd/a\nb", "x")
        self.assertFalse(self.origin("odd", p)); self.assertIn("line break", self.said[-1])

    def test_rebind_command_checks_its_arguments_and_where_it_runs(self):
        self._origins(); S = self.S
        for bad in ([], ["--dismiss"], ["a", "b"], ["--bogus"], ["../x"]):
            with self.assertRaises(SystemExit, msg=bad): S["cmd_rebind"](bad)
        with mock.patch.dict(os.environ, {"SBX_ACTIVE": "1"}), self.assertRaises(SystemExit): S["cmd_rebind"](["game"])
        self.assertIn("rebind", S["COMMANDS"])

    def test_cleaning_a_program_also_drops_its_pending_decision(self):
        self._origins(); S = self.S
        old = self.write("apps/ff", "real"); self.origin("ff", old); self.origin("ff", self.write("Downloads/ff", "evil"))
        os.makedirs(f"{S['HOMES']}/ff"); self.assertTrue(os.path.exists(f"{S['CONF']}/pending/ff"))
        S["wipe_home"]("ff")
        for d in ("origins", "pending"): self.assertFalse(os.path.exists(f"{S['CONF']}/{d}/ff"), d)

    def test_the_waiting_notification_opens_sbx_on_that_program(self):
        S = self.S
        def which(x): return {"notify-send": "/usr/bin/notify-send", "sbx-gui": "/usr/bin/sbx-gui"}.get(x)
        with mock.patch("shutil.which", side_effect=which), mock.patch("os.access", return_value=True), \
             mock.patch("subprocess.run", return_value=types.SimpleNamespace(stdout="no actions")) as run:
            S["notify_click"]("plain text", "clickable text", "Open sbx", ["game"])
        sent = run.call_args_list[-1].args[0]; self.assertIn("plain text", sent); self.assertNotIn("-A", sent)      # no actions: a plain popup
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            return types.SimpleNamespace(stdout="--action" if "--help" in cmd else "default\n")
        with mock.patch("shutil.which", side_effect=which), mock.patch("os.access", return_value=True), mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("os.fork", return_value=0), mock.patch("os.setsid"), mock.patch("os.open", return_value=3), mock.patch("os.dup2"), \
             mock.patch("os._exit", side_effect=SystemExit), mock.patch("subprocess.Popen") as popen, self.assertRaises(SystemExit):
            S["notify_click"]("plain text", "clickable text", "Open sbx", ["game"])                        # the child that waits for the click
        wait = calls[-1]; self.assertIn("default=Open sbx", wait); self.assertIn("clickable text", wait); self.assertIn("--wait", wait)
        self.assertEqual(popen.call_args[0][0], ["/usr/bin/sbx-gui", "game"])                             # a click: sbx-gui game

    # -- where a program comes from (list, show)
    def test_a_program_comes_from_the_system_your_downloads_or_your_home(self):
        S = self.S
        self.write(".config/user-dirs.dirs", 'XDG_DOWNLOAD_DIR="$HOME/Transferencias"\n')
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{self.home}/.config"}):
            for path, kind in (("/usr/bin/firefox", "System"), ("/opt/x/bin/x", "System"), (f"{self.home}/Transferencias/Crash", "Downloaded"),
                               (f"{self.home}/Downloads/Crash", "Home"), (f"{self.home}/Games/Crash", "Home"), ("/tmp/x/run", "Other")):
                self.assertEqual(S["source_kind"](path), kind, path)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{self.home}/none"}):
            self.assertEqual(S["source_kind"](f"{self.home}/Downloads/Crash"), "Downloaded")           # no user-dirs.dirs: the usual name
        text = lambda **k: S["source_text"]({"kind": "Home", "path": f"{self.home}/Games/Crash", "missing": False, "moved": None, "moved_exists": False, **k})
        self.assertEqual(text(), "Home: ~/Games/Crash")
        self.assertEqual(text(kind="System", path="/usr/bin/firefox"), "System: /usr/bin")
        self.assertEqual(text(missing=True), "Missing: ~/Games/Crash")
        self.assertEqual(text(moved=f"{self.home}/Downloads/Crash"), "Home: ~/Games/Crash, moved from ~/Downloads/Crash")
        self.assertEqual(text(moved=f"{self.home}/Downloads/Crash", moved_exists=True), "Home: ~/Games/Crash, copied from ~/Downloads/Crash")

    def _sourced(self):
        """Three programs that only have a private home, from a Downloads folder, the home and the system, named so that name order is the wrong order."""
        self._origins(); S, h = self.S, self.home
        S["running"] = lambda *a: []
        S["SYSTEM_PROGRAMS"] = (f"{h}/sys/",)
        for n, rel in (("aaa", "Downloads/Crash"), ("bbb", "Games/Other"), ("ccc", "sys/bin/tool")):
            os.makedirs(f"{S['HOMES']}/{n}"); self.origin(n, self.write(rel, n))
        os.makedirs(f"{S['HOMES']}/ddd"); self.write(".config/sbx/apps/ddd.conf", "net=0\n")                     # set up, no origin yet

    def test_list_shows_the_source_and_puts_system_programs_first_and_downloaded_last(self):
        self._sourced()
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{self.home}/.config"}):
            every = self._list("--all")
        rows = every.splitlines()
        self.assertEqual([r.split()[0] for r in rows], ["ddd", "ccc", "bbb", "aaa"])             # set up first, then System, Home, Downloaded
        self.assertRegex(rows[1], r"ccc\s+idle\s+System: " + re.escape(f"{self.home}/sys/bin") + r"\s+\(only run from the terminal\)")
        self.assertRegex(rows[3], r"aaa\s+idle\s+Downloaded: ~/Downloads/Crash\s+\(only")
        self.assertNotIn("Downloaded", self._list().replace("Downloaded: ~/Downloads/Crash", ""))      # without --all the hidden ones stay hidden
        os.unlink(f"{self.home}/Games/Other")
        self.assertRegex(self._list("--all"), r"bbb\s+idle\s+Missing: ~/Games/Other")
        os.makedirs(f"{self.home}/.local/bin"); os.symlink("/usr/local/bin/sbx", f"{self.home}/.local/bin/ddd")
        self.assertRegex(self._list(), r"ddd\s+idle\s+\[link\]")

    def test_list_and_show_say_when_a_decision_is_waiting(self):
        self._sourced(); S = self.S
        old = self.write("apps/ff", "real"); self.origin("ff", old); os.makedirs(f"{S['HOMES']}/ff"); self.write(".config/sbx/apps/ff.conf", "net=0\n")
        evil = self.write("Downloads/ff", "evil"); self.origin("ff", evil)
        self.assertRegex(self._list(), r"ff\s+idle\s+Home: ~/apps/ff\s+waiting for your decision: sbx rebind ff")
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_show"]([ "ff"])
        text = out.getvalue()
        self.assertRegex(text, r"source\s+= Home: ~/apps/ff")
        self.assertRegex(text, r"waiting\s+= ~/apps/ff -> ~/Downloads/ff"); self.assertIn("sbx rebind ff", text)
        with contextlib.redirect_stdout(io.StringIO()): S["cmd_rebind"](["--dismiss", "ff"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_show"](["ff"])
        self.assertNotIn("waiting", out.getvalue()); self.assertIn("source", out.getvalue())

    # -- what a program stored beside its file follows the move (carry_data)
    def _stored_beside(self, folder="Downloads"):
        """`game` ran from ~/Downloads/Crash and wrote beside itself: in its private home that is Downloads/..."""
        self._origins(); S = self.S
        old = self.write(f"{folder}/Crash", "the game"); self.origin("game", old)
        h = f"{S['HOMES']}/game/{folder}"
        for rel, text in (("settings.json", "disc=a.chd"), ("save/carda.sav", "1"), ("game/x/main.cs", "2")):
            os.makedirs(os.path.dirname(f"{h}/{rel}"), exist_ok=True); open(f"{h}/{rel}", "w").write(text)
        return old, h

    def test_what_a_program_stored_beside_its_file_follows_it_to_the_new_folder(self):
        old, h = self._stored_beside(); S = self.S
        new = f"{self.home}/Downloads/retro games/Crash"; os.makedirs(os.path.dirname(new)); shutil.move(old, new)
        self.assertTrue(self.origin("game", new))
        t = f"{S['HOMES']}/game/Downloads/retro games"
        self.assertEqual(open(f"{t}/settings.json").read(), "disc=a.chd")
        self.assertTrue(os.path.exists(f"{t}/save/carda.sav") and os.path.exists(f"{t}/game/x/main.cs"))
        self.assertEqual(sorted(os.listdir(h)), ["retro games"], "the new folder is inside the old one: it stays, the rest moved in")
        self.assertEqual(self.said, ["'game': moved 3 items it had stored beside its old file, from ~/Downloads to ~/Downloads/retro games, in its private home"])
        self.said.clear(); self.assertTrue(self.origin("game", new)); self.assertEqual(self.said, [], "once")

    def test_nothing_is_carried_over_a_copy_or_over_what_is_already_there(self):
        old, h = self._stored_beside(); S = self.S
        copy = self.write("Games/Crash", "the game")                                    # the original is still there: it may be in use
        self.assertTrue(self.origin("game", copy)); self.assertEqual(os.listdir(h).count("settings.json"), 1)
        self.assertEqual(os.listdir(f"{S['HOMES']}/game/Games") if os.path.isdir(f"{S['HOMES']}/game/Games") else [], [])
        os.unlink(old); os.unlink(copy)
        new = self.write("Moved/Crash", "the game")
        os.makedirs(f"{S['HOMES']}/game/Moved"); open(f"{S['HOMES']}/game/Moved/settings.json", "w").write("fresh")
        self.assertEqual(S["carry_data"]("game", old, new), 2, "what is free moves (save, game), what is taken stays (settings.json)")
        self.assertEqual(open(f"{S['HOMES']}/game/Moved/settings.json").read(), "fresh", "never over something that is there")
        self.assertEqual(open(f"{h}/settings.json").read(), "disc=a.chd", "and it stays where it was")

    def test_carrying_stays_inside_the_private_home_and_inside_your_home(self):
        old, h = self._stored_beside(); S = self.S
        os.unlink(old)
        outside = f"{self.tmp}/outside"; os.makedirs(outside)
        os.makedirs(f"{S['HOMES']}/game/Evil"); os.symlink(outside, f"{S['HOMES']}/game/Evil/sub")      # a link the program planted in its home
        new = f"{self.home}/Evil/sub/Crash"; os.makedirs(os.path.dirname(new))
        self.assertEqual(S["carry_data"]("game", old, new), 0); self.assertEqual(os.listdir(outside), [])
        self.assertEqual(S["carry_data"]("game", old, f"{self.tmp}/elsewhere/Crash"), 0, "a file outside your home")
        self.assertEqual(S["carry_data"]("game", "/mnt/games/Crash", new), 0)
        self.assertEqual(S["carry_data"]("game", None, new), 0)
        link = f"{S['HOMES']}/linked"; os.symlink(f"{S['HOMES']}/game", link)             # the whole private home is a link
        self.assertEqual(S["carry_data"]("linked", old, f"{self.home}/Moved/Crash"), 0)

    def test_sbx_rebind_carries_it_too(self):
        self._origins(); S = self.S
        old = self.write("Downloads/Crash", "one"); self.origin("game", old)
        h = f"{S['HOMES']}/game/Downloads"; os.makedirs(h); open(f"{h}/settings.json", "w").write("s")
        os.unlink(old); new = self.write("Games/CrashDeluxe", "two"); self.origin("game", new)       # rule c: waits
        with contextlib.redirect_stdout(io.StringIO()): S["cmd_rebind"](["game"])
        self.assertEqual(open(f"{S['HOMES']}/game/Games/settings.json").read(), "s")

    # -- a renamed file is still its program: looked up by content before its file name counts (file_owner)
    def _renamed(self):
        """Game-Linux ran once and was renamed to Game; the renamed file was run too, so leftovers of that name exist."""
        self._origins(); S = self.S
        old = self.write("Downloads/test/Game-Linux", "the game " * 50); os.chmod(old, 0o755)
        self.assertTrue(self.origin("Game-Linux", old))
        self._put(f"{S['CONF']}/apps/Game-Linux.conf", "net=0\n")
        self._put(f"{S['HOMES']}/Game-Linux/save", "slot 1" * 3000)
        new = f"{self.home}/Downloads/test/Game"; os.rename(old, new)
        return old, new

    def _put(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f: f.write(text)

    def _fresh_name(self, new, **kw):
        """The name build() runs the file under, with the cleanup of a live run (and what it said)."""
        S = self.S
        name, _cmd, st = S["build"](new, [], True, kw)
        S["teardown"](st)
        return name

    def test_a_renamed_file_keeps_its_program_even_when_its_new_name_has_leftovers(self):
        old, new = self._renamed(); S = self.S
        leftover = f"{S['HOMES']}/Game"; os.makedirs(leftover)                  # an earlier run of the renamed file
        S["write_origin"]("Game", new, S["fingerprint"](new))                  # with the same hash
        self.said.clear()
        self.assertEqual(S["file_owner"]("Game", new), ("Game-Linux", old, None))
        self.assertEqual(self._fresh_name(new), "Game-Linux")
        self.assertEqual(S["read_origin"]("Game-Linux")[0], new, "the record follows the file")
        self.assertIsNone(S["read_origin"]("Game"), "the second record of the same file is dropped")
        self.assertTrue(os.path.isdir(leftover), "an unused home is left alone, not deleted")
        self.assertEqual(len(self.said), 1, self.said)
        self.assertIn("'Game' is 'Game-Linux' renamed", self.said[0]); self.assertIn("sbx clean NAME", self.said[0]); self.assertIn("~/.sbx-homes/Game", self.said[0])
        self.assertTrue(os.path.exists(f"{S['CONF']}/apps/Game-Linux.conf"), "its settings stay")
        self.assertEqual(os.path.getsize(f"{S['HOMES']}/Game-Linux/save"), 6 * 3000)
        self.said.clear()
        self.assertEqual(self._fresh_name(new), "Game-Linux", "and the next run finds it again, quietly")
        self.assertEqual(self.said, []); self.assertEqual(self.pinged, [])

    def test_a_renamed_file_without_any_leftovers_and_a_preview_change_nothing(self):
        old, new = self._renamed(); S = self.S
        name, _cmd, _st = S["build"](new, [], False, {})                                   # a preview only looks
        self.assertEqual(name, "Game-Linux"); self.assertEqual(self.said, [])
        self.assertEqual(S["read_origin"]("Game-Linux")[0], old, "nothing recorded by a preview")
        self.assertEqual(self._fresh_name(new), "Game-Linux")
        self.assertNotIn("left over", self.said[0])

    def test_a_copy_a_named_run_and_other_programs_keep_their_own_name(self):
        old, new = self._renamed(); S = self.S
        self.assertEqual(S["file_owner"]("x", "/bin/sh"), ("x", None, None), "system programs are never looked up")
        os.rename(new, old)                                                               # back where it was: the original is there
        copy = self.write("Downloads/test/Game", "the game " * 50); os.chmod(copy, 0o755)
        self.assertEqual(S["file_owner"]("Game", copy), ("Game", None, None), "a copy is a program of its own")
        os.unlink(old)
        self.assertEqual(self._fresh_name(copy, name="mine"), "mine", "--name is never overridden")
        other = self.write("Downloads/test/Other", "x" * len("the game " * 50))            # same size, other content
        self.assertEqual(S["file_owner"]("Other", other), ("Other", None, None))

    def test_only_files_of_a_recorded_size_are_read(self):
        old, new = self._renamed(); S = self.S
        hashed = []
        real_fp = S["fingerprint"]; S["fingerprint"] = lambda p: (hashed.append(p), real_fp(p))[1]
        other = self.write("Downloads/test/Big", "other size")
        self.assertEqual(S["file_owner"]("Big", other)[0], "Big"); self.assertEqual(hashed, [])
        self.assertEqual(S["file_owner"]("Game", new)[0], "Game-Linux"); self.assertEqual(hashed, [new])

    def test_a_new_name_with_settings_of_its_own_is_not_taken_over_but_leftover_data_is_no_reason(self):
        old, new = self._renamed(); S = self.S
        self._put(f"{S['CONF']}/apps/Game.conf", "net=1\n")
        owner, was, why = S["file_owner"]("Game", new)
        self.assertEqual((owner, was), ("Game", None)); self.assertIn("--name=Game-Linux", why)
        self.said.clear(); self.assertEqual(self._fresh_name(new), "Game"); self.assertIn("stays 'Game'", self.said[0])
        os.unlink(f"{S['CONF']}/apps/Game.conf")
        self._put(f"{S['HOMES']}/Game/save", "s" * 70000)                                # a big home from runs under the wrong name, no record
        self.assertEqual(S["file_owner"]("Game", new)[0], "Game-Linux", "data in a leftover home is no reason to start a second program")
        S["write_origin"]("Game", new, S["fingerprint"](new))
        self.assertEqual(S["file_owner"]("Game", new)[0], "Game-Linux")
        # settings that belong to another program of that name (its own file is there) don't stop this file being the renamed one
        other = self.write("Apps/Game", "different"); self.assertTrue(self.origin("Game", other, rebind=True))
        self._put(f"{S['CONF']}/apps/Game.conf", "net=1\n")
        self.assertEqual(S["file_owner"]("Game", new)[0], "Game-Linux")
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_show"]([new])
        self.assertIn("is 'Game-Linux' renamed", out.getvalue()); self.assertIn("Game-Linux   private home", out.getvalue())

    def test_a_renamed_file_that_is_then_moved_and_run_under_its_leftover_name_is_still_its_program(self):
        old, new = self._renamed(); S = self.S
        self.assertEqual(self._fresh_name(new), "Game-Linux")                              # claimed at the rename; its big leftover home stays
        self._put(f"{S['HOMES']}/Game/save", "s" * 70000)
        moved = f"{self.home}/Elsewhere/Game"; os.makedirs(os.path.dirname(moved)); os.rename(new, moved)
        self.said.clear()
        self.assertEqual(self._fresh_name(moved), "Game-Linux")
        self.assertEqual(S["read_origin"]("Game-Linux")[0], moved)

    def test_a_file_that_matches_several_gone_programs_is_not_guessed(self):
        old, new = self._renamed(); S = self.S
        os.unlink(f"{S['CONF']}/apps/Game-Linux.conf")                          # nobody set either up: no way to tell
        twin = self.write("Downloads/test/Twin", "the game " * 50); self.assertTrue(self.origin("Twin", twin)); os.unlink(twin)
        owner, was, why = S["file_owner"]("Game", new)
        self.assertEqual((owner, was), ("Game", None)); self.assertIn("Game-Linux, Twin", why)

    def test_runs_under_wrong_names_leave_records_and_the_set_up_program_wins(self):
        old, new = self._renamed(); S = self.S
        fp = S["fingerprint"](new)
        for n, p in (("Game", "Downloads/test/Game"), ("Game-Linu", "Downloads/Game-Linu"), ("Game-Linuxx", "Downloads/x")):
            S["write_origin"](n, f"{self.home}/{p}", fp)                                  # earlier runs by a version that took each file name for a program
        self._put(f"{S['HOMES']}/Game/save", "s" * 70000)                       # one of them even has a big home
        self.said.clear()
        self.assertEqual(S["file_owner"]("Game", new)[0], "Game-Linux", "the one with settings, not the name the file has")
        self.assertEqual(self._fresh_name(new), "Game-Linux")
        self.assertEqual(sorted(os.listdir(f"{S['CONF']}/origins")), ["Game-Linux"], "records of the same content nobody set up are dropped")
        self.assertTrue(os.path.isdir(f"{S['HOMES']}/Game"), "homes stay"); self.assertEqual(len(self.said), 1)
        self.assertIn("~/.sbx-homes/Game", self.said[0])
        self.said.clear(); self.assertEqual(self._fresh_name(new), "Game-Linux"); self.assertEqual(self.said, [])

    def test_open_with_a_renamed_file_uses_the_settings_of_the_old_name(self):
        old, new = self._renamed(); S = self.S
        calls = []
        S["subprocess"] = mock.Mock(run=lambda cmd, **kw: (calls.append(cmd), mock.Mock(returncode=0))[1], DEVNULL=-3, TimeoutExpired=Exception)
        S["sys"] = mock.Mock(argv=["/nonexistent/sbx"], executable=sys.executable, stderr=io.StringIO(), exit=sys.exit, stdin=io.StringIO())
        S["shutil"] = mock.Mock(which=lambda n: "/usr/bin/sbx")
        for conf, extra in ((True, False), (False, True)):
            if not conf: os.unlink(f"{S['CONF']}/apps/Game-Linux.conf")
            calls.clear()
            with mock.patch.object(os, "chdir"), self.assertRaises(SystemExit): S["cmd_open"]([new])
            flags = calls[0][2:-1]
            self.assertEqual("--input" in flags, extra, "a settings file under the old name means no extras")
            self.assertNotIn("--here", flags, "the program's folder is never shared unless folder=1 says so")

    def test_a_missing_file_failure_points_at_the_folder_setting(self):
        S = self.S
        for text in ("thread 'main' panicked at src/lang.rs:10:89:\ncalled `Result::unwrap()` on an `Err` value: Os { code: 2, kind: NotFound, message: \"No such file or directory\" }",
                     "FileNotFoundError: [Errno 2] No such file or directory: 'x'"):
            self.assertIn("folder=1", S["missing_file_hint"](text), text)
            self.assertIn("not Downloads", S["missing_file_hint"](text))
        self.assertEqual(S["missing_file_hint"]("segmentation fault"), "")

    def test_rebind_is_documented(self):
        with open(f"{HERE}/sbx.1") as f: man = f.read()
        with open(f"{HERE}/README.md") as f: readme = f.read()
        self.assertIn("sbx rebind", man); self.assertIn("sbx rebind", readme); self.assertIn("sbx rebind", self.S["USAGE"])
        self.assertIn("pending", man); self.assertIn("same file name", readme)
        self.assertIn("stored beside", self.S["USAGE"]); self.assertIn("beside its own file", man); self.assertIn("next to its own file", readme)
        for c in ("sbx.bash", "_sbx", "sbx.fish"):
            with open(f"{HERE}/completions/{c}") as f: self.assertIn("rebind", f.read(), c)

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
    def test_completions_know_every_option_and_command(self):
        S = self.S
        internal = {"--loader"}                       # set by sbx itself, not typed
        root = os.path.dirname(os.path.abspath(__file__))
        text = {}
        for f in ("sbx.bash", "_sbx", "sbx.fish"):
            with open(f"{root}/completions/{f}") as fh: text[f] = fh.read()
        opts = [f for f in list(S["FLAGS"]) + list(S["VALUE_FLAGS"]) if f not in internal]
        for f, t in text.items():
            looped = {w for l in t.splitlines() if l.startswith("for ") for w in l.split()[3:]}      # sbx.fish: for d in camera alsa ...
            for o in opts:
                name = o[2:]
                ok = (o in t or f"-l {name}" in t or name in looped
                      or (name.startswith("portal-") and name[7:] in looped))
                self.assertTrue(ok, f"{f} doesn't offer {o}")
            for c in ("preview", "show", "doctor", "clean", "reset", "link", "desktop", "open", "list", "stop"): self.assertIn(c, t, f"{f} lacks {c}")

    # -- running programs
    def running_file(self, pid, name, start="same"):
        os.makedirs(self.S["RUNNING"], exist_ok=True)
        with open(f"{self.S['RUNNING']}/{pid}", "w") as f:
            f.write(f"{name}\n" + (self.S["proc_start"](pid) if start == "same" else start))

    def test_a_running_file_counts_only_for_the_process_that_wrote_it(self):
        S = self.S; S["RUNNING"] = f"{self.tmp}/run"; S["live_sbx"] = lambda pid: True
        me = os.getpid()
        self.running_file(me, "app")
        self.assertEqual(S["running"]("app"), [str(me)]); self.assertEqual(S["running"](), [str(me)]); self.assertEqual(S["running"]("other"), [])
        self.running_file(me, "app", start="1")                       # the PID now belongs to another process
        self.assertEqual(S["running"]("app"), [])
        with open(f"{S['RUNNING']}/{me}", "w") as f: f.write("app\n")      # no start time: can't be checked
        self.assertEqual(S["running"]("app"), [])
        self.running_file(me, "app"); S["live_sbx"] = lambda pid: False      # not an sbx process
        self.assertEqual(S["running"]("app"), [])

    def test_mark_running_drops_stale_files_and_unmark_removes_ours(self):
        S = self.S; S["RUNNING"] = f"{self.tmp}/run"; S["live_sbx"] = lambda pid: True
        os.makedirs(S["RUNNING"])
        with open(f"{S['RUNNING']}/999999", "w") as fh: fh.write("gone\n123")
        open(f"{S['RUNNING']}/notes.txt", "w").close()
        S["mark_running"]("app")
        self.assertEqual(sorted(os.listdir(S["RUNNING"])), sorted(["notes.txt", str(os.getpid())]))
        self.assertEqual(S["running"]("app"), [str(os.getpid())])
        S["unmark_running"](); S["unmark_running"]()
        self.assertEqual(os.listdir(S["RUNNING"]), ["notes.txt"])

    def test_stop_signals_the_checked_process_and_leaves_a_reused_pid_alone(self):
        S = self.S; S["RUNNING"] = f"{self.tmp}/run"; S["live_sbx"] = lambda pid: True
        mine, other = (subprocess.Popen(["sleep", "60"]) for _ in range(2))
        self.addCleanup(lambda: [p.kill() or p.wait() for p in (mine, other)])
        self.running_file(mine.pid, "app")
        self.running_file(other.pid, "app", start="1")                # a newcomer that took a stale file's PID
        self.assertEqual(S["stop_program"]("app"), 1)
        self.assertEqual(mine.wait(5), -15)
        self.assertIsNone(other.poll())

    def test_clean_refuses_a_running_program_and_stop_needs_one(self):
        S = self.S; S["RUNNING"] = f"{self.tmp}/run"; S["live_sbx"] = lambda pid: True
        os.makedirs(f"{S['HOMES']}/busy"); os.makedirs(f"{S['HOMES']}/idle")
        self.running_file(os.getpid(), "busy")
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            S["cmd_clean"](["--yes", "busy"])
        self.assertIn("sbx stop busy", err.getvalue()); self.assertTrue(os.path.isdir(f"{S['HOMES']}/busy"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): S["cmd_stop"](["idle"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_list"](["--all"])
        self.assertEqual([l.split()[:3] for l in out.getvalue().splitlines()], [["busy", "running", "(1)"], ["idle", "idle", "(only"]])

    def _programs(self):
        """Programs of every kind: set up by a settings file, a link, a launcher; and two that only have a private home."""
        S, h = self.S, self.home
        S["running"] = lambda *a: []
        for n in ("conf", "linked", "launched", "seen", "big"): os.makedirs(f"{S['HOMES']}/{n}")
        self.write(".config/sbx/apps/conf.conf", "net=0\n")
        os.makedirs(f"{h}/.local/bin"); os.symlink("/usr/local/bin/sbx", f"{h}/.local/bin/linked")
        self.write(".local/share/applications/org.example.launched.desktop", "[Desktop Entry]\nX-Sbx-Sandboxed=true\nExec=/usr/local/bin/sbx launched %U\n")
        self.write(".sbx-homes/seen/.cache/x", "tiny")
        self.write(".sbx-homes/big/data", "x" * (S["UNUSED_BYTES"] + 1))
        self.write(".config/sbx/ids/seen", "id\n")

    def _list(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.S["cmd_list"](list(args))
        return out.getvalue()

    def test_list_leaves_out_programs_nobody_set_up_unless_asked(self):
        self._programs()
        out = self._list()
        self.assertEqual([l.split()[0] for l in out.splitlines()[:3]], ["conf", "launched", "linked"])
        self.assertNotIn("seen ", out); self.assertNotIn("big ", out)
        self.assertIn("2 more", out); self.assertIn("sbx list --all", out); self.assertIn("sbx clean --unused", out)
        every = self._list("--all")
        self.assertRegex(every, r"seen\s+idle\s+\(only run from the terminal\)"); self.assertRegex(every, r"big\s+idle\s+\(only run from the terminal\)")
        self.assertNotRegex(every, r"conf\s+idle\s+\(only")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): self._list("--bogus")

    def test_a_running_program_is_listed_even_when_nobody_set_it_up(self):
        self._programs()
        self.S["running"] = lambda *a: ["1"]; self.S["pid_app"] = lambda p: "seen"
        self.assertRegex(self._list(), r"seen\s+running \(1\)")

    def test_unused_means_nobody_set_it_up_and_almost_nothing_is_stored(self):
        self._programs()
        self.assertEqual(self.S["unused_programs"](), ["seen"])                          # `big` holds data, the others were set up
        self.assertTrue(self.S["home_is_small"]("seen")); self.assertFalse(self.S["home_is_small"]("big"))
        self.S["running"] = lambda *a: ["1"]; self.S["pid_app"] = lambda p: "seen"
        self.assertEqual(self.S["unused_programs"](), [], "a running program is never unused")

    def test_clean_unused_deletes_only_those_homes_and_asks_first(self):
        S, h = self.S, self.home
        self._programs()
        with mock.patch.object(sys, "stdin", io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            S["cmd_clean"](["--unused"])                                                                                        # no terminal: needs --yes
        self.assertIn("--yes", err.getvalue()); self.assertTrue(os.path.isdir(f"{S['HOMES']}/seen"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_clean"](["--yes", "--unused"])
        self.assertIn("removed 1 private home", out.getvalue())
        self.assertEqual(sorted(os.listdir(S["HOMES"])), ["big", "conf", "launched", "linked"])
        self.assertFalse(os.path.exists(f"{h}/.config/sbx/ids/seen"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_clean"](["--yes", "--unused"])
        self.assertIn("Nothing unused", out.getvalue())

    def test_reset_deletes_settings_files_and_nothing_else(self):
        S = self.S; conf = S["CONF"]; apps = f"{conf}/apps"
        os.makedirs(apps); os.makedirs(f"{S['HOMES']}/a")
        def make():
            for p in (f"{conf}/default.conf", f"{apps}/a.conf", f"{apps}/b.conf"):
                with open(p, "w") as f: f.write("net=0\n")
        def reset(*args):
            out = io.StringIO()
            with contextlib.redirect_stdout(out): S["cmd_reset"](list(args))
            return out.getvalue()
        make()
        reset("--yes", "a")                                                                      # one program: only its own file
        self.assertEqual(sorted(os.listdir(apps)), ["b.conf"]); self.assertTrue(os.path.exists(f"{conf}/default.conf"))
        self.assertTrue(os.path.isdir(f"{S['HOMES']}/a"), "private homes are data, not settings")
        reset("--yes", "--default")
        self.assertFalse(os.path.exists(f"{conf}/default.conf")); self.assertTrue(os.path.exists(f"{apps}/b.conf"))
        self.assertIn("Nothing to reset", reset("--yes", "--default"))                           # already the defaults: not an error
        make(); reset("--all", "--yes")
        self.assertEqual(os.listdir(apps), []); self.assertFalse(os.path.exists(f"{conf}/default.conf"))
        self.assertTrue(os.path.isdir(f"{S['HOMES']}/a"))
        make()
        for bad in (["../x"], ["--bogus"], [], ["a", "b"], ["--yes"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit, msg=bad): S["cmd_reset"](bad)
        with mock.patch.object(sys, "stdin", io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as c:
            S["cmd_reset"]([ "a"])                                                               # no terminal to ask on
        self.assertNotEqual(c.exception.code, 0); self.assertIn("--yes", err.getvalue()); self.assertTrue(os.path.exists(f"{apps}/a.conf"))
        tty = mock.Mock(); tty.isatty.return_value = True
        with mock.patch.object(sys, "stdin", tty), mock.patch("builtins.input", return_value="n"), contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as c:
            S["cmd_reset"](["--all"])
        self.assertEqual(c.exception.code, 0); self.assertTrue(os.path.exists(f"{apps}/a.conf"))
        self.assertIn("apps/a.conf", out.getvalue()); self.assertIn("apps/b.conf", out.getvalue()); self.assertIn("default.conf", out.getvalue())   # it says what goes
        with mock.patch.object(sys, "stdin", tty), mock.patch("builtins.input", return_value="y"), contextlib.redirect_stdout(io.StringIO()):
            S["cmd_reset"](["--all"])
        self.assertFalse(os.path.exists(f"{apps}/a.conf"))
        with mock.patch.dict(os.environ, {"SBX_ACTIVE": "1"}), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): S["cmd_reset"](["--yes", "--all"])

    def test_reset_is_documented(self):
        with open(f"{HERE}/sbx.1") as f: man = f.read()
        with open(f"{HERE}/README.md") as f: readme = f.read()
        self.assertIn("sbx reset", man); self.assertIn("sbx reset", readme); self.assertIn("sbx reset", self.S["USAGE"]); self.assertIn("reset", self.S["COMMANDS"])

    def test_link_makes_typing_the_name_start_the_program_through_sbx(self):
        S, h = self.S, self.home
        inst = f"{h}/inst"; bindir = f"{h}/bin"; os.makedirs(inst); os.makedirs(bindir)
        for p in (f"{inst}/sbx", f"{bindir}/foo", f"{bindir}/bar"):
            with open(p, "w") as f: f.write("#!/bin/sh\necho real\n")
            os.chmod(p, 0o755)
        mine = f"{h}/.local/bin"
        env = {"PATH": f"{mine}:{bindir}:/usr/bin:/bin", "SBX_ACTIVE": ""}
        def link(*a):
            return self._link_with_env(S, inst, env, a[-1]) if len(a) == 1 else self._run_link(S, inst, env, a)
        def fails(*a, **kw):
            with mock.patch.dict(os.environ, kw.get("env", env)), mock.patch.object(sys, "argv", [f"{inst}/sbx"]), contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit, msg=a): S["cmd_link"](list(a))
        os.environ.pop("SBX_ACTIVE", None)
        self.assertEqual(link("foo"), "")                                                       # our folder is first: no warning
        self.assertEqual(os.readlink(f"{mine}/foo"), f"{inst}/sbx"); self.assertTrue(S["is_sbx_link"](f"{mine}/foo"))
        link("foo")                                                                             # again: fine
        with mock.patch.dict(os.environ, env):
            self.assertEqual(S["find_in_path"]("foo"), f"{bindir}/foo", "the link is not the program")
            self.assertEqual(S["find_program"]("foo")[0], f"{bindir}/foo")                      # `sbx foo`, launchers and the GUI must not find the link
            self.assertEqual(S["linked_name"](f"{mine}/foo"), "foo"); self.assertIsNone(S["linked_name"](f"{inst}/sbx"))
        with open(f"{mine}/bar", "w") as f: f.write("mine\n")                                  # somebody else's file: never replaced
        fails("bar")
        with open(f"{mine}/bar") as f: self.assertEqual(f.read(), "mine\n")
        fails("nosuchprogram"); fails("list"); fails("sbx-gui"); fails(".x"); fails("a/b"); fails(); fails("-x")
        self.assertIn("not before", self._link_with_env(S, inst, {"PATH": f"{bindir}:{mine}:/usr/bin:/bin"}, "foo"))   # the real one comes first: say so
        self.assertIn("not in your PATH", self._link_with_env(S, inst, {"PATH": f"{bindir}:/usr/bin:/bin"}, "foo"))
        fails("--remove", "bar"); self.assertTrue(os.path.exists(f"{mine}/bar"))                  # not ours
        link("--remove", "foo"); self.assertFalse(os.path.lexists(f"{mine}/foo"))
        fails("--remove", "foo")
        with mock.patch.dict(os.environ, {"SBX_ACTIVE": "1"}): fails("foo", env={**env, "SBX_ACTIVE": "1"})

    def _run_link(self, S, inst, env, a):
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", [f"{inst}/sbx"]), contextlib.redirect_stdout(io.StringIO()):
            S["cmd_link"](list(a))
        return ""

    def _link_with_env(self, S, inst, env, name):
        del self.warned[:]
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", [f"{inst}/sbx"]), contextlib.redirect_stdout(io.StringIO()):
            S["cmd_link"]([name])
        return " ".join(self.warned)

    def test_a_linked_start_runs_the_real_program_with_its_own_arguments(self):
        S, h = self.S, self.home
        inst = f"{h}/inst"; bindir = f"{h}/bin"; mine = f"{h}/.local/bin"
        for d in (inst, bindir, mine): os.makedirs(d)
        for p in (f"{inst}/sbx", f"{bindir}/foo"):
            with open(p, "w") as f: f.write("#!/bin/sh\n")
            os.chmod(p, 0o755)
        os.symlink(f"{inst}/sbx", f"{mine}/foo")
        env = {"PATH": f"{mine}:{bindir}:/usr/bin"}
        calls = []
        S["run"] = lambda prog, args, opts: calls.append((prog, args, opts))
        with mock.patch.dict(os.environ, env):
            os.environ.pop("SBX_ACTIVE", None)
            S["run_linked"](f"{mine}/foo", ["--incognito", "--net=none", "x"])
            self.assertEqual(calls, [("foo", ["--incognito", "--net=none", "x"], {})], "program flags are not sbx options")
            with mock.patch("os.execv") as execv, mock.patch.dict(os.environ, {"SBX_ACTIVE": "1"}):
                S["run_linked"](f"{mine}/foo", ["a"])
            execv.assert_called_once_with(f"{bindir}/foo", ["foo", "a"]); self.assertEqual(len(calls), 1, "already sandboxed: not a second sandbox")
            os.unlink(f"{bindir}/foo")
            with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit): S["run_linked"](f"{mine}/foo", [])
            self.assertIn("sbx link --remove foo", err.getvalue())
        with open(f"{HERE}/sbx.1") as f: man = f.read()
        with open(f"{HERE}/README.md") as f: readme = f.read()
        self.assertIn("sbx link", man); self.assertIn("sbx link", readme); self.assertIn("sbx link", S["USAGE"])

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
        names = {f"{dead}.bus": False, f"{dead}.xauth": False, f"{dead}.wl": False, f"{dead}.boot_id": False, f"{dead}.hostname": False, f"{dead}.hosts": False, f"{dead}.resolv.conf": False, f"{dead}.seccomp": False, f"{os.getpid()}.wl": True, "notes.txt": True, f"{dead}.other": True}
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
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{h}/.config"}):                      # CI sets its own XDG_CONFIG_HOME
            self.assertEqual(of(f"{h}/Games/Crash/game"), f"{h}/Games/Crash")
            self.assertEqual(of(f"{h}/Downloads/Crash/game"), f"{h}/Downloads/Crash", "a subfolder of Downloads is the program's own")
            for bad in (f"{h}/game", f"{h}/Downloads/game", f"{h}/Desktop/game", f"{h}/Transferências/game", f"{h}/Área de Trabalho/game",
                        f"{h}/.local/bin/game", f"{h}/.cache/x/game", "/usr/bin/game", "/tmp/game", "/opt/x/game"):
                self.assertIsNone(of(bad), bad)
            self.assertEqual(of("/tmp/x/game"), "/tmp/x")

    def start_in(self, rel, opts, conf=None):
        """build() live for an executable at HOME/REL; returns (the --chdir folder, the bound paths, what was warned)."""
        S, h = self.S, self.home
        prog = self.write(rel, "#!/bin/sh\n"); os.chmod(prog, 0o755)
        if conf: self.write(f".config/sbx/apps/{os.path.basename(rel)}.conf", conf)
        os.makedirs(f"{self.tmp}/run", exist_ok=True)
        S["runtime_dir"] = lambda: f"{self.tmp}/run"
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{h}/.config", "WAYLAND_DISPLAY": "", "DISPLAY": ""}):
            _, cmd, st = S["build"](prog, [], True, opts)
        for f in st["keep"]: f.close()
        for fd in st["fds"]:
            try: os.close(fd)
            except OSError: pass
        binds = [cmd[i + 1] for i in range(len(cmd) - 2) if cmd[i] == "--bind"]
        return cmd[cmd.index("--chdir") + 1], binds, self.warned

    def test_folder_starts_the_program_in_its_own_folder_and_shares_it(self):
        h = self.home
        self.assertEqual(self.start_in("Games/Crash/game", {})[0], h, "off by default: the private home")
        cwd, binds, warned = self.start_in("Games/Crash2/game", {"folder": True})
        self.assertEqual(cwd, f"{h}/Games/Crash2"); self.assertIn(f"{h}/Games/Crash2", binds)
        self.assertFalse([w for w in warned if "folder=1" in w], warned)

    def test_folder_from_the_settings_file_survives_other_settings(self):
        h = self.home
        cwd, binds, _ = self.start_in("Games/Crash3/game", {}, "net=0\nfolder=1\nsound=0\n")
        self.assertEqual(cwd, f"{h}/Games/Crash3"); self.assertIn(f"{h}/Games/Crash3", binds)

    def test_folder_refuses_downloads_and_the_home_folder_and_says_why(self):
        h = self.home
        for rel in ("game", "Downloads/game", "Desktop/game", ".local/bin/game"):
            self.warned.clear()
            cwd, binds, warned = self.start_in(rel, {"folder": True})
            self.assertEqual(cwd, h, rel)
            self.assertNotIn(os.path.dirname(f"{h}/{rel}"), binds, rel)
            self.assertTrue([w for w in warned if "folder=1" in w and "folder of its own" in w], (rel, warned))

    def test_a_folder_named_after_the_program_is_shared_by_default(self):
        h = self.home
        for folder in ("G-Diffuser", "G-Diffuser-v1.2.3-linux-x64", "gdiffuser_1.2"):
            self.warned.clear()
            cwd, binds, warned = self.start_in(f"Games/{folder}/G-Diffuser", {})
            self.assertEqual(cwd, f"{h}/Games/{folder}", folder); self.assertIn(f"{h}/Games/{folder}", binds)
            self.assertTrue([w for w in warned if "folder=0" in w], (folder, warned))

    def test_a_folder_not_named_after_the_program_is_not_shared_by_default(self):
        h = self.home
        cwd, binds, warned = self.start_in("Games/Stuff/G-Diffuser", {})
        self.assertEqual(cwd, h); self.assertNotIn(f"{h}/Games/Stuff", binds)
        self.assertFalse([w for w in warned if "folder=0" in w], warned)

    def test_the_named_folder_default_never_reaches_downloads_or_home(self):
        h = self.home
        for rel in ("G-Diffuser", "Downloads/G-Diffuser"):
            cwd, binds, _ = self.start_in(rel, {})
            self.assertEqual(cwd, h, rel)

    def test_folder_0_turns_the_named_folder_default_off(self):
        h = self.home
        cwd, binds, warned = self.start_in("Games/G-Diffuser/G-Diffuser", {}, "folder=0\n")
        self.assertEqual(cwd, h); self.assertNotIn(f"{h}/Games/G-Diffuser", binds)
        self.assertFalse([w for w in warned if "folder=0" in w], warned)

    def test_folder_named_like(self):
        f = self.S["folder_named_like"]
        for n, d, ok in (("G-Diffuser", "/x/G-Diffuser-v1.2.3-linux-x64", True), ("G-Diffuser", "/x/gdiffuser", True),
                         ("G-Diffuser", "/x/Stuff", False), ("", "/x/Stuff", False), ("game", "/x/linux-x64-game", False)):
            self.assertEqual(f(n, d), ok, (n, d))

    def test_the_here_flag_still_wins_over_folder(self):
        h = self.home
        os.makedirs(f"{h}/Work/proj")
        self.S["share_dir"] = lambda d: f"{h}/Work/proj"
        cwd, binds, _ = self.start_in("Games/Crash4/game", {"folder": True, "here": True})
        self.assertEqual(cwd, f"{h}/Work/proj")

    def test_own_folder_gives_the_reason(self):
        S, h = self.S, self.home
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{h}/.config"}):
            self.assertEqual(S["own_folder"](f"{h}/Games/x/game"), (f"{h}/Games/x", None))
            for bad, word in ((f"{h}/Downloads/game", "Downloads"), (f"{h}/.cache/x/game", "hidden"), ("/usr/bin/game", "not inside")):
                d, why = S["own_folder"](bad)
                self.assertIsNone(d); self.assertIn(word, why)

    def test_man_page_documents_the_desktop_options(self):
        with open(f"{HERE}/sbx.1") as f: man = f.read().replace("\\-", "-")
        for flag in ("--install-opener", "--remove-opener", "--install-menu", "--remove-menu", "--remove"): self.assertIn(flag, man)

    def test_desktop_remove_finds_the_copy_kept_under_the_system_launcher_name(self):
        S, h = self.S, self.home
        sysdir = f"{h}/sysshare"; os.makedirs(f"{sysdir}/applications")
        with open(f"{sysdir}/applications/org.mozilla.firefox.desktop", "w") as f: f.write("[Desktop Entry]\nName=Firefox\nExec=firefox %u\n")
        dest = f"{h}/.local/share/applications/org.mozilla.firefox.desktop"
        with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": sysdir}), contextlib.redirect_stdout(io.StringIO()): S["cmd_desktop"](["firefox"])
        self.assertTrue(os.path.isfile(dest))
        with contextlib.redirect_stdout(io.StringIO()): S["cmd_desktop"](["--remove", "firefox"])
        self.assertFalse(os.path.exists(dest))
        self.write(".local/share/applications/org.other.firefox.desktop", "[Desktop Entry]\nName=Mine\n")      # not made by sbx: left alone
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()): S["cmd_desktop"](["--remove", "firefox"])
        self.assertTrue(os.path.exists(f"{h}/.local/share/applications/org.other.firefox.desktop"))

    def test_menu_commands(self):
        S, h = self.S, self.home
        desk = f"{h}/.local/share/applications/org.sbx.Gui.desktop"; icon = f"{h}/.local/share/icons/hicolor/scalable/apps/org.sbx.Gui.svg"
        out = io.StringIO()
        with contextlib.redirect_stdout(out): S["cmd_menu"](False)
        self.assertIn("nothing to remove", out.getvalue())
        self.write(".local/share/applications/org.sbx.Gui.desktop", "x"); self.write(".local/share/icons/hicolor/scalable/apps/org.sbx.Gui.svg", "x")
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}), contextlib.redirect_stdout(out): S["cmd_desktop"](["--remove-menu"])
        self.assertFalse(os.path.exists(desk) or os.path.exists(icon))
        def fake(uid, mode):                                                                                 # which sbx-gui counts: root-owned and not writable by others
            real_stat = os.stat
            def stat(p, *a, **k):
                return os.stat_result((mode, 0, 0, 1, uid, uid, 0, 0, 0, 0)) if str(p).endswith("/sbx-gui") else real_stat(p, *a, **k)
            return stat
        for uid, mode in ((1000, 0o100755), (0, 0o100775), (0, 0o100757)):
            with mock.patch("os.stat", fake(uid, mode)), mock.patch.object(S["subprocess"], "run") as run, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                S["cmd_menu"](True)
            run.assert_not_called()
        with mock.patch("os.stat", fake(0, 0o100755)), mock.patch.object(S["subprocess"], "run", return_value=mock.Mock(returncode=0)) as run, \
             self.assertRaises(SystemExit) as r:
            S["cmd_menu"](True)
        self.assertEqual(r.exception.code, 0)
        self.assertEqual(run.call_args[0][0], ["/usr/local/bin/sbx-gui", "--install-menu"])

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
        os.makedirs(cfg)
        with open(f"{cfg}/mimeapps.list", "w") as fh: fh.write("[Default Applications]\ntext/plain=other.desktop;\n")
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out): S["cmd_opener"](True)
        dest = f"{h}/.local/share/applications/sbx-run.desktop"
        with open(dest) as f: text = f.read()
        for must in ("Name=Run in sbx", "Exec=", " open %f", "application/x-executable;application/x-pie-executable;", "Terminal=false", "X-Sbx-Opener=true"):
            self.assertIn(must, text)
        self.assertIn("application/x-shellscript;", text)                                                   # offered under Open With...
        self.assertIn("NoDisplay=true\n", text)                                                             # ...but not shown in the app menu
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

    def test_exec_lines_name_the_program_they_start(self):
        ep = self.S["exec_program"]
        self.assertEqual(ep("/usr/bin/google-chrome-stable %U"), "google-chrome-stable")
        self.assertEqual(ep("firefox --new-window %u"), "firefox")
        self.assertEqual(ep("/usr/local/bin/sbx /usr/bin/x %U"), "x")                  # a launcher sbx already rewrote
        self.assertEqual(ep('"/opt/my tools/sbx" app %f'), "app")
        self.assertEqual(ep("env FOO=1 app"), "env")                                    # not the program: rewrite_desktop warns about these
        self.assertIsNone(ep("")); self.assertIsNone(ep('app "unterminated'))

    def _launcher_share(self, *files):
        share = f"{self.home}/sysshare"; os.makedirs(f"{share}/applications", exist_ok=True)
        for name, text in files:
            with open(f"{share}/applications/{name}", "w") as f: f.write(text)
        return share

    def test_desktop_finds_a_launcher_by_the_program_it_starts_and_removes_it_the_same_way(self):
        S, h = self.S, self.home
        share = self._launcher_share(("google-chrome.desktop", "[Desktop Entry]\nName=Chrome\nExec=/usr/bin/google-chrome-stable %U\n\n[Desktop Action x]\nExec=/usr/bin/other\n"),
                                     ("unrelated.desktop", "[Desktop Entry]\nName=Other\nExec=other\n"))
        dest = f"{h}/.local/share/applications/google-chrome.desktop"
        with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": share}), mock.patch.object(sys, "argv", ["/usr/local/bin/sbx"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S["system_launchers"]("google-chrome-stable"), [f"{share}/applications/google-chrome.desktop"])
            self.assertEqual(S["system_launchers"]("google-chrome"), [f"{share}/applications/google-chrome.desktop"])      # by id, as before
            S["cmd_desktop"](["google-chrome-stable"])
            self.assertEqual(S["launcher_program"](dest), "google-chrome-stable")                                         # looks past the sbx we added
            self.assertEqual(S["our_launchers"]("google-chrome-stable"), [dest]); self.assertEqual(S["our_launchers"]("google-chrome"), [dest])
            self.assertEqual(S["our_launchers"]("other"), [], "an action's Exec= is not the launcher's program")
            S["cmd_desktop"](["--remove", "google-chrome-stable"])
        self.assertFalse(os.path.exists(dest))
        with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": share}), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): S["cmd_desktop"](["nosuchprogram"])

    def test_the_launcher_with_that_id_wins_over_one_that_merely_starts_the_program(self):
        share = self._launcher_share(("foo.desktop", "[Desktop Entry]\nExec=foo-real\n"), ("other.desktop", "[Desktop Entry]\nExec=foo\n"))
        with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": share}):
            self.assertEqual(self.S["system_launchers"]("foo"), [f"{share}/applications/foo.desktop"])
            self.assertEqual(self.S["launchers_starting"]("foo"), [f"{share}/applications/other.desktop"])

    def test_desktop_never_replaces_a_launcher_sbx_did_not_make(self):
        S = self.S
        share = self._launcher_share(("app.desktop", "[Desktop Entry]\nName=App\nExec=app\n"))
        mine = self.write(".local/share/applications/app.desktop", "[Desktop Entry]\nName=My own\nExec=app --my-flag\n")
        with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": share}), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit): S["cmd_desktop"](["app"])
        with open(mine) as f: self.assertIn("My own", f.read())
        self.assertTrue(any("not touching it" in w for w in self.warned))

    def _linkable(self, *launchers):
        """A fake installed sbx and program 'chrome-bin' in front of the system's, with system launchers made from LAUNCHERS."""
        h = self.home; inst = f"{h}/inst"; bindir = f"{h}/bin"; os.makedirs(inst); os.makedirs(bindir)
        for p in (f"{inst}/sbx", f"{bindir}/chrome-bin"):
            with open(p, "w") as f: f.write("#!/bin/sh\necho real\n")
            os.chmod(p, 0o755)
        share = self._launcher_share(*[(n, f"[Desktop Entry]\nName=Chrome\nExec={bindir}/chrome-bin %U\n") for n in launchers])
        env = {"PATH": f"{h}/.local/bin:{bindir}:/usr/bin:/bin", "SBX_ACTIVE": "", "XDG_DATA_DIRS": share}
        def run(*a):
            out = io.StringIO()
            with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", [f"{inst}/sbx"]), contextlib.redirect_stdout(out): self.S["cmd_link"](list(a))
            return out.getvalue()
        return run

    def test_link_says_when_an_app_menu_launcher_still_skips_sbx(self):
        run = self._linkable("google-chrome.desktop")
        out = run("chrome-bin")
        self.assertIn("sbx desktop google-chrome", out)
        self.assertFalse(os.path.exists(f"{self.home}/.local/share/applications/google-chrome.desktop"), "without --desktop nothing is written")
        with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": f"{self.home}/sysshare"}), mock.patch.object(sys, "argv", [f"{self.home}/inst/sbx"]), \
             contextlib.redirect_stdout(io.StringIO()): self.S["cmd_desktop"](["chrome-bin"])
        self.assertNotIn("sbx desktop", run("chrome-bin"), "already covered: no hint")
        self.assertIn("--remove chrome-bin --desktop", run("--remove", "chrome-bin"), "removing only the link says the launcher is still there")

    def test_link_with_two_launchers_for_one_program_points_at_the_flag(self):
        run = self._linkable("a.desktop", "b.desktop")
        out = run("chrome-bin")
        self.assertIn("a, b", out); self.assertIn("sbx link chrome-bin --desktop", out)

    def test_link_desktop_covers_the_launchers_and_remove_desktop_undoes_both(self):
        h = self.home
        run = self._linkable("a.desktop", "b.desktop")
        apps = f"{h}/.local/share/applications"
        run("chrome-bin", "--desktop")
        self.assertTrue(os.path.islink(f"{h}/.local/bin/chrome-bin"))
        self.assertEqual(sorted(os.listdir(apps)), ["a.desktop", "b.desktop"])
        for n in ("a.desktop", "b.desktop"): self.assertEqual(self.S["launcher_program"](f"{apps}/{n}"), "chrome-bin")
        run("--remove", "chrome-bin", "--desktop")
        self.assertFalse(os.path.lexists(f"{h}/.local/bin/chrome-bin")); self.assertEqual(os.listdir(apps), [])
        run("chrome-bin", "--desktop"); os.unlink(f"{h}/.local/bin/chrome-bin")          # the link is gone, the launchers are not
        run("--remove", "chrome-bin", "--desktop"); self.assertEqual(os.listdir(apps), [])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): run("--remove", "chrome-bin", "--desktop")      # nothing left of either
        self.assertIn("no app-menu launcher starts 'nothing'", self._linkable_none())

    def _linkable_none(self):
        h = self.home; out = io.StringIO()
        with open(f"{h}/bin/nothing", "w") as f: f.write("#!/bin/sh\n")
        os.chmod(f"{h}/bin/nothing", 0o755)
        env = {"PATH": f"{h}/.local/bin:{h}/bin:/usr/bin:/bin", "SBX_ACTIVE": "", "XDG_DATA_DIRS": f"{h}/sysshare"}
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", [f"{h}/inst/sbx"]), contextlib.redirect_stdout(out): self.S["cmd_link"](["nothing", "--desktop"])
        return out.getvalue()

    def test_completions_offer_link_desktop(self):
        for f, want in (("sbx.bash", "--remove --desktop"), ("_sbx", "--remove --desktop"), ("sbx.fish", "-l desktop")):
            with open(f"{HERE}/completions/{f}") as fh: self.assertIn(want, fh.read(), f)

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

    def test_a_long_running_handle_survives_another_process_trimming_the_log(self):
        S = self.S
        held = S["open_log"]()
        S["log_write"](held, "early line\n")
        with open(S["LOG"], "a") as f: f.write(("y" * 99 + "\n") * (S["LOG_MAX"] // 100 + 10))
        S["log"]("another sbx trims", "other")
        S["log_write"](held, "late line from the long-running program\n"); held.close()
        out = self.lines()
        self.assertIn("late line from the long-running program", out[-1]); self.assertLessEqual(len(out), S["LOG_KEEP"] + 2)
        self.assertEqual(os.stat(S["LOG"]).st_mode & 0o777, 0o600)

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

    def test_failure_reason_skips_progress_meters_and_sbx_notices(self):
        S = self.S
        text = ("Uncompressing Lucas\nsbx: the compositor has no security-context support\n     0%     1%    99%  100%   MD5 checksums are OK.\r"
                "     0%     1%  100%  ./setup_linux.sh: line 21:  1116 Aborted (core dumped) ./setup_linux 2> /dev/null\n")
        why = S["failure_reason"](text, 134)
        self.assertEqual(why, "Uncompressing Lucas MD5 checksums are OK. ./setup_linux.sh: line 21:  1116 Aborted (core dumped) ./setup_linux 2> /dev/null")
        self.assertEqual(S["failure_reason"]("sbx: bubblewrap missing\n", 1), "sbx: bubblewrap missing")      # nothing else: keep it
        self.assertEqual(S["failure_reason"]("", 3), "exit code 3")

    def test_failure_notification_carries_the_reason(self):
        S = self.S
        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/notify-send" if x == "notify-send" else None), \
             mock.patch("subprocess.run", return_value=types.SimpleNamespace(stdout="no actions")) as run:
            S["notify_failure"]("app", "it aborted")
        self.assertTrue(any("app stopped right after starting. it aborted" in x for x in run.call_args_list[-1].args[0]))

    def test_a_crash_is_told_by_its_signal_whenever_it_happens(self):
        S = self.S
        cs = S["crash_signal"]
        self.assertEqual(cs(139), "SIGSEGV"); self.assertEqual(cs(134), "SIGABRT"); self.assertEqual(cs(-11), "SIGSEGV")      # bwrap's 128+n, or a direct signal
        for rc in (0, 1, 2, 128, 130, 143, -15, 255, 300): self.assertIsNone(cs(rc))                                          # exits, Ctrl-C, SIGTERM: not crashes
        sent = []
        for call in (lambda: S["notify_crash"]("chrome", "SIGSEGV"), lambda: S["notify_ended"]("chrome", 1)):
            with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/notify-send" if x == "notify-send" else None), \
                 mock.patch("subprocess.run", return_value=types.SimpleNamespace(stdout="no actions")) as run:
                call()
            sent.append(run.call_args_list[-1].args[0])
        crash, ended = sent
        self.assertIn("critical", crash); self.assertTrue(any("chrome crashed (segmentation fault, SIGSEGV)" in x for x in crash))
        self.assertIn("normal", ended); self.assertTrue(any("chrome stopped with an error (exit code 1)" in x for x in ended))

    def test_failure_notification_without_actions_is_a_plain_popup(self):
        S = self.S
        with mock.patch("shutil.which", return_value=None), mock.patch("subprocess.run") as run:
            S["notify_failure"]("app"); run.assert_not_called()                        # no notify-send: nothing happens
        with mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/notify-send" if x == "notify-send" else None), \
             mock.patch("subprocess.run", return_value=types.SimpleNamespace(stdout="no actions")) as run:
            S["notify_failure"]("app")
        sent = run.call_args_list[-1].args[0]
        self.assertIn("critical", sent); self.assertTrue(any("app stopped" in x for x in sent))


class JournalTag(unittest.TestCase):
    def reconnected(self, set_env):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/journal"
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(path); srv.listen(2); srv.settimeout(10)
            # The fake launcher's stream is a connection to the same kind of socket, so the test can tell it is "the journal"
            code = (f"import os, socket, sys\nns = {{'__name__': 'x'}}\nexec(compile(open({HERE + '/sbx'!r}).read(), 'sbx', 'exec'), ns)\n"
                    f"c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); c.connect({path!r})\n"
                    "os.dup2(c.fileno(), 1); os.dup2(c.fileno(), 2)\n"
                    "st = os.fstat(1)\n" + ("os.environ['JOURNAL_STREAM'] = f'{st.st_dev}:{st.st_ino}'\n" if set_env else "os.environ.pop('JOURNAL_STREAM', None)\n") +
                    f"ns['journal_tag']('myapp', {path!r})\nsys.stdout.write('hello\\n'); sys.stdout.flush()\n"
                    "sys.stderr.write('oops\\n'); sys.stderr.flush()\n")
            p = subprocess.Popen([sys.executable, "-c", code])
            old, _ = srv.accept()                         # the launcher's own connection: it stays quiet
            new, _ = srv.accept(); new.settimeout(10)     # the one journal_tag opens
            data = b""
            while True:
                chunk = new.recv(4096)
                if not chunk: break
                data += chunk
            p.wait(timeout=10); old.close(); new.close(); srv.close()
        return data

    def test_journal_streams_are_reconnected_with_the_program_name(self):
        self.assertEqual(self.reconnected(True), b"myapp\n\n6\n0\n0\n0\n0\nhello\noops\n")

    def test_journal_streams_are_found_without_JOURNAL_STREAM(self):
        self.assertEqual(self.reconnected(False), b"myapp\n\n6\n0\n0\n0\n0\nhello\noops\n")

    def test_nothing_changes_when_not_started_from_the_journal(self):
        S = load_sbx()
        with mock.patch.dict(os.environ, clear=False):
            os.environ.pop("JOURNAL_STREAM", None)
            S["journal_tag"]("myapp", "/nonexistent/journal")      # no stream to match: must not even try to connect


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

    def test_reset_deletes_the_settings_file_instead_of_emptying_it(self):
        G = self.G; os.makedirs(G["APPS"], exist_ok=True)
        for p in (f"{G['CONF']}/default.conf", f"{G['APPS']}/a.conf", f"{G['APPS']}/b.conf"):
            with open(p, "w") as f: f.write("net=0\n")
        win = mock.MagicMock()
        G["Win"].reset(win, "a")
        self.assertFalse(os.path.exists(f"{G['APPS']}/a.conf"), "an empty file would still switch off the defaults of `sbx open`")
        self.assertTrue(os.path.exists(f"{G['APPS']}/b.conf") and os.path.exists(f"{G['CONF']}/default.conf"))
        win.nav.replace.assert_called_once(); G["Adw"].Toast.assert_called_with(title="Settings reset to the defaults")
        G["Win"].reset(win, "a"); G["Adw"].Toast.assert_called_with(title="Already using the defaults")
        self.assertEqual(len(G["settings_files"]()), 2)
        win.ask.reset_mock(); win.toasts.add_toast.reset_mock()
        G["Win"].reset_all(win)                                                          # asks first, with the number of files
        self.assertIn("(2)", win.ask.call_args[0][1]); self.assertTrue(os.path.exists(f"{G['APPS']}/b.conf"))
        G["Win"].do_reset_all(win)
        self.assertEqual(G["settings_files"](), []); win.fill_apps.assert_called_once(); G["Adw"].Toast.assert_called_with(title="Reset 2 settings files")
        win.ask.reset_mock(); G["Win"].reset_all(win)
        win.ask.assert_not_called(); G["Adw"].Toast.assert_called_with(title="Nothing to reset")

    def test_link_is_ours_only_for_links_to_sbx(self):
        G = self.G; d = f"{G['HOME']}/.local/bin"; os.makedirs(d)
        os.symlink("/usr/local/bin/sbx", f"{d}/mine"); os.symlink("/usr/bin/true", f"{d}/other")
        with open(f"{d}/plain", "w") as f: f.write("x")
        self.assertTrue(G["link_is_ours"]("mine"))
        for n in ("other", "plain", "missing"): self.assertFalse(G["link_is_ours"](n), n)

    def test_launcher_is_ours_by_id_or_by_the_program_it_starts(self):
        G = self.G; d = f"{G['HOME']}/.local/share/applications"; os.makedirs(d)
        def put(name, text):
            with open(f"{d}/{name}", "w") as f: f.write(text)
        put("google-chrome.desktop", "[Desktop Entry]\nX-Sbx-Sandboxed=true\nName=Chrome (sbx)\nExec=/usr/local/bin/sbx /usr/bin/google-chrome-stable %U\n")
        put("org.mozilla.firefox.desktop", "[Desktop Entry]\nX-Sbx-Sandboxed=true\nExec=\"/opt/my tools/sbx\" firefox %u\n")
        put("mine.desktop", "[Desktop Entry]\nExec=/usr/bin/mine\n")                      # not made by sbx
        for n in ("google-chrome-stable", "google-chrome", "firefox", "org.mozilla.firefox"): self.assertTrue(G["launcher_is_ours"](n), n)
        for n in ("chrome", "mine", "missing", "fire"): self.assertFalse(G["launcher_is_ours"](n), n)

    def test_removing_a_program_takes_it_off_the_list_and_trashes_only_its_home(self):
        G = self.G; os.makedirs(G["APPS"], exist_ok=True)
        for n in ("a", "b"):
            with open(f"{G['APPS']}/{n}.conf", "w") as f: f.write("net=0\n")
            os.makedirs(f"{G['HOMES']}/{n}")
            for d in ("ids", "origins", "pending"):
                os.makedirs(f"{G['CONF']}/{d}", exist_ok=True)
                with open(f"{G['CONF']}/{d}/{n}", "w") as f: f.write("x\n")
        trashed = []
        G["Gio"].File.new_for_path.return_value.trash.side_effect = lambda *_: trashed.append(1) or shutil.rmtree(f"{G['HOMES']}/a")
        G["program_running"] = lambda n: n == "b"
        win = mock.MagicMock()
        G["Win"].remove_program(win, "b")                                                  # running: nothing is touched
        self.assertEqual(G["app_names"](), ["a", "b"]); win.alert.assert_called_once(); self.assertEqual(trashed, [])
        G["Win"].remove_program(win, "a")
        self.assertEqual(G["app_names"](), ["b"]); self.assertEqual(trashed, [1])
        for d in ("ids", "origins", "pending"):
            self.assertFalse(os.path.exists(f"{G['CONF']}/{d}/a")); self.assertTrue(os.path.exists(f"{G['CONF']}/{d}/b"))
        win.nav.replace.assert_called_once(); win.fill_apps.assert_called_once(); G["Adw"].Toast.assert_called_with(title="Removed a from the list")

    def test_a_program_stays_when_its_home_cannot_go_to_the_trash(self):
        G = self.G; os.makedirs(G["APPS"], exist_ok=True); os.makedirs(f"{G['HOMES']}/a")
        with open(f"{G['APPS']}/a.conf", "w") as f: f.write("net=0\n")
        class Failed(Exception): message = "no trash here"
        G["GLib"].Error = Failed
        G["Gio"].File.new_for_path.return_value.trash.side_effect = Failed()
        G["program_running"] = lambda n: False
        win = mock.MagicMock()
        G["Win"].remove_program(win, "a")
        self.assertTrue(os.path.exists(f"{G['APPS']}/a.conf")); self.assertTrue(os.path.isdir(f"{G['HOMES']}/a"))
        self.assertIn("no trash here", win.alert.call_args[0][1]); win.nav.replace.assert_not_called()

    def _gui_programs(self):
        G = self.G; h = G["HOME"]; os.makedirs(G["APPS"], exist_ok=True)
        for n in ("a", "b", "c", "d", "e"): os.makedirs(f"{G['HOMES']}/{n}")
        with open(f"{G['APPS']}/a.conf", "w") as f: f.write("net=0\n")
        os.makedirs(f"{h}/.local/bin"); os.symlink("/usr/local/bin/sbx", f"{h}/.local/bin/b")
        os.makedirs(f"{h}/.local/share/applications")
        with open(f"{h}/.local/share/applications/org.example.c.desktop", "w") as f: f.write("[Desktop Entry]\nX-Sbx-Sandboxed=true\nExec=sbx c\n")
        with open(f"{G['HOMES']}/e/data", "w") as f: f.write("x" * (G["UNUSED_BYTES"] + 1))
        G["program_running"] = lambda n: False

    def test_the_list_separates_programs_you_set_up_from_terminal_only_ones(self):
        self._gui_programs(); G = self.G
        self.assertEqual(G["program_groups"](), (["a", "b", "c"], ["d", "e"]))
        self.assertEqual(G["unused_programs"](["d", "e"]), ["d"])
        G["program_running"] = lambda n: n == "d"
        self.assertEqual(G["program_groups"](), (["a", "b", "c", "d"], ["e"]))

    def test_removing_the_unused_ones_trashes_only_those_homes(self):
        self._gui_programs(); G = self.G
        def new_for_path(p):
            m = mock.MagicMock(); m.trash.side_effect = lambda *_: shutil.rmtree(p); return m
        G["Gio"].File.new_for_path.side_effect = new_for_path
        win = mock.MagicMock()
        G["Win"].remove_unused(win, ["d"])
        self.assertEqual(G["app_names"](), ["a", "b", "c", "e"])
        win.fill_apps.assert_called(); G["Adw"].Toast.assert_called_with(title="Removed 1 unused program")
        G["Win"].ask_remove_unused(win)                                                  # only `e` is left, and it holds data
        win.ask.assert_not_called(); G["Adw"].Toast.assert_called_with(title="Nothing unused")

    # -- where a program comes from, and the decisions waiting for you
    def _gui_origin(self, name, path, moved=None, content="x"):
        """What sbx writes for NAME (through sbx's own write_origin, so a change of format breaks these tests)."""
        G, S = self.G, self.S
        S.update(HOME=G["HOME"], CONF=G["CONF"], HOMES=G["HOMES"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path) and content is not None:
            with open(path, "w") as f: f.write(content)
        S["write_origin"](name, path, S["fingerprint"](path), moved)

    def _gui_pending(self, name, old, new, rule):
        G, S = self.G, self.S
        S.update(HOME=G["HOME"], CONF=G["CONF"], HOMES=G["HOMES"])
        S["write_pending"](name, old, new, rule, "why")

    def _gui_waiting(self):
        """`game` waits (rule c: the old file is gone), `ff` waits (rule d: the original is still there)."""
        h = self.G["HOME"]
        old = f"{h}/Downloads/Crash"; self._gui_origin("game", old); os.unlink(old)
        new = self._gui_new("game", f"{h}/Games/CrashDeluxe"); self._gui_pending("game", old, new, "c")
        orig = f"{h}/apps/ff"; self._gui_origin("ff", orig)
        evil = self._gui_new("ff", f"{h}/Downloads/ff"); self._gui_pending("ff", orig, evil, "d")
        return old, new, orig, evil

    def _gui_new(self, name, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f: f.write("other " + name)
        return path

    def test_the_window_reads_origins_and_pending_files_like_sbx_does(self):
        G, S = self.G, self.S; h = G["HOME"]
        old, new, orig, evil = self._gui_waiting()
        self.assertEqual(G["SYSTEM_PROGRAMS"], S["SYSTEM_PROGRAMS"]); self.assertEqual(G["SOURCE_RANK"], S["SOURCE_RANK"])
        for n in ("game", "ff"):
            self.assertEqual(G["read_origin"](n), S["read_origin"](n)); self.assertEqual(G["read_pending"](n), S["read_pending"](n))
        self.assertEqual(sorted(G["pending_decisions"]()), S["pending_names"]()); self.assertEqual(S["pending_names"](), ["ff", "game"])
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{h}/.config"}):
            for path in ("/usr/bin/x", "/opt/a/b", f"{h}/Downloads/x", f"{h}/Games/x", "/tmp/x/y"):
                self.assertEqual(G["source_kind"](path), S["source_kind"](path), path)
        # the same verdict in every situation: the old file back, the new file gone, the record moved on
        with open(old, "w") as f: f.write("x")
        self.assertEqual(sorted(G["pending_decisions"]()), S["pending_names"]()); self.assertEqual(S["pending_names"](), ["ff"])
        os.unlink(evil); self.assertEqual(G["pending_decisions"](), {}); self.assertEqual(S["pending_names"](), [])
        for n in ("game", "ff"): self.assertEqual(G["pending_stale"](n, G["read_pending"](n)), S["pending_stale"](n, S["read_pending"](n)))

    def test_a_row_says_where_the_program_comes_from(self):
        G = self.G; h = G["HOME"]; G["program_running"] = lambda n: False
        self.assertEqual(G["row_subtitle"]("nothing"), "Uses the default settings")                       # never ran through sbx: nothing to say
        self._gui_origin("game", f"{h}/Downloads/Crash")
        self.assertEqual(G["row_subtitle"]("game"), "Downloaded · ~/Downloads/Crash · Uses the default settings")
        self._gui_origin("tool", "/usr/bin/true"); self.assertEqual(G["row_subtitle"]("tool"), "System · /usr/bin · Uses the default settings")
        os.makedirs(f"{h}/.local/bin"); os.symlink("/usr/local/bin/sbx", f"{h}/.local/bin/game")
        self.assertEqual(G["row_subtitle"]("game", True), "Waiting for your decision · Downloaded · ~/Downloads/Crash · link · Uses the default settings")
        new = f"{h}/Games/Crash"; os.makedirs(f"{h}/Games"); os.rename(f"{h}/Downloads/Crash", new)
        self._gui_origin("game", new, moved=f"{h}/Downloads/Crash")
        self.assertIn("Home · ~/Games/Crash · moved from ~/Downloads/Crash", G["row_subtitle"]("game"))
        os.unlink(new); G["program_running"] = lambda n: n == "game"
        self.assertTrue(G["row_subtitle"]("game").startswith("Running now · Missing: ~/Games/Crash · moved from ~/Downloads/Crash"))

    def test_programs_only_run_from_the_terminal_list_system_first_and_downloaded_last(self):
        G = self.G; h = G["HOME"]; G["program_running"] = lambda n: False
        for n, path in (("aaa", f"{h}/Downloads/a"), ("bbb", f"{h}/Games/b"), ("ccc", "/usr/bin/true")):
            os.makedirs(f"{G['HOMES']}/{n}"); self._gui_origin(n, path)
        self.assertEqual(G["program_groups"](), ([], ["ccc", "bbb", "aaa"]))

    def test_the_main_page_banner_counts_the_programs_waiting_for_a_decision(self):
        G = self.G; G["program_running"] = lambda n: False
        win = mock.MagicMock(); win.app_rows = []; win.others_open = False
        G["Win"].fill_apps(win)
        win.pending_banner.set_revealed.assert_called_with(False)
        self._gui_waiting()
        os.makedirs(G["APPS"], exist_ok=True)
        for n in ("game", "ff"): os.makedirs(f"{G['HOMES']}/{n}", exist_ok=True)
        G["Win"].fill_apps(win)
        win.pending_banner.set_title.assert_called_with("2 programs are waiting for your decision"); win.pending_banner.set_revealed.assert_called_with(True)
        self.assertEqual({c.args[0]: c.args[1] for c in win.app_row.call_args_list}, {"game": True, "ff": True})       # each row is told
        os.unlink(f"{G['HOME']}/Downloads/ff")
        G["Win"].fill_apps(win)
        win.pending_banner.set_title.assert_called_with("1 program is waiting for your decision")
        G["Win"].review_pending(win); win.open.assert_called_with("game")                                              # the banner's button

    def _buttons(self):
        made = {}
        self.G["GLib"].markup_escape_text.side_effect = lambda text: text            # the stub would hide the wording
        self.G["Gtk"].Button.side_effect = lambda **kw: made.setdefault(kw["label"], mock.MagicMock(name=kw["label"]))
        self.addCleanup(setattr, self.G["Gtk"].Button, "side_effect", None)
        return made

    def test_the_program_page_shows_old_and_new_path_with_two_buttons(self):
        G = self.G; self._gui_waiting(); made = self._buttons(); adw = G["Adw"]
        win = mock.MagicMock()
        self.assertIsNone(G["Win"].pending_group(win, "nothing waits"))
        adw.PreferencesGroup.reset_mock(); adw.ActionRow.reset_mock()
        self.assertIsNotNone(G["Win"].pending_group(win, "game"))
        self.assertEqual(adw.ActionRow.call_args.kwargs["title"], "~/Downloads/Crash → ~/Games/CrashDeluxe")
        self.assertEqual(adw.PreferencesGroup.call_args.kwargs["title"], "Waiting for your decision")
        self.assertNotIn("still at", adw.PreferencesGroup.call_args.kwargs["description"])
        self.assertEqual(set(made), {"Same program, accept", "Dismiss"})
        accept, dismiss = (made[k].connect.call_args[0][1] for k in ("Same program, accept", "Dismiss"))
        accept(); win.decide_pending.assert_called_once_with("game", True); win.ask.assert_not_called()               # rule c: one click
        dismiss(); win.decide_pending.assert_called_with("game", False)

    def test_when_the_original_is_still_there_the_warning_is_stronger_and_accepting_asks_again(self):
        G = self.G; self._gui_waiting(); made = self._buttons(); adw = G["Adw"]; win = mock.MagicMock()
        adw.PreferencesGroup.reset_mock()
        G["Win"].pending_group(win, "ff")
        desc = adw.PreferencesGroup.call_args.kwargs["description"]
        self.assertIn("The original is still at ~/apps/ff", desc); self.assertIn("--name=OTHER", desc)
        accept = made["Same program, accept"].connect.call_args[0][1]
        accept()
        win.decide_pending.assert_not_called()                                                                       # not yet: it asks first
        heading, body, label, then = win.ask.call_args[0]
        self.assertIn("~/apps/ff", body); self.assertEqual(label, "Accept anyway")
        then(); win.decide_pending.assert_called_once_with("ff", True)
        made["Dismiss"].connect.call_args[0][1](); win.decide_pending.assert_called_with("ff", False)               # dismissing is never asked

    def test_the_window_starts_a_program_that_is_not_in_path_from_its_recorded_file(self):
        G = self.G; h = G["HOME"]; cli = G["sbx_cmd"]()
        bindir = f"{h}/bin"; os.makedirs(bindir)
        with mock.patch.dict(os.environ, {"PATH": bindir}):
            self.assertIn("not in your PATH", G["start_command"]("CrashBandicoot-Linux")[1])                      # never ran from a file: say how
            game = f"{h}/Games/CrashBandicoot-Linux"; self._gui_origin("CrashBandicoot-Linux", game)
            self.assertEqual(G["start_command"]("CrashBandicoot-Linux"), ([cli, "open", game], None))              # as Open With does
            self._gui_origin("renamed", game)
            self.assertEqual(G["start_command"]("renamed"), ([cli, "--name=renamed", game], None))                 # a --name stays
            self.assertEqual(G["preview_command"]("CrashBandicoot-Linux"), ([cli, "preview", "--input", game], None))   # not just `sbx preview NAME`: that only looks in PATH
            self.assertEqual(G["preview_command"]("renamed"), ([cli, "preview", "--name=renamed", game], None))
            os.makedirs(G["APPS"], exist_ok=True)
            with open(f"{G['APPS']}/CrashBandicoot-Linux.conf", "w") as f: f.write("net=0\n")
            self.assertEqual(G["preview_command"]("CrashBandicoot-Linux"), ([cli, "preview", game], None))             # a settings file: exactly that file
            os.unlink(f"{G['APPS']}/CrashBandicoot-Linux.conf")
            os.unlink(game); problem = G["start_command"]("CrashBandicoot-Linux")
            self.assertEqual(problem[0], None); self.assertIn("~/Games/CrashBandicoot-Linux is gone", problem[1])
            prog = f"{bindir}/tool"
            with open(prog, "w") as f: f.write("#!/bin/sh\n")
            os.chmod(prog, 0o755)
            self.assertEqual(G["start_command"]("tool"), ([cli, "tool"], None))                                    # in PATH: by name
            self.assertEqual(G["preview_command"]("tool"), ([cli, "preview", "tool"], None))
            self.assertIn("gone", G["preview_command"]("CrashBandicoot-Linux")[1])
            os.unlink(prog); os.symlink("/usr/local/bin/sbx", prog)
            self.assertIsNone(G["start_command"]("tool")[0])                                                       # an sbx link is not the program

    def test_run_hands_the_recorded_file_to_sbx(self):
        G = self.G; game = f"{G['HOME']}/Games/Crash"; self._gui_origin("Crash", game); win = mock.MagicMock()
        with mock.patch.dict(os.environ, {"PATH": f"{G['HOME']}/nowhere"}), mock.patch.object(G["subprocess"], "Popen") as popen:
            G["Win"].run_program(win, "Crash")
        cmd = popen.call_args[0][0]; self.assertEqual(cmd[-3:], [G["sbx_cmd"](), "open", game]); win.alert.assert_not_called()

    def test_run_shows_why_instead_of_starting_a_missing_file(self):
        G = self.G; win = mock.MagicMock()
        with mock.patch.dict(os.environ, {"PATH": f"{G['HOME']}/nowhere"}), mock.patch.object(G["subprocess"], "Popen") as popen:
            G["Win"].run_program(win, "ghost")
        popen.assert_not_called(); self.assertEqual(win.alert.call_args[0][0], "Can't start ghost")

    def test_the_buttons_style_follows_the_rule(self):
        G = self.G; self._gui_waiting(); win = mock.MagicMock(); styles = {}
        G["GLib"].markup_escape_text.side_effect = lambda text: text
        G["Gtk"].Button.side_effect = lambda **kw: styles.__setitem__(kw["label"], kw["css_classes"]) or mock.MagicMock()
        self.addCleanup(setattr, G["Gtk"].Button, "side_effect", None)
        G["Win"].pending_group(win, "game"); self.assertEqual(styles["Same program, accept"], ["suggested-action"])
        G["Win"].pending_group(win, "ff"); self.assertEqual(styles["Same program, accept"], ["destructive-action"])
        self.assertEqual(styles["Dismiss"], ["flat"])

    def test_the_buttons_run_sbx_rebind_through_the_cli(self):
        G = self.G; win = mock.MagicMock(); cli = G["sbx_cmd"]()
        done = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(G["subprocess"], "run", return_value=done) as run:
            G["Win"].decide_pending(win, "game", True)
            self.assertEqual(run.call_args[0][0], [cli, "rebind", "game"])
            win.nav.replace.assert_called_once(); win.fill_apps.assert_called_once(); win.alert.assert_not_called()
            self.assertIn("accepted", G["Adw"].Toast.call_args.kwargs["title"])
            G["Win"].decide_pending(win, "game", False)
            self.assertEqual(run.call_args[0][0], [cli, "rebind", "--dismiss", "game"]); self.assertIn("Dismissed", G["Adw"].Toast.call_args.kwargs["title"])
        win.reset_mock()
        failed = types.SimpleNamespace(returncode=1, stdout="", stderr="sbx: nothing is waiting for a decision for 'game'\n")
        with mock.patch.object(G["subprocess"], "run", return_value=failed):
            G["Win"].decide_pending(win, "game", True)
        self.assertIn("nothing is waiting", win.alert.call_args[0][1]); win.nav.replace.assert_called_once()           # the page shows how things stand now
        win.reset_mock()
        with mock.patch.object(G["subprocess"], "run", side_effect=OSError("no sbx")):
            G["Win"].decide_pending(win, "game", True)
        win.alert.assert_called_once(); win.nav.replace.assert_not_called()

    def test_the_decision_made_through_the_cli_clears_the_banner(self):
        G, S = self.G, self.S; self._gui_waiting()
        S["warn"] = lambda m: None
        with contextlib.redirect_stdout(io.StringIO()): S["cmd_rebind"](["game"]); S["cmd_rebind"](["--dismiss", "ff"])
        self.assertEqual(G["pending_decisions"](), {})

    def test_the_program_page_says_where_the_program_comes_from(self):
        G = self.G; h = G["HOME"]; adw = G["Adw"]; win = mock.MagicMock()
        self.assertIsNone(G["Win"].source_group(win, "never"))
        self._gui_origin("game", f"{h}/Downloads/Crash", moved=f"{h}/Old/Crash")
        adw.ActionRow.reset_mock(); G["Win"].source_group(win, "game")
        kw = adw.ActionRow.call_args.kwargs
        self.assertEqual(kw["title"], "Downloaded · ~/Downloads/Crash"); self.assertIn("moved from ~/Old/Crash", kw["subtitle"])
        os.unlink(f"{h}/Downloads/Crash"); adw.ActionRow.reset_mock(); G["Win"].source_group(win, "game")
        kw = adw.ActionRow.call_args.kwargs
        self.assertEqual(kw["title"], "Missing: ~/Downloads/Crash"); self.assertIn("The recorded file is gone", kw["subtitle"])

    def test_switch_rows_do_not_carry_a_default_note(self):
        G = self.G
        os.makedirs(G["APPS"], exist_ok=True)
        key = next(iter(G["SWITCHES"]))
        with open(f"{G['APPS']}/app.conf", "w") as f: f.write(f"{key}=1\n")
        for scope in ("app", "other", None):                                             # overridden, inherited, the default page itself
            row = G["Adw"].SwitchRow.return_value; row.set_subtitle.reset_mock()
            G["Win"].switch_row(mock.MagicMock(), scope, key)
            row.set_subtitle.assert_called_with(G["SWITCHES"][key][1])

    def test_the_window_will_not_trash_the_home_of_a_running_program(self):
        G = self.G
        G["RUNNING"] = f"{self.tmp}/run"; os.makedirs(G["RUNNING"])
        with open("/proc/self/stat", "rb") as f: start = f.read().rsplit(b")", 1)[1].split()[19].decode()
        G["live_sbx"] = lambda pid: True
        with open(f"{G['RUNNING']}/{os.getpid()}", "w") as f: f.write(f"app\n{start}")
        self.assertTrue(G["program_running"]("app")); self.assertFalse(G["program_running"]("other"))
        with open(f"{G['RUNNING']}/{os.getpid()}", "w") as f: f.write("app\n1")
        self.assertFalse(G["program_running"]("app"))

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

    def test_startup_arguments_say_which_page_a_second_start_shows(self):
        pa = self.G["parse_args"]
        self.assertEqual(pa([]), (False, None))
        self.assertEqual(pa(["--default"]), (None, None))
        self.assertEqual(pa(["firefox"]), ("firefox", None))
        self.assertEqual(pa(["--log"]), (False, ""))                  # a notification click: the log of all programs...
        self.assertEqual(pa(["--log", "firefox"]), (False, "firefox"))   # ...or of one, handed to the window that is already open

    def test_a_silent_crash_hints_at_user_namespaces_only_while_they_are_off(self):
        crash = self.run_log(code=139)                                # a core dump and nothing printed: only the exit line
        self.assertIn(("userns", True), self.hint_keys(crash, userns=False, x11=True))
        self.assertEqual(self.hint_keys(crash, userns=True, x11=True), [])             # on already: not the cause, nothing to suggest
        self.assertIn(("userns", True), self.hint_keys(self.run_log(code=134), userns=False))
        self.assertEqual(self.hint_keys(crash, userns=False, x11=False)[0], ("userns", True))      # before the X11 guess
        self.assertEqual(self.hint_keys(self.run_log(code=1), userns=False, x11=True), [])         # an ordinary error is not a crash
        marked = [self.START, "[2026-11-11 20:00:05] [app] EXIT code=139 (SIGSEGV)"]                 # the line run() writes now
        self.assertIn(("userns", True), self.hint_keys(marked, userns=False, x11=True))

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

    WGPU = ["wgpu error: Validation Error", "Caused by:", "  In Device::create_shader_module, label = 'iced_wgpu.quad.solid.shader'",
            "Shader requires capability Capabilities(SHADER_FLOAT16_IN_FLOAT32)"]
    NOTFOUND = ["thread 'main' (2) panicked at src/lang.rs:10:89:",
                "called `Result::unwrap()` on an `Err` value: Os { code: 2, kind: NotFound, message: \"No such file or directory\" }"]

    def test_an_iced_shader_failure_suggests_the_cpu_renderer_until_it_is_set(self):
        G = self.G
        lines = self.run_log(*self.WGPU, code=101)
        keys = lambda lists: [w for _k, w, *_ in G["log_hints"](lines, {}, lists)]
        self.assertEqual(keys({}), ["WGPU_BACKEND=gl", "ICED_BACKEND=tiny-skia"])                       # OpenGL first, the processor second
        self.assertEqual(keys({"setenv": ["WGPU_BACKEND=gl"]}), ["ICED_BACKEND=tiny-skia"])             # still failing with it: the next one
        self.assertEqual(keys({"setenv": ["WGPU_BACKEND=gl", "ICED_BACKEND=tiny-skia"]}), [])           # both set
        self.assertEqual(keys({"setenv": ["WGPU_BACKEND=vulkan", "ICED_BACKEND=wgpu"]}), [])            # chosen on purpose: leave them
        other = self.run_log("wgpu error: Validation Error", "Shader requires capability Capabilities(X)", code=101)   # wgpu but not iced
        self.assertEqual([w for _k, w, *_ in G["log_hints"](other, {}, {})], ["WGPU_BACKEND=gl"])
        plain = self.run_log("wgpu error: Validation Error", "something in another_crate", code=101)    # no capability message: nothing to say
        self.assertEqual(G["log_hints"](plain, {}, {}), [])

    def test_the_hint_button_adds_the_variable_to_the_programs_file_and_keeps_the_rest(self):
        G = self.G
        os.makedirs(G["APPS"], exist_ok=True)
        with open(f"{G['APPS']}/app.conf", "w") as f: f.write("net=0\nsetenv=A=1\n")
        self.assertEqual(G["effective_lists"]("app"), {"setenv": ["A=1"]})
        self.assertTrue(G["add_item"]("app", "setenv", "ICED_BACKEND=tiny-skia")); G["add_item"]("app", "setenv", "ICED_BACKEND=tiny-skia")   # twice: once
        with open(f"{G['APPS']}/app.conf") as f: self.assertEqual(f.read(), "net=0\nsetenv=A=1\nsetenv=ICED_BACKEND=tiny-skia\n")
        self.assertEqual([w for _k, w, *_ in G["log_hints"](self.run_log(*self.WGPU, code=101), {}, G["effective_lists"]("app"))], ["WGPU_BACKEND=gl"])      # that one is gone, the other stays

    def test_a_panic_on_a_missing_file_offers_to_start_in_the_programs_own_folder(self):
        G = self.G
        got = G["log_hints"](self.run_log(*self.NOTFOUND, code=101), {}, {})
        self.assertEqual([(k, w) for k, w, *_ in got], [("folder", True)])
        self.assertIn("--here", got[0][3]); self.assertIn("Downloads", got[0][3])
        self.assertEqual(G["log_hints"](self.run_log(*self.NOTFOUND, code=101), {"folder": True}, {}), [], "already on: nothing to suggest")
        self.assertEqual(G["log_hints"](self.run_log("thread 'main' panicked at x.rs:1:1:", "index out of bounds", code=101), {}, {}), [])
        both = G["log_hints"](self.run_log(*self.NOTFOUND, *self.WGPU, code=101), {}, {})                # both can show, each once
        self.assertEqual(sorted(str(k) for k, *_ in both), ["folder", "setenv", "setenv"])

    def test_every_hint_names_a_real_switch(self):
        G = self.G
        for pat, key, want, title, why, needs in G["LOG_HINTS"]:
            re.compile(pat)
            if key is None: self.assertIsNone(want)                                   # advice, no button
            elif key in self.S["LISTS"]: self.assertRegex(want, r"^[A-Za-z_][A-Za-z0-9_]*=.+$")      # setenv=NAME=value
            else: self.assertIn(key, self.S["SCALARS"]); self.assertIsInstance(want, bool)
            self.assertTrue(title and why)
            for k in needs: self.assertIn(k, self.S["SCALARS"])

    def test_environment_variables_can_be_added_replaced_and_removed_from_the_window(self):
        G, S = self.G, self.S
        self.assertEqual(G["ENV_PROTECTED"], S["ENV_PROTECTED"], "sbx-gui and sbx must refuse the same variables")
        for name, value, bad in (("SDL_VIDEODRIVER", "x11", False), ("_X1", "", False), ("1X", "a", True), ("A-B", "a", True), ("", "a", True),
                                 ("HOME", "/x", True), ("SBX_ACTIVE", "1", True), ("FOO", "a #b", True), ("FOO", "a#b", False)):
            self.assertEqual(bool(G["env_problem"](name, value)), bad, (name, value))
        self.assertIsNone(G["env_problem"]("PATH")); self.assertTrue(G["env_problem"]("HOME"))          # passed through: no value to check
        self.assertTrue(G["add_item"]("app", "setenv", "SDL_VIDEODRIVER=x11")); G["add_item"]("app", "env", "MY_TOKEN")
        self.assertEqual(G["raw_list"]("app", "setenv"), ["SDL_VIDEODRIVER=x11"]); self.assertEqual(G["raw_list"]("app", "env"), ["MY_TOKEN"])
        G["remove_item"]("app", "setenv", "SDL_VIDEODRIVER=x11")
        self.assertEqual(G["raw_list"]("app", "setenv"), []); self.assertEqual(G["raw_list"]("app", "env"), ["MY_TOKEN"], "the other list is untouched")
        G["set_key"]("app", "net", False)
        self.assertEqual(G["raw_list"]("app", "env"), ["MY_TOKEN"], "a switch change keeps the variable lines")

    def test_the_folder_switch_has_wording_and_warns_when_the_folder_cannot_be_shared(self):
        G = self.G
        self.assertIn("folder", G["SWITCHES"]); self.assertIn("folder", G["SHORT"]); self.assertIn("folder", self.S["SCALARS"])
        h = G["HOME"]
        os.makedirs(f"{h}/Games/Crash", exist_ok=True); os.makedirs(f"{h}/Downloads", exist_ok=True)
        for rel, bad in (("Games/Crash/game", False), ("Downloads/game", True), ("game", True)):
            p = f"{h}/{rel}"
            with open(p, "w") as f: f.write("x")
            os.makedirs(f"{G['CONF']}/origins", exist_ok=True)
            with open(f"{G['CONF']}/origins/game", "w") as f: f.write(f"{p}\n")
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": f"{h}/.config"}):
                self.assertEqual(bool(G["folder_problem"]("game")), bad, rel)

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
        if self.listen: self.listen.close(); self.listen = None
        if self.close_fd is not None:
            try: os.close(self.close_fd)
            except OSError: pass
            self.close_fd = None

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
        cls.report = f"{cls.home}/sbx-report"
        shutil.copy(f"{HERE}/sbx-report", cls.report); os.chmod(cls.report, 0o755)
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

    def test_the_report_shows_what_the_sandbox_hides(self):
        r = self.sbx_run("--net=none", "--privacy", self.report, *self.canaries)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(TOKEN, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"blocked\s+Internet")
        for gone in (".ssh", ".gnupg", ".aws", ".config/sbx", ".sbx-homes"): self.assertRegex(r.stdout, rf"hidden\s+~/{re.escape(gone)}\s*\n")
        self.assertNotRegex(r.stdout, r"(?m)^EXPOSED")
        self.assertIn("Nothing EXPOSED", r.stdout)

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

    def test_net_private_reaches_the_internet_stack_but_not_this_computer(self):
        if not shutil.which("pasta"): self.skipTest("pasta is not installed")
        if subprocess.run(["pasta", "--quiet", "--config-net", "--", "true"], capture_output=True, timeout=30).returncode != 0:
            self.skipTest("pasta cannot start here")
        probe = ("import os, socket, sys\n"
                 "s = socket.socket(); s.settimeout(5)\n"
                 "try: s.connect(('127.0.0.1', int(sys.argv[1]))); r = 'connected'\n"
                 "except OSError as e: r = 'refused' if e.errno == 111 else 'error %s' % e\n"
                 "print(r, os.getuid(), os.getgid(), repr(open('/etc/resolv.conf').read()), sorted(os.listdir('/sys/class/net')))\n")
        with socket.socket() as srv:                                  # something on this computer that the program must not reach
            srv.bind(("127.0.0.1", 0)); srv.listen(5)
            port = str(srv.getsockname()[1])
            r = self.sbx_run("--ignore-config", "python3", "-c", probe, port)            # control: the shared network does reach it
            self.assertEqual(r.stdout.split()[0], "connected", r.stdout + r.stderr)
            run = f"{self.T}/run-pasta"
            r = self.sbx_run("--ignore-config", "--net-private", "python3", "-c", probe, port, env={"XDG_RUNTIME_DIR": run})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.split()[0], "refused", r.stdout + r.stderr)
        self.assertIn(f" {os.getuid()} {os.getgid()} ", r.stdout)                         # still your own ids
        self.assertIn("'nameserver 169.254.1.1\\n'", r.stdout)
        self.assertTrue(r.stdout.rstrip().endswith("[]"), "/sys/class/net should be empty: " + r.stdout)
        left = os.listdir(f"{run}/sbx") if os.path.isdir(f"{run}/sbx") else []
        self.assertEqual([n for n in left if n.endswith(("seccomp", "resolv.conf"))], [], "the helper files are removed when the run ends")

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
        self.assertRegex(r.stdout, r"(OK|WARN)\s+pasta")
        os.makedirs(f"{self.home}/.config/sbx/apps", exist_ok=True)
        with open(f"{self.home}/.config/sbx/apps/showtest.conf", "w") as f: f.write("net=0\nmemory=1G\nwhitelist=~/Documents\n")
        r = self.sbx_run("show", "--env=ZED", "showtest")
        self.assertRegex(r.stdout, r"net\s+= 0\s+\[apps/showtest.conf\]")
        self.assertRegex(r.stdout, r"gpu\s+= 1\s+\[default\]"); self.assertRegex(r.stdout, r"memory\s+= 1G")
        self.assertRegex(r.stdout, r"whitelist\s+= ~/Documents\s+\[apps/showtest.conf\]"); self.assertRegex(r.stdout, r"env\s+= ZED\s+\[flag\]")
        import json
        self.assertIn("hostname", json.loads(self.sbx_run("schema").stdout)["switches"])
        self.assertIn("net_private", json.loads(self.sbx_run("schema").stdout)["switches"])

    def test_privacy_is_a_switch_and_a_flag(self):
        import json
        self.assertIn("privacy", json.loads(self.sbx_run("schema").stdout)["switches"])
        self.assertIn("--privacy", self.sbx_run("--help").stdout + self.sbx_run("--help").stderr)

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
            with open(p, "w") as f: f.write("#!/bin/sh\necho ran-$0\n" + ("# a different program\n" if p == evil else ""))     # same content would be the same program
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
