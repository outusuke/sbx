#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for sbx-trace. Run as a normal user:   python3 tools/test_sbx_trace.py
# Nothing here needs bpftrace or root: the parser, the bpftrace program text, the report and the held start are tested, and the
# helper is a small fake that prints what bpftrace would.
import contextlib, importlib.machinery, importlib.util, io, json, os, signal, stat, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    loader = importlib.machinery.SourceFileLoader("sbx_trace_under_test", f"{HERE}/sbx-trace")
    spec = importlib.util.spec_from_loader("sbx_trace_under_test", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    return mod


T = load()
SAMPLE = [
    "4242\tREADY",
    "4242\tx\t1234\t0\t-1\t@H@/bin/foo",
    "4242\to\t1234\t3\t524288\t/etc/ld.so.cache",
    "4242\to\t1234\t-2\t0\t@H@/.ssh/config",
    "4242\to\t1234\t4\t577\t@H@/.config/foo/s.json",
    "4242\to\t1234\t5\t0\t@H@/.config/foo/s.json",
    "4242\to\t1235\t-13\t0\t/etc/shadow",
    "4242\tp\t1234\t0\t-1\t/tmp",
    "4242\to\t1234\t-1\t0\t/proc/../etc/../tmp//x",
    "4242\to\t1234\t9\t0\trelative/file",
    "4242\to\t1234\t3\t0\t/proc/self/maps",
    "Attaching 23 probes...", "garbage line", "9999\to\t1\t3\t0\t/forged", ""]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-trace-test-"))
        self.home = f"{self.tmp}/home"; os.makedirs(self.home)
        for name, val in (("HOME", self.home), ("STATE", f"{self.tmp}/state")):
            p = mock.patch.object(T, name, val); p.start(); self.addCleanup(p.stop)

    def tr(self, lines=None, token=4242):
        t = T.Trace(token)
        for l in (lines or SAMPLE): t.feed(l.replace("@H@", self.home))
        return t


class Parser(Base):
    def test_it_counts_reads_writes_probes_and_runs_with_their_errors(self):
        t = self.tr(); p, H = t.paths, self.home
        self.assertTrue(t.ready.is_set())
        self.assertEqual(p[f"{H}/bin/foo"], {"x": 1}); self.assertEqual(p["/tmp"], {"p": 1})
        self.assertEqual(p[f"{H}/.config/foo/s.json"], {"w": 1, "r": 1})            # O_WRONLY|O_CREAT|O_TRUNC is a write, O_RDONLY a read
        self.assertEqual(p[f"{H}/.ssh/config"], {"r": 1, "err": {"ENOENT": 1}})
        self.assertEqual(p["/etc/shadow"], {"r": 1, "err": {"EACCES": 1}})
        self.assertEqual(p["/tmp/x"], {"r": 1, "err": {"EPERM": 1}})                 # the path is cleaned up
        self.assertNotIn("relative/file", p); self.assertEqual(t.unresolved, 1)      # relative: unknown, counted
        self.assertNotIn("/forged", p)                                               # a line without this run's token is not ours
        self.assertEqual(t.lines, 10); self.assertEqual(len(t.pids), 2)

    def test_a_long_path_is_counted_not_kept_cut_off(self):
        self.assertEqual(self.tr(["4242\to\t1\t3\t0\t/" + "a" * 300]).unresolved, 1)

    def test_the_rest_of_a_name_with_a_newline_cannot_forge_a_line(self):
        t = self.tr(["4242\to\t1\t3\t0\t/tmp/b", "\to\t1\t3\t0\t/etc/passwd"])        # what is left of a name like /tmp/b\n...
        self.assertEqual(set(t.paths), {"/tmp/b"})

    def test_it_stays_small_however_much_the_program_does(self):
        with mock.patch.object(T, "PATHS_MAX", 3):
            t = T.Trace(4242)
            for i in range(10): t.feed(f"4242\to\t1\t3\t0\t/x/{i}")
            t.feed("4242\to\t1\t3\t0\t/x/0")                                         # one that is kept is still counted
        self.assertEqual((len(t.paths), t.dropped, t.paths["/x/0"]["r"]), (3, 7, 2))
        t.feed("4242\to\t1\t3\t0\t/" + "a" * 20000); self.assertEqual(len(t.paths), 3)   # a line over the limit is skipped

    def test_it_reads_a_pipe(self):
        r, w = os.pipe()
        with os.fdopen(w, "wb") as f:
            for l in SAMPLE: f.write(l.replace("@H@", self.home).encode() + b"\n")
            f.write(b"4242\to\t1\t3\t0\t/" + b"a" * 40000 + b"\n4242\to\t1\t3\t0\t/last\n")   # an endless line is skipped, the next one is read
        t = T.Trace(4242)
        with os.fdopen(r, "rb") as f: t.read_pipe(f)
        self.assertIn("/last", t.paths); self.assertEqual(t.lines, 11)


class Program(unittest.TestCase):
    def test_the_program_follows_only_the_sandbox_unless_asked(self):
        for m in T.PROBES:
            sc = T.script(m)
            self.assertIn("sched_process_fork", sc); self.assertIn("->level >= 1", sc); self.assertIn("exit();", sc)
            self.assertNotIn("->level", T.script(m, False))
            for c in T.PROBES[m]: self.assertIn(f"sys_enter_{c} ", sc); self.assertIn(f"sys_exit_{c} ", sc)
            self.assertEqual(sc.count("printf"), len(T.PROBES[m]) + 1)               # READY and one line per call
            self.assertTrue(all('printf("%llu\\t' in l for l in sc.splitlines() if "printf" in l))   # the token comes first on every line, in 64 bits: %u is 32 bits in bpftrace 0.26
        self.assertIn("args->flags", T.script("x86_64").split("sys_enter_openat ")[1].split("\n")[0])
        self.assertNotIn("sys_enter_open ", T.script("aarch64"))                     # the old calls don't exist on arm64
        for m in T.PROBES: self.assertNotIn("sys_enter_stat ", T.script(m)); self.assertNotIn("sys_enter_lstat ", T.script(m))   # no tracepoint on newer kernels: bpftrace refuses the whole script


class Report(Base):
    def test_blocked_paths_come_first_and_hidden_means_the_host_has_it(self):
        H = self.home
        os.makedirs(f"{H}/.ssh"); open(f"{H}/.ssh/config", "w").close(); os.makedirs(f"{H}/Documents"); open(f"{H}/Documents/a.txt", "w").close()
        t = self.tr(SAMPLE + [f"4242\to\t1\t-2\t0\t{H}/Documents/a.txt", "4242\to\t1\t-2\t0\t/usr/lib/nothing"])
        v = T.view(t); kinds = {b["path"]: b["kind"] for b in v["blocked"]}
        self.assertEqual(kinds["/etc/shadow"], "denied"); self.assertEqual(kinds[f"{H}/Documents/a.txt"], "hidden")
        self.assertEqual(kinds[f"{H}/.ssh/config"], "hidden"); self.assertNotIn("/usr/lib/nothing", kinds)   # not on the host either: just probing
        self.assertEqual(v["blocked"][0]["kind"], "denied"); self.assertEqual(v["noise"]["total"], 1)
        self.assertTrue(v["home"]["total"] >= 3 and v["system"]["total"] >= 3)
        self.assertNotIn(f"{H}/.ssh/config", {b["path"] for b in T.view(t, host=True)["blocked"]})   # without a sandbox a missing file is just missing

    def test_failed_dev_and_run_paths_are_shown_and_the_rest_of_them_stay_quiet(self):
        t = self.tr(["4242\to\t1\t3\t0\t/dev/null", "4242\to\t1\t-2\t0\t/dev/dri/card0", "4242\to\t1\t-2\t0\t/run/user/1000/wayland-0",
                     "4242\to\t1\t3\t0\t/run/utmp", "4242\to\t1\t-2\t0\t/proc/self/nothing", "4242\to\t1\t-13\t0\t/sys/x"])
        with mock.patch.object(T.os.path, "lexists", side_effect=lambda p: p.startswith(("/dev/", "/run/", "/proc/"))):
            v = T.view(t)
        kinds = {b["path"]: b["kind"] for b in v["blocked"]}
        self.assertEqual(kinds, {"/dev/dri/card0": "hidden", "/run/user/1000/wayland-0": "hidden", "/sys/x": "denied"})   # /proc and /sys are never "hidden"
        self.assertEqual({r["path"] for r in v["system"]["rows"]}, {"/dev/dri/card0", "/run/user/1000/wayland-0"})
        self.assertEqual(v["noise"]["total"], 4)                                        # /dev/null, /run/utmp, /proc/self/nothing, /sys/x

    def test_the_text_has_no_control_characters_and_is_saved_privately(self):
        t = self.tr(["4242\to\t1\t-13\t0\t/tmp/evil\x1b[31mname"]); v = T.view(t); v["name"] = "foo"
        out = io.StringIO()
        with contextlib.redirect_stdout(out): T.show(v, 40)
        self.assertNotIn("\x1b", out.getvalue()); self.assertIn("denied    /tmp/evil?[31mname", out.getvalue())
        T.save("foo", v)
        self.assertEqual(stat.S_IMODE(os.stat(T.report_path("foo")).st_mode), 0o600); self.assertEqual(stat.S_IMODE(os.stat(T.STATE).st_mode), 0o700)
        with open(T.report_path("foo")) as f: self.assertEqual(json.load(f)["name"], "foo")

    def test_names(self):
        self.assertEqual(T.name_of(["/usr/bin/firefox", "x"]), "firefox")
        self.assertEqual(T.name_of(["sbx", "--net", "firefox", "-p"]), "firefox")
        self.assertEqual(T.name_of(["/opt/sbx", "run me"]), "run_me")


class Fake(Base):
    """The real start-up (the held command, the handshake, the pipe) with a fake bpftrace."""
    def fake(self, body):
        p = f"{self.tmp}/bpftrace"
        with open(p, "w") as f: f.write("#!/usr/bin/python3\nimport os, sys, time\ntoken, pid = sys.argv[-1], int(sys.argv[-2])\n" + body)
        os.chmod(p, 0o755); return p

    def run_trace(self, exe, cmd):
        old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            with mock.patch.object(T, "support", return_value=(("/usr/bin/env", exe), "")), contextlib.redirect_stderr(io.StringIO()):
                return T.trace(cmd)
        finally:
            for s, h in old.items(): signal.signal(s, h)

    def test_the_command_waits_for_the_helper_and_its_exit_code_is_passed_on(self):
        mark = f"{self.tmp}/ready"
        exe = self.fake(f"""
open({mark!r}, "w").close()
print(token + "\\tREADY", flush=True)
while True:
    try: os.kill(pid, 0)
    except ProcessLookupError: break
    time.sleep(0.05)
print(token + "\\to\\t1\\t-2\\t0\\t/etc/missing", flush=True)
""")
        rc, tr = self.run_trace(exe, ["/bin/sh", "-c", f"test -f {mark} || exit 9; exit 3"])    # exits 9 if it started before READY
        self.assertEqual(rc, 3); self.assertEqual(tr.paths["/etc/missing"]["err"], {"ENOENT": 1})

    def test_when_the_helper_fails_the_command_is_not_run(self):
        ran = f"{self.tmp}/ran"
        exe = self.fake("print('Error: cannot attach', file=sys.stderr)\nsys.exit(1)\n")
        with self.assertRaises(SystemExit) as c: self.run_trace(exe, ["/bin/sh", "-c", f"touch {ran}"])
        self.assertNotEqual(c.exception.code, 0); self.assertFalse(os.path.exists(ran))

    def test_lost_events_are_counted_and_warned_about(self):
        exe = self.fake("print(token + '\\tREADY', flush=True)\nprint('Lost 5 events', file=sys.stderr, flush=True)\n"
                        "while True:\n    try: os.kill(pid, 0)\n    except ProcessLookupError: break\n    time.sleep(0.05)\nprint('WARNING: Lost 7 events', file=sys.stderr, flush=True)\n")
        rc, tr = self.run_trace(exe, ["/bin/sh", "-c", "exit 0"])
        self.assertIn(tr.lost, (5, 12))                                                # the second message can come after we stopped reading
        v = T.view(tr); v["name"] = "x"; out = io.StringIO()
        with contextlib.redirect_stdout(out): T.show(v, 40)
        self.assertIn(f"bpftrace lost {tr.lost} events", out.getvalue())

    def test_the_limit_needs_a_number_and_last_with_no_reports_says_so(self):
        for argv in (["--limit"], ["--limit", "abc"], ["--limit", "²"]):
            with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit): T.main(argv)
            self.assertIn("--limit needs a number", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit): T.main(["--last"])
        self.assertIn("no report yet", err.getvalue())

    def test_it_says_why_it_cannot(self):
        with mock.patch.object(T.shutil, "which", return_value=None): h, why = T.support(); self.assertIsNone(h); self.assertIn("bpftrace is missing", why)
        with mock.patch.object(T.shutil, "which", return_value="/usr/bin/x"), mock.patch.object(T.os.path, "exists", return_value=False):
            h, why = T.support(); self.assertIsNone(h); self.assertIn("BTF", why)
        with mock.patch.object(T.shutil, "which", side_effect=lambda x: "/usr/bin/bpftrace" if x == "bpftrace" else None), mock.patch.object(T.os.path, "exists", return_value=True):
            h, why = T.support(); self.assertIsNone(h); self.assertIn("root", why)

    def test_main_saves_a_report_and_shows_it_again(self):
        exe = self.fake("print(token + '\\tREADY', flush=True)\nwhile True:\n    try: os.kill(pid, 0)\n    except ProcessLookupError: break\n    time.sleep(0.05)\nprint(token + '\\to\\t1\\t-13\\t0\\t/etc/shadow', flush=True)\n")
        out = io.StringIO()
        with mock.patch.object(T, "support", return_value=(("/usr/bin/env", exe), "")), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(out), \
                mock.patch.object(T.os, "geteuid", return_value=1000):
            code = T.main(["--", "/bin/sh", "-c", "exit 0"])
        self.assertEqual(code, 0); self.assertIn("denied    /etc/shadow", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out): T.main(["--last", "--json"])
        self.assertEqual(json.loads(out.getvalue())["blocked"][0]["path"], "/etc/shadow")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): T.main(["--last", "nothing"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
