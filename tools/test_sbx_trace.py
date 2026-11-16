#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for sbx-trace. Run as a normal user:   python3 tools/test_sbx_trace.py
# Nothing here needs bpftrace or root: the parser, the bpftrace program text, the report and the held start are tested, and the
# helper is a small fake that prints what bpftrace would.
import base64, contextlib, hashlib, importlib.machinery, importlib.util, io, json, os, platform, re, signal, stat, tempfile, unittest
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

    def test_writes_count_as_writes_and_what_is_normal_is_not_an_error(self):
        t = self.tr(["4242\tw\t1\t-30\t-1\t/usr/x", "4242\tw\t1\t-17\t-1\t/tmp/d", "4242\tw\t1\t-2\t-1\t/tmp/gone", "4242\tn\t1\t-2\t-1\t/tmp/new",
                     "4242\tn\t1\t-30\t-1\t/usr/y", "4242\tn\t1\t0\t-1\t/tmp/ok"])
        p = t.paths
        self.assertEqual(p["/usr/x"], {"w": 1, "err": {"EROFS": 1}})                    # refused: an error
        self.assertEqual(p["/tmp/d"], {"w": 1})                                         # mkdir of what exists: not
        self.assertEqual(p["/tmp/gone"], {"w": 1, "err": {"ENOENT": 1}})                # unlink of what is not there is a look: an error, as for a read
        self.assertEqual(p["/tmp/new"], {"w": 1})                                       # a missing OLD name says nothing about the new one
        self.assertEqual(p["/usr/y"], {"w": 1, "err": {"EROFS": 1}}); self.assertEqual(p["/tmp/ok"], {"w": 1})
        self.assertEqual(T.view(t)["blocked"][0], {"path": "/usr/x", "kind": "read-only", "n": 1})

    def test_a_write_through_openat2_is_a_write_once_its_flags_are_read(self):
        t = self.tr(["4242\to\t1\t3\t577\t/tmp/a", "4242\to\t1\t3\t0\t/tmp/b"])                # O_WRONLY|O_CREAT|O_TRUNC, and no flags
        self.assertEqual((t.paths["/tmp/a"], t.paths["/tmp/b"]), ({"w": 1}, {"r": 1}))

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
            self.assertNotIn("bwrap", T.script(m, False))                          # on the host there is no sandbox to tell apart
            for l in sc.splitlines():
                if "sys_enter_" in l: self.assertEqual('comm != "bwrap"' in l, not re.search(r"sys_enter_execve(at)? ", l))   # bwrap's setup is left out, the exec of the program is kept
            for c in T.calls(m): self.assertIn(f"sys_enter_{c} ", sc); self.assertIn(f"sys_exit_{c} ", sc)
            self.assertEqual(sc.count("printf"), len(T.calls(m)) + 1)                # READY and one line per call
            self.assertTrue(all('printf("%llu\\t' in l for l in sc.splitlines() if "printf" in l))   # the token comes first on every line, in 64 bits: %u is 32 bits in bpftrace 0.26
        self.assertIn("args->flags", T.script("x86_64").split("sys_enter_openat ")[1].split("\n")[0])
        self.assertNotIn("sys_enter_open ", T.script("aarch64"))                     # the old calls don't exist on arm64
        for m in T.PROBES: self.assertNotIn("sys_enter_stat ", T.script(m)); self.assertNotIn("sys_enter_lstat ", T.script(m))   # no tracepoint on newer kernels: bpftrace refuses the whole script


    def test_the_writes_are_watched_with_the_field_that_holds_their_path(self):
        sc = T.script("x86_64"); line = lambda c: next(l for l in sc.splitlines() if f"sys_enter_{c} " in l)
        for c, field in {**T.WRITES, **T.READS}.items(): self.assertIn(f"str(args->{field})", line(c), c)
        self.assertIn("str(args->newname)", line("renameat2")); self.assertIn("str(args->filename)", line("openat"))
        out = next(l for l in sc.splitlines() if "sys_exit_rename " in l); self.assertIn("\\tn\\t", out)         # the new name of a rename: n
        self.assertIn("\\tw\\t", next(l for l in sc.splitlines() if "sys_exit_unlink " in l)); self.assertIn("\\tp\\t", next(l for l in sc.splitlines() if "sys_exit_readlink " in l))
        for m in T.PROBES:
            self.assertIn("str(args->filename); @f[tid] = args->how->flags;", T.script(m).split("sys_enter_openat2 ")[1].split("\n")[0])      # the flags of openat2 are in a struct
            self.assertNotIn("how->flags", T.script(m, rich=False)); self.assertIn("sys_enter_openat2 ", T.script(m, rich=False))     # the basic set keeps the call, not the struct
        arm = T.script("aarch64")
        for c in T.LEGACY: self.assertNotIn(f"sys_enter_{c} ", arm, c)                  # arm64 has only the newer calls
        self.assertIn("sys_enter_unlinkat ", arm)

    def test_a_call_the_kernel_lacks_is_left_out_but_the_basic_ones_are_always_kept(self):
        have = {"unlink", "mkdirat"}
        got = T.calls("x86_64", have)
        self.assertEqual([c for c in got if c not in T.PROBES["x86_64"]], ["unlink", "mkdirat"]); self.assertEqual(got[:len(T.PROBES["x86_64"])], list(T.PROBES["x86_64"]))
        self.assertEqual(T.calls("x86_64", set())[:], list(T.PROBES["x86_64"])); self.assertGreater(len(T.calls("x86_64")), len(T.PROBES["x86_64"]))    # nothing known: all of them

    def test_the_calls_of_the_kernel_come_from_its_symbols(self):
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/kallsyms"
            with open(p, "w") as f: f.write("0000000000000000 T __x64_sys_unlink\n0000000000000000 t __x64_sys_rename.cold\n0000000000000000 T vfs_unlink\n0000000000000000 T __arm64_sys_openat [mod]\nbad\n")
            self.assertEqual(T.kernel_calls("x86_64", p), {"unlink", "rename.cold"}); self.assertEqual(T.kernel_calls("aarch64", p), {"openat"})
            self.assertIsNone(T.kernel_calls("riscv64", p)); self.assertIsNone(T.kernel_calls("x86_64", f"{d}/missing"))
            with open(p, "w") as f: f.write("0 T vfs_unlink\n")
            self.assertIsNone(T.kernel_calls("x86_64", p))                               # no system calls found: unknown, not "none"


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


class Suggest(Base):
    def report(self, blocked, rows=()):
        return {"name": "foo", "blocked": blocked, "home": {"total": len(rows), "rows": list(rows)}, "system": {"total": 0, "rows": []}}

    def test_the_narrowest_safe_setting_for_what_was_refused_or_hidden(self):
        H = self.home
        b = lambda path, kind="hidden", n=1: {"path": path, "kind": kind, "n": n}
        v = self.report([
            b(f"{H}/Projects/a.txt", "hidden", 3), b(f"{H}/Projects/out", "denied"), b(f"{H}/Projects/out/log"), b(f"{H}/Shared/x"),
            b("/mnt/data/x", "read-only"), b(f"{H}/.ssh/config"), b(f"{H}/.local/share"), b(f"{H}", "denied"), b(f"{H}/Downloads"),
            b(f"{H}/.sbx-homes/foo/x"), b(f"{H}/weird\nname"), b(f"{H}/a #b"), b(f"{H}/.config/foo/new"), b("/etc/shadow", "denied"), b("/usr/x", "read-only")],
            [{"path": f"{H}/Projects/out", "w": 2, "err": {"EACCES": 2}}])
        os.makedirs(f"{H}/.config/sbx/apps")
        with open(f"{H}/.config/sbx/apps/foo.conf", "w") as f: f.write("whitelist=~/Shared  # already\nnet=0\n")
        s = T.suggest(v, T.shared_by("foo"))
        self.assertEqual([(l["setting"], l["value"]) for l in s["lines"]],
                         [("read_only", "~/Projects/a.txt"), ("whitelist", "~/Projects/out"), ("whitelist", "/mnt/data/x"), ("read_only", "~/.config/foo/new")])
        # ~/Projects/out/log is inside the read-write path above; ~/Shared/x is already shared; /etc and /usr are not places a folder setting is suggested for
        self.assertEqual({k["path"]: k["why"].split(":")[0].split(" ")[0] for k in s["skipped"]},
                         {f"{H}/.ssh/config": "looks", f"{H}/.local/share": "looks", H: "too", f"{H}/Downloads": "too", f"{H}/.sbx-homes/foo/x": "sbx",
                          f"{H}/weird\nname": "its", f"{H}/a #b": "its"})
        self.assertEqual(s["outside"], 2)
        self.assertEqual(s["lines"][0]["why"], "the sandbox hides it"); self.assertEqual(s["lines"][0]["n"], 3)

    def test_a_read_only_path_inside_an_earlier_read_only_one_is_kept_only_when_it_needs_writing(self):
        H = self.home
        v = self.report([{"path": f"{H}/p", "kind": "hidden", "n": 1}, {"path": f"{H}/p/a", "kind": "hidden", "n": 1}, {"path": f"{H}/p/b", "kind": "read-only", "n": 1}])
        self.assertEqual([(l["setting"], l["value"]) for l in T.suggest(v)["lines"]], [("read_only", "~/p"), ("whitelist", "~/p/b")])

    def test_a_report_of_the_wrong_shape_gives_nothing_and_does_not_crash(self):
        for v in ({}, {"blocked": "x"}, {"blocked": [5, {"path": 3}, {"kind": "denied"}]}, []):
            self.assertEqual(T.suggest(v), {"lines": [], "skipped": [], "outside": 0})

    def test_suggest_shows_the_saved_report_as_text_or_json(self):
        H = self.home
        T.save("foo", self.report([{"path": f"{H}/Projects/a.txt", "kind": "hidden", "n": 2}, {"path": "/etc/shadow", "kind": "denied", "n": 1}]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(T.main(["--suggest", "foo"]), 0)
        self.assertIn("read_only=~/Projects/a.txt   # the sandbox hides it, 2x", out.getvalue()); self.assertIn("1 more blocked path outside your home", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out): T.main(["--suggest", "--json"])                  # no name: the newest report
        j = json.loads(out.getvalue()); self.assertEqual((j["name"], j["lines"][0]["value"], j["outside"]), ("foo", "~/Projects/a.txt", 1))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): T.main(["--suggest", "nothing"])


class Compare(Base):
    def rep(self, blocked, rows=(), t=100.0):
        return {"name": "foo", "time": t, "blocked": [{"path": p, "kind": k, "n": 1} for p, k in blocked], "home": {"total": 0, "rows": []},
                "system": {"total": len(rows), "rows": [{"path": p, "err": e} for p, e in rows]}}

    def test_saving_again_keeps_the_report_before_and_it_is_private(self):
        T.save("foo", self.rep([("/a", "denied")], t=1.0)); self.assertFalse(os.path.exists(T.previous_path("foo")))
        T.save("foo", self.rep([], t=2.0)); T.save("foo", self.rep([], t=3.0))
        with open(T.previous_path("foo")) as f: self.assertEqual(json.load(f)["time"], 2.0)
        with open(T.report_path("foo")) as f: self.assertEqual(json.load(f)["time"], 3.0)
        self.assertEqual(stat.S_IMODE(os.stat(T.previous_path("foo")).st_mode), 0o600); self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(T.previous_path("foo"))).st_mode), 0o700)
        T.save("bar", self.rep([])); self.assertFalse(os.path.exists(T.previous_path("bar")))      # one program's history is not another's

    def test_it_says_what_a_setting_changed(self):
        old = self.rep([("/a", "denied"), ("/b", "hidden"), ("/c", "hidden"), ("/d", "hidden")], [("/z", {})])
        new = self.rep([("/d", "hidden"), ("/e", "denied")], [("/a", {}), ("/b", {"ENOENT": 1}), ("/e", {"EACCES": 1}), ("/y", {})])
        d = T.diff(old, new)
        self.assertEqual({x["path"]: x["now"] for x in d["fixed"]}, {"/a": "works", "/b": "fails another way", "/c": "not touched"})
        self.assertEqual([b["path"] for b in d["new_blocked"]], ["/e"]); self.assertEqual(d["still_blocked"], 1)
        self.assertEqual((sorted(d["added"]), d["removed"]), (["/a", "/b", "/e", "/y"], ["/z"]))
        self.assertEqual(T.diff({}, {}), {"fixed": [], "new_blocked": [], "still_blocked": 0, "added": [], "removed": []})
        self.assertEqual(T.diff({"blocked": "x", "system": []}, {"blocked": [5, {"path": 3}]})["fixed"], [])          # a report of the wrong shape does not crash it

    def test_diff_compares_the_last_report_with_the_one_before(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit): T.main(["--diff"])               # no report at all
        T.save("foo", self.rep([("/a", "denied")], t=1.0))
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit): T.main(["--diff", "foo"])
        self.assertIn("no earlier report", err.getvalue())
        T.save("foo", self.rep([("/b", "hidden")], [("/a", {})], t=2.0))
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(T.main(["--diff", "foo"]), 0)
        self.assertIn("no longer blocked (1):\n  /a   works", out.getvalue()); self.assertIn("newly blocked (1):\n  hidden    /b", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out): T.main(["--diff", "--json"])
        j = json.loads(out.getvalue()); self.assertEqual((j["name"], j["before"], j["time"], j["fixed"][0]["path"]), ("foo", 1.0, 2.0, "/a"))

    def test_a_report_says_which_version_of_the_format_it_is(self):
        exe = Fake.fake(self, "print(token + '\\tREADY', flush=True)\nwhile True:\n    try: os.kill(pid, 0)\n    except ProcessLookupError: break\n    time.sleep(0.05)\n")
        with mock.patch.object(T, "support", return_value=(("/usr/bin/env", exe), "")), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(T.os, "geteuid", return_value=1000):
            T.main(["--", "/bin/sh", "-c", "exit 0"])
        with open(T.report_path("sh")) as f: self.assertEqual(json.load(f)["version"], T.VERSION_OF_REPORT)


class Page(Base):
    def report(self):
        H = self.home
        return {"name": "fire</title>fox", "exit": 0, "time": 1.0, "blocked": [{"path": f"{H}/.ssh/config", "kind": "hidden", "n": 1}, {"path": "/etc/shadow", "kind": "denied", "n": 1},
                                                                        {"path": f"{H}/gone", "kind": "hidden", "n": 2}],
                "home": {"total": 5, "rows": [{"path": f"{H}/.ssh/config", "err": {"ENOENT": 1}}, {"path": f"{H}/.ssh/known_hosts", "r": 2, "err": {}},
                                              {"path": f"{H}/x</script><img src=x onerror=alert(1)>\x1b[31m", "w": 1, "err": {}}]},
                "system": {"total": 1, "rows": [{"path": "/etc/shadow", "r": 1, "err": {"EACCES": 1}}]}, "noise": {"total": 7},
                "info": {"calls": 9, "processes": 2, "dropped": 0, "unresolved": 1, "lost": 3, "host": False}}

    def test_the_places_that_hold_secrets(self):
        H = self.home
        for p in (f"{H}/.ssh", f"{H}/.ssh/id_ed25519", f"{H}/.local/share/keyrings/x", "/etc/shadow", "/etc/shadow-", "/etc/sudoers.d/x", "/etc/ssh/ssh_host_rsa_key"):
            self.assertTrue(T.holds_secrets(p), p)
        for p in (f"{H}/.sshx", f"{H}/.local/share", H, "/etc/passwd", "/etc/ssh/ssh_config", f"{H}/x/.ssh"): self.assertFalse(T.holds_secrets(p), p)

    def test_the_page_gets_its_data_from_the_report_as_it_is_read(self):
        d = T.html_data(self.report()); H = self.home
        by = {r["path"]: r for r in d["rows"]}
        self.assertEqual((by["~/.ssh/config"]["k"], by["~/.ssh/config"]["s"], by["~/.ssh/known_hosts"]["k"], by["~/.ssh/known_hosts"]["s"]), ("hidden", 1, None, 1))
        self.assertEqual((by["/etc/shadow"]["k"], by["/etc/shadow"]["e"], by["~/gone"]["k"]), ("denied", {"EACCES": 1}, "hidden"))      # a blocked path the rows lack is kept
        self.assertFalse(any(H in r["path"] or "\x1b" in r["path"] for r in d["rows"]))                           # ~ for your home, no control characters
        self.assertEqual((d["more"], d["noise"], d["info"]["lost"], d["exit"]), (1, 7, 3, 0))                  # 6 files in all, 5 listed
        for bad in ({}, [], "x", {"home": 5, "system": {"rows": "x"}, "blocked": "x", "info": []}, {"blocked": [5, {"path": 3}], "home": {"rows": [5, {"path": 3}, {"path": "/a", "err": []}]}}):
            T.html_page(bad)                                                                                  # a report of the wrong shape does not crash it

    def test_the_page_runs_nothing_but_its_own_script_and_what_a_path_says_is_only_data(self):
        page = T.html_page(self.report())
        self.assertTrue(page.isascii()); self.assertEqual(page.count("<script"), 2); self.assertNotIn("</script><img", page); self.assertNotIn("<title>sbx trace: fire</title>", page)
        self.assertFalse(re.search(r"https?://", page))                                                       # nothing is fetched
        sha = base64.b64encode(hashlib.sha256(T.HTML_JS.encode()).digest()).decode()
        self.assertIn(f"script-src 'sha256-{sha}'", page); self.assertIn(f"<script>{T.HTML_JS}</script>", page)   # the policy allows exactly this script
        blob = re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1)
        self.assertEqual(json.loads(blob), json.loads(json.dumps(T.html_data(self.report()))))                # and the data reads back whole
        self.assertNotIn("@@", page.replace(blob, "").replace(T.HTML_JS, ""))                                   # no placeholder is left

    def test_html_writes_the_last_report_to_a_file_or_to_the_terminal(self):
        T.save("foo", self.report()); out = f"{self.tmp}/r.html"
        with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(T.main(["--html", "-o", out, "foo"]), 0)
        with open(out, encoding="utf-8") as f: self.assertIn("<!doctype html>", f.read())
        so = io.StringIO()
        with contextlib.redirect_stdout(so): T.main(["--html"])                                               # the newest report, to stdout
        self.assertTrue(so.getvalue().startswith("<!doctype html>"))
        for argv in (["--html", "-o"], ["--html", "-o", ""], ["--html", "nothing"], ["--html", "-o", f"{self.tmp}/no/such/dir/r.html", "foo"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit, msg=argv): T.main(argv)


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

    def test_when_bpftrace_refuses_the_full_set_it_is_tried_again_with_the_basic_set_before_the_command_runs(self):
        ran = f"{self.tmp}/ran"
        exe = self.fake("""
if "sys_enter_unlink " in " ".join(sys.argv): print("ERROR: no such field", file=sys.stderr); sys.exit(1)
print(token + "\\tREADY", flush=True)
while True:
    try: os.kill(pid, 0)
    except ProcessLookupError: break
    time.sleep(0.05)
print(token + "\\to\\t1\\t-2\\t0\\t/etc/missing", flush=True)
""")
        err = io.StringIO(); old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        try:
            with mock.patch.object(T, "support", return_value=(("/usr/bin/env", exe), "")), mock.patch.object(T, "kernel_calls", return_value=None), \
                    mock.patch.object(T, "start", wraps=T.start) as st, contextlib.redirect_stderr(err):
                rc, tr = T.trace(["/bin/sh", "-c", f"test -f {self.tmp}/ready || true; touch {ran}; exit 4"])
        finally:
            for s, h in old.items(): signal.signal(s, h)
        self.assertEqual(rc, 4); self.assertEqual(st.call_count, 2); self.assertEqual(st.call_args_list[1][0][3], list(T.PROBES[platform.machine()])); self.assertEqual(st.call_args_list[1][1], {"rich": False, "live": None}); self.assertEqual(st.call_args_list[0][1], {"live": None})
        self.assertIn("trying again with the basic set", err.getvalue()); self.assertIn("no such field", err.getvalue()); self.assertEqual(tr.paths["/etc/missing"]["err"], {"ENOENT": 1})
        self.assertTrue(os.path.exists(ran))                                           # it ran once, after the attempt that worked

    def test_a_refused_password_is_not_tried_again_and_a_basic_failure_is_final(self):
        old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
        ran = f"{self.tmp}/ran"
        try:
            with mock.patch.object(T, "support", return_value=(("/bin/false", self.fake("sys.exit(1)\n")), "")), mock.patch.object(T, "kernel_calls", return_value=None), \
                    mock.patch.object(T, "start", wraps=T.start) as st, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                T.trace(["/bin/sh", "-c", f"touch {ran}"])                              # the helper never says it started: one attempt
            self.assertEqual(st.call_count, 1)
            with mock.patch.object(T, "support", return_value=(("/usr/bin/env", self.fake("print('ERROR: nope', file=sys.stderr)\nsys.exit(1)\n")), "")), \
                    mock.patch.object(T, "kernel_calls", return_value=None), mock.patch.object(T, "start", wraps=T.start) as st, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                T.trace(["/bin/sh", "-c", f"touch {ran}"])                              # refused with the full set and then with the basic one: two attempts, then it ends
            self.assertEqual(st.call_count, 2)
        finally:
            for s, h in old.items(): signal.signal(s, h)
        self.assertFalse(os.path.exists(ran))

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


class Running(Base):
    def listing(self, text):
        return mock.patch.object(T.subprocess, "run", return_value=mock.MagicMock(stdout=text))

    def test_only_sbx_can_say_that_the_program_runs_already(self):
        with self.listing("chrome running\nfirefox idle\n") as run:
            self.assertEqual(T.running_now(["sbx", "--net=none", "chrome"]), "chrome"); self.assertIsNone(T.running_now(["sbx", "firefox"]))
            self.assertEqual(run.call_args[0][0], ["sbx", "list"])
            run.reset_mock(); self.assertIsNone(T.running_now(["/bin/sh", "-c", "exit 0"])); run.assert_not_called()      # not an sbx command: nothing to ask
        with mock.patch.object(T.subprocess, "run", side_effect=OSError): self.assertIsNone(T.running_now(["sbx", "chrome"]))

    def test_a_running_program_is_said_and_on_a_terminal_asked_about(self):
        def go(answer, tty=True, yes=False):
            err = io.StringIO()
            with mock.patch.object(T, "running_now", return_value="chrome"), contextlib.redirect_stderr(err), mock.patch("sys.stdin", mock.MagicMock(isatty=lambda: tty)), \
                    mock.patch("builtins.input", return_value=answer):
                return T.go_on_if_running(["sbx", "chrome"], yes), err.getvalue()
        ok, said = go("n"); self.assertFalse(ok); self.assertIn("chrome is already running", said); self.assertIn("sbx stop chrome", said); self.assertIn("[y/N]", said)
        self.assertFalse(go("")[0]); self.assertTrue(go("y")[0]); self.assertTrue(go("YES")[0])
        ok, said = go("n", tty=False); self.assertTrue(ok); self.assertIn("no terminal", said)       # nobody to ask: say it and go on
        with mock.patch.object(T, "running_now") as r: self.assertTrue(T.go_on_if_running(["sbx", "chrome"], True)); r.assert_not_called()
        with mock.patch.object(T, "running_now", return_value=None): self.assertTrue(T.go_on_if_running(["sbx", "chrome"], False))

    def test_main_does_not_start_the_trace_when_the_person_says_no_and_does_not_ask_with_yes(self):
        with mock.patch.object(T.os, "geteuid", return_value=1000), mock.patch.object(T, "trace", return_value=(0, self.tr())) as tr, contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(T, "go_on_if_running", return_value=False), self.assertRaises(SystemExit): T.main(["--", "sbx", "chrome"])
            tr.assert_not_called()
            with mock.patch.object(T, "go_on_if_running", return_value=True) as g: T.main(["--yes", "--", "sbx", "chrome"])
            self.assertTrue(g.call_args[0][1]); tr.assert_called_once()
            with mock.patch.object(T, "go_on_if_running", return_value=True) as g: T.main(["-y", "--", "sbx", "chrome"])
            self.assertTrue(g.call_args[0][1])


class Live(Base):
    def test_a_snapshot_is_a_report_with_no_exit_code_in_a_folder_that_last_does_not_read(self):
        T.write_live(self.tr(), False, {"name": "foo", "command": ["foo"]})
        with open(T.live_path("foo")) as f: v = json.load(f)
        self.assertTrue(v["live"]); self.assertIsNone(v["exit"]); self.assertEqual(v["name"], "foo"); self.assertEqual(v["command"], ["foo"])
        self.assertIn("/etc/shadow", [b["path"] for b in v["blocked"]])
        self.assertEqual(os.stat(T.live_path("foo")).st_mode & 0o777, 0o600)
        self.assertEqual([n for n in os.listdir(T.STATE) if n.endswith(".json")], [])          # --last would take it for a finished report

    def test_the_loop_writes_while_it_runs_and_removes_the_snapshot_when_it_ends(self):
        import threading
        done, t = threading.Event(), self.tr()
        with mock.patch.object(T, "LIVE_EVERY", 0.05):
            th = threading.Thread(target=T.live_loop, args=(t, False, {"name": "foo", "command": ["foo"]}, done)); th.start()
            for _ in range(100):
                if os.path.exists(T.live_path("foo")): break
                import time; time.sleep(0.02)
            self.assertTrue(os.path.exists(T.live_path("foo")))
            done.set(); th.join(2)
        self.assertFalse(os.path.exists(T.live_path("foo")))

    def test_the_loop_skips_a_snapshot_when_nothing_new_was_seen_and_backs_off_when_one_is_slow(self):
        import threading
        done, t, n = threading.Event(), self.tr(), []
        waits = []
        class D:
            def wait(self, delay):
                waits.append(delay); return len(waits) > 4
            def set(self): pass
        with mock.patch.object(T, "write_live", side_effect=lambda *a: n.append(1)), mock.patch.object(T, "LIVE_EVERY", 0.01), mock.patch.object(T.os, "unlink"):
            T.live_loop(t, False, {"name": "foo", "command": ["foo"]}, D())
        self.assertEqual(len(n), 1)                                                 # the trace did not move after the first snapshot

    def test_a_snapshot_never_raises(self):
        with mock.patch.object(T, "STATE", "/proc/nope"):
            T.write_live(self.tr(), False, {"name": "foo", "command": ["foo"]})


if __name__ == "__main__":
    unittest.main(verbosity=1)
