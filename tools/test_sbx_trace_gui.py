#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Tests for sbx-trace-gui. Run as a normal user:   python3 tools/test_sbx_trace_gui.py
# Nothing here needs a display, bpftrace or root. GTK is stubbed the way test_sbx.py stubs it for sbx-gui, so the window itself is not
# drawn; what is tested is everything it decides: how a saved report is read (against the real sbx-trace's own output), what is shown and
# searched, what is copied, how sbx-trace is started (no terminal, so it asks for root through pkexec), and when a run counts as done.
import importlib.machinery, importlib.util, json, os, re, shutil, signal, stat, subprocess, sys, tempfile, threading, time, types, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = f"{HERE}/sbx-trace-gui"
SBX = f"{os.path.dirname(HERE)}/sbx"


def load_trace():
    loader = importlib.machinery.SourceFileLoader("sbx_trace_for_gui_tests", f"{HERE}/sbx-trace")
    spec = importlib.util.spec_from_loader("sbx_trace_for_gui_tests", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    return mod


def load_gui(home):
    """The GUI script as a dict of its names, with GTK stubbed and HOME set to HOME."""
    stub = mock.MagicMock()
    with mock.patch.dict(sys.modules, {"gi": stub, "gi.repository": stub}), mock.patch.dict(os.environ, {"HOME": home}):
        with open(GUI) as f: src = f.read()
        ns = {"__name__": "sbx_trace_gui_under_test", "__file__": GUI}
        class Base: pass
        stub.Adw.ApplicationWindow = stub.Adw.Application = Base
        exec(compile(src, "sbx-trace-gui", "exec"), ns)
    return ns


SAMPLE = [
    "4242\to\t1234\t-2\t0\t@H@/.ssh/config",
    "4242\to\t1234\t4\t577\t@H@/.config/foo/s.json",
    "4242\to\t1234\t5\t0\t@H@/.config/foo/s.json",
    "4242\to\t1235\t-13\t0\t/etc/shadow",
    "4242\to\t1235\t-30\t0\t/usr/share/x",
    "4242\tp\t1234\t0\t-1\t/tmp",
    "4242\tx\t1234\t0\t-1\t/usr/bin/foo",
    "4242\to\t1234\t3\t0\t/proc/self/maps"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-trace-gui-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = f"{self.tmp}/home"; os.makedirs(self.home)
        self.G = load_gui(self.home)
        self.T = load_trace()
        for name, val in (("HOME", self.home), ("STATE", f"{self.tmp}/state/sbx-trace")):
            p = mock.patch.object(self.T, name, val); p.start(); self.addCleanup(p.stop)

    def put(self, path, text):
        with open(path, "w") as f: f.write(text)

    def real_report(self):
        """A report made by the real sbx-trace (its parser and its view), as it is saved and printed by --json."""
        H = self.home
        os.makedirs(f"{H}/.ssh"); open(f"{H}/.ssh/config", "w").close()
        t = self.T.Trace(4242)
        for l in SAMPLE: t.feed(l.replace("@H@", H))
        v = self.T.view(t); v.update(name="foo", command=["sbx", "foo"], exit=0, time=time.time())
        return v

    def fake_trace(self, mode):
        """A stand-in for sbx-trace: `--last` prints the report it saved, a run saves one (mode ok, exit code 3) or fails without one."""
        p = f"{self.tmp}/sbx-trace"
        with open(p, "w") as f:
            f.write("""#!/usr/bin/python3
import json, os, sys, time
state = os.path.join(os.environ["FAKE_STATE"], "r.json")
a = sys.argv[1:]
if a[:1] == ["--last"]:
    try: print(open(state).read())
    except OSError: print("sbx-trace: no report yet: sbx-trace -- COMMAND", file=sys.stderr); sys.exit(1)
    sys.exit(0)
if os.environ["FAKE_MODE"] == "fail":
    print("sbx-trace: starting bpftrace with pkexec (needs root)", file=sys.stderr)
    print("sbx-trace: the trace did not start: ~~~~~~~~", file=sys.stderr); sys.exit(1)
rep = {"name": "x", "command": a[1:], "exit": 3, "time": time.time(), "blocked": [{"path": "/etc/shadow", "kind": "denied", "n": 1}],
       "home": {"total": 0, "rows": []}, "system": {"total": 0, "rows": []}, "noise": {"total": 0, "rows": []},
       "info": {"calls": 1, "processes": 1, "dropped": 0, "unresolved": 0, "host": False}}
open(state, "w").write(json.dumps(rep))
print("the program said hello")
sys.exit(3)
""")
        os.chmod(p, 0o755)
        os.makedirs(f"{self.tmp}/fake", exist_ok=True)
        env = mock.patch.dict(os.environ, {"FAKE_STATE": f"{self.tmp}/fake", "FAKE_MODE": mode}); env.start(); self.addCleanup(env.stop)
        self.G["trace_cmd"] = lambda: p
        self.G["sbx_cmd"] = lambda: "/usr/bin/sbx"
        return p


class Reading(Base):
    def test_it_reads_what_the_real_sbx_trace_saves_and_prints(self):
        G, v = self.G, self.real_report()
        rep = G["clean_report"](json.loads(json.dumps(v)))
        H = self.home
        self.assertEqual({b["path"]: b["kind"] for b in rep["blocked"]}, {"/etc/shadow": "denied", f"{H}/.ssh/config": "hidden", "/usr/share/x": "read-only"})
        self.assertEqual(rep["blocked"][0]["kind"], "denied")                            # the order sbx-trace chose: refused first
        self.assertEqual([r["path"] for r in rep["home"]["rows"]][0], f"{H}/.config/foo/s.json")
        self.assertEqual(rep["home"]["rows"][0], {"path": f"{H}/.config/foo/s.json", "r": 1, "w": 1, "err": {}})
        self.assertEqual(rep["home"]["total"], v["home"]["total"]); self.assertEqual(rep["system"]["total"], v["system"]["total"])
        self.assertEqual(rep["noise"], 1)                                                # /proc is a count, not rows
        self.assertEqual((rep["name"], rep["exit"], rep["command"]), ("foo", 0, ["sbx", "foo"]))
        self.assertEqual(rep["info"]["calls"], v["info"]["calls"])

    def test_load_report_asks_the_real_sbx_trace_for_the_last_report(self):
        G = self.G
        G["trace_cmd"] = lambda: f"{HERE}/sbx-trace"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": f"{self.tmp}/state"}):
            rep, why = G["load_report"]()
            self.assertIsNone(rep); self.assertIn("no report yet", why); self.assertFalse(why.startswith("sbx-trace:"))
            v = self.real_report(); self.T.save("foo", v)
            v2 = dict(v, name="bar", command=["sbx", "bar"], blocked=[]); self.T.save("bar", v2)           # the newest is bar
            self.assertEqual(G["load_report"]()[0]["name"], "bar")
            rep, why = G["load_report"]("foo")
            self.assertEqual((rep["name"], why), ("foo", ""))
            self.assertIsNone(G["load_report"]("nothing")[0])
            with open(self.T.report_path("foo"), "w") as f: f.write("{not json")
            rep, why = G["load_report"]("foo"); self.assertIsNone(rep); self.assertTrue(why)
            self.assertEqual(stat.S_IMODE(os.stat(self.T.STATE).st_mode), 0o700)           # (nothing here changed what sbx-trace saved)

    def test_a_foreign_or_hostile_file_cannot_break_it(self):
        G = self.G
        for bad in ([], "x", None, {}, {"blocked": []}, {"blocked": {}, "home": {"rows": []}, "system": {"rows": []}},
                    {"blocked": [], "home": {"rows": []}, "system": {"rows": "x"}}):
            with self.assertRaises(ValueError, msg=repr(bad)): G["clean_report"](bad)
        rep = G["clean_report"]({
            "name": 5, "command": ["sbx", 7, "a"], "exit": True, "time": True,
            "blocked": [{"path": "/a", "kind": "<b>weird</b>", "n": -3}, {"path": 5}, "x", {"path": "/b", "kind": "hidden", "n": 2}],
            "home": {"total": 1, "rows": [{"path": "/h/a", "r": True, "w": -1, "p": 2, "err": [1]}, {"path": None}, {"path": "/h/b", "err": {"EACCES": "many", "ENOENT": 4}}, 3]},
            "system": {"total": "lots", "rows": []}, "noise": {"total": -1}, "info": {"calls": "7", "processes": 2}})
        self.assertEqual((rep["name"], rep["exit"], rep["time"], rep["command"]), ("?", None, None, ["sbx", "a"]))
        self.assertEqual(rep["blocked"], [{"path": "/a", "kind": "denied", "n": 1}, {"path": "/b", "kind": "hidden", "n": 2}])
        self.assertEqual(rep["home"]["rows"], [{"path": "/h/a", "p": 2, "err": {}}, {"path": "/h/b", "err": {"ENOENT": 4}}])
        self.assertEqual(rep["home"]["total"], 2)                                        # never less than the rows it has
        self.assertEqual((rep["system"]["total"], rep["noise"], rep["info"]), (0, 0, {"calls": 0, "processes": 2, "dropped": 0, "unresolved": 0, "lost": 0}))

    def test_a_run_counts_when_it_saved_its_report_whatever_the_program_exit_code(self):
        G = self.G; rep = {"command": ["sbx", "a"], "time": 100.0}
        self.assertTrue(G["is_fresh"](rep, ["sbx", "a"], 99.0))
        self.assertFalse(G["is_fresh"](rep, ["sbx", "a"], 101.0))                         # an older report of the same program
        self.assertFalse(G["is_fresh"](rep, ["sbx", "b"], 99.0)); self.assertFalse(G["is_fresh"](None, ["sbx", "a"], 0))
        self.assertFalse(G["is_fresh"]({"command": ["sbx", "a"], "time": None}, ["sbx", "a"], 0))


class Text(Base):
    def test_what_is_shown(self):
        G, H = self.G, self.home
        self.assertEqual(G["counts_text"]({"path": "/x", "r": 3, "x": 1, "w": 2, "err": {}}), "read 3 · write 2 · run 1")
        self.assertEqual(G["errors_text"]({"ENOENT": 2, "EACCES": 5, "EIO": 2}), "EACCES ×5, EIO ×2, ENOENT ×2")
        self.assertEqual(G["row_subtitle"]({"path": "/x", "p": 2, "err": {"ENOENT": 2}}), "probe 2 · errors: ENOENT ×2")
        self.assertEqual(G["row_subtitle"]({"path": "/x", "err": {}}), "")
        self.assertEqual(G["tilde"](f"{H}/.ssh/config"), "~/.ssh/config"); self.assertEqual(G["tilde"]("/etc/passwd"), "/etc/passwd")
        i = {"calls": 10, "processes": 2, "dropped": 3, "unresolved": 0}
        self.assertEqual(G["summary_text"]({"info": i}), "10 file calls from 2 threads, 3 paths not kept (too many)")
        self.assertIn("7 events lost", G["summary_text"]({"info": {**i, "lost": 7}}))
        rep = {"name": "fire\x1b[31mfox", "exit": 1, "time": 0.0}
        self.assertNotIn("\x1b", G["title_text"](rep)); self.assertIn("fire?[31mfox · exit 1 · ", G["title_text"](rep))
        self.assertEqual(G["title_text"]({"name": "x", "exit": None, "time": None}), "x")

    def test_search_matches_every_word_in_the_path_as_it_is_or_with_a_tilde(self):
        G, H = self.G, self.home; p = f"{H}/.ssh/Config"
        for q, want in (("", True), ("config", True), ("SSH config", True), ("~/.ssh", True), ("~/.ssh  conf", True), ("ssh nothing", False), ("/etc", False)):
            self.assertEqual(G["matches"](p, G["search_terms"](q)), want, q)
        self.assertTrue(G["matches"]("/etc/shadow", ["denied"], "denied")); self.assertFalse(G["matches"]("/etc/shadow", ["hidden"], "denied"))

    def test_copying_never_puts_control_characters_on_the_clipboard(self):
        G = self.G
        self.assertEqual(G["copyable"]("/tmp/plain name"), ("/tmp/plain name", False))
        self.assertEqual(G["copyable"]("/tmp/a\x1b]0;x\x07\nb"), ("/tmp/a?]0;x??b", True))
        win = mock.MagicMock()
        G["Win"].copy_path(win, "/tmp/ok"); win.copy_text.assert_called_with("/tmp/ok", "Copied the path")
        G["Win"].copy_path(win, "/tmp/a\nb"); win.copy_text.assert_called_with("/tmp/a?b", "Copied, with control characters replaced by ?")

    def test_a_program_name_is_the_one_sbx_trace_gives_its_report(self):
        G = self.G
        for words, want in ((["firefox"], "firefox"), (["--net=none", "--here", "python3", "s.py"], "python3"), (["/usr/bin/firefox", "-p"], "firefox"),
                            (["--net=none"], None), (["a b"], None), (["--x", "-weird"], None)):
            self.assertEqual(G["report_name"](words), want, words)
            if want: self.assertEqual(self.T.name_of(["sbx"] + words), want)               # the same rule as sbx-trace
        self.assertEqual(G["split_command"]("firefox  --private-window 'a b'"), ["firefox", "--private-window", "a b"])
        self.assertIsNone(G["split_command"]("  ")); self.assertIsNone(G["split_command"]("echo 'unclosed"))


class Programs(Base):
    @unittest.skipUnless(os.path.exists(SBX), "needs the sbx script of this repository")
    def test_it_reads_the_list_the_real_sbx_prints(self):
        G = self.G; h = f"{self.tmp}/sbxhome"
        os.makedirs(f"{h}/.config/sbx/apps"); self.put(f"{h}/.config/sbx/apps/foo.conf", "net=0\n")
        for n in ("firefox", "my.game"): os.makedirs(f"{h}/.sbx-homes/{n}")
        run = lambda *a: subprocess.run([sys.executable, SBX, "list", *a], capture_output=True, text=True, env=dict(os.environ, SBX_HOME=h)).stdout
        self.assertEqual(G["parse_list"](run()), ["foo"])                                 # "2 more only run from the terminal ..." is a message
        self.assertEqual(sorted(G["parse_list"](run("--all"))), ["firefox", "foo", "my.game"])
        self.assertEqual(G["parse_list"]("No programs yet. Start one with: sbx PROGRAM\n"), [])
        self.assertEqual(G["parse_list"]("a   running (2)   x\n3 more only run from the terminal\n-bad idle\n"), ["a"])

    def test_the_choices_are_the_private_homes_and_the_programs_sbx_list_shows(self):
        G = self.G
        for n in ("b", "a"): os.makedirs(f"{G['HOMES']}/{n}")
        self.put(f"{G['HOMES']}/file", ""); os.makedirs(f"{G['HOMES']}/.hidden dir")
        done = subprocess.CompletedProcess([], 0, "x   idle\n2 more only run from the terminal\n", "")
        with mock.patch.object(subprocess, "run", return_value=done): self.assertEqual(G["program_names"](), ["a", "b", "x"])
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("no sbx")): self.assertEqual(G["program_names"](), ["a", "b"])


class Starting(Base):
    def ready(self, **which):
        """Everything start_problem looks for is there, apart from what WHICH says (name: path, or None for missing)."""
        found = {"pkexec": "/usr/bin/pkexec", "firefox": "/usr/bin/firefox", "sudo": "/usr/bin/sudo"}; found.update(which)
        self.G["trace_cmd"] = lambda: "/x/sbx-trace"; self.G["sbx_cmd"] = lambda: "/bin/sh"
        for p in (mock.patch.object(shutil, "which", side_effect=lambda n: found.get(n)), mock.patch.object(os, "geteuid", return_value=1000)):
            p.start(); self.addCleanup(p.stop)

    def test_it_says_why_it_cannot_start_before_asking_for_a_password(self):
        G = self.G; self.ready()
        self.assertIsNone(G["start_problem"](["firefox"])); self.assertIsNone(G["start_problem"](["--net=none", "firefox"]))
        self.assertIsNone(G["start_problem"](["/opt/app/bin/app"]))                        # a path is sbx's to look at
        self.assertIn("not in your PATH", G["start_problem"](["--net=none", "nothere"]))
        G["trace_cmd"] = lambda: None; self.assertIn("sbx-trace was not found", G["start_problem"](["firefox"]))
        self.ready(pkexec=None)                                                            # sudo is there, but the window has no terminal for it
        self.assertIn("pkexec", G["start_problem"](["firefox"])); self.assertIn("no terminal", G["start_problem"](["firefox"]))
        self.ready(); G["sbx_cmd"] = lambda: "/nonexistent/sbx"; self.assertIn("sbx was not found", G["start_problem"](["firefox"]))
        self.ready()
        with mock.patch.object(os, "geteuid", return_value=0): self.assertIn("not as root", G["start_problem"](["firefox"]))

    def test_the_traced_command_is_sbx_with_the_typed_words(self):
        G = self.G; G["trace_cmd"] = lambda: "/x/sbx-trace"; G["sbx_cmd"] = lambda: "/usr/bin/sbx"
        self.assertEqual(G["trace_command"](["--net=none", "python3", "a b.py"]),
                         (["/usr/bin/sbx", "--net=none", "python3", "a b.py"], ["/x/sbx-trace", "--", "/usr/bin/sbx", "--net=none", "python3", "a b.py"]))

    def test_sbx_trace_is_found_next_to_the_window_before_the_path(self):
        G = self.G
        self.assertEqual(G["trace_cmd"](), f"{HERE}/sbx-trace")
        with mock.patch.object(os.path, "isfile", return_value=False), mock.patch.object(shutil, "which", return_value="/usr/bin/sbx-trace"):
            self.assertEqual(G["trace_cmd"](), "/usr/bin/sbx-trace")


class Running(Base):
    def script(self, body):
        p = f"{self.tmp}/prog.py"
        with open(p, "w") as f: f.write("import os, sys, time\n" + body)
        return [sys.executable, p]

    def test_sbx_trace_gets_no_terminal_so_it_asks_for_root_through_pkexec_and_its_output_stays_small(self):
        G = self.G
        r = G["Run"](self.script("""
sys.stdout.write("x" * 3_000_000 + "\\n")
print("stdin=" + os.readlink("/proc/self/fd/0"), "leader=" + str(os.getsid(0) == os.getpid()), "tty=" + str(sys.stdin.isatty()), "\\x1b[31mred")
sys.exit(3)"""))
        rc, out = r.wait()
        self.assertEqual(rc, 3); self.assertIn("stdin=/dev/null leader=True tty=False ?[31mred", out)
        self.assertNotIn("\x1b", out); self.assertLessEqual(len(r.tail), G["LOG_KEEP"])

    def test_stop_ends_it(self):
        G = self.G
        r = G["Run"](self.script("print('up', flush=True)\ntime.sleep(60)"))
        guard = threading.Timer(15, lambda: r.proc.kill()); guard.start(); self.addCleanup(guard.cancel)
        while "up" not in r.text(): time.sleep(0.05)
        r.stop(); rc, _ = r.wait(); self.assertEqual(rc, -signal.SIGTERM)
        r.stop()                                                                          # already over: nothing happens

    def test_a_helper_the_program_leaves_behind_does_not_keep_the_window_waiting(self):
        G = self.G
        r = G["Run"](self.script("import subprocess\nprint(subprocess.Popen(['/bin/sleep', '30']).pid, flush=True)"))
        start = time.time(); rc, out = r.wait()
        try: self.assertEqual(rc, 0); self.assertLess(time.time() - start, 8)
        finally:
            try: os.kill(int(out.split()[0]), signal.SIGKILL)
            except (ValueError, IndexError, OSError): pass

    def test_a_trace_that_did_not_start_is_not_taken_for_one_that_ran(self):
        G = self.G; self.fake_trace("fail")
        old = {"name": "x", "command": ["/usr/bin/sbx", "firefox"], "exit": 0, "time": time.time() - 100, "blocked": [], "home": {"rows": []}, "system": {"rows": []}}
        self.put(f"{self.tmp}/fake/r.json", json.dumps(old))                       # the report of an earlier run of the same program
        cmd, argv = G["trace_command"](["firefox"]); started = time.time()
        rc, out = G["Run"](argv).wait()
        rep, why = G["load_report"](None)
        self.assertEqual(rc, 1); self.assertFalse(G["is_fresh"](rep, cmd, started))
        self.assertIn("the trace did not start", G["failure_text"](out))

    def test_a_program_that_fails_is_still_a_trace_that_worked(self):
        G = self.G; self.fake_trace("ok")
        cmd, argv = G["trace_command"](["firefox", "--bad"]); started = time.time()
        rc, out = G["Run"](argv).wait()
        rep, why = G["load_report"](None)
        self.assertEqual(rc, 3); self.assertTrue(G["is_fresh"](rep, cmd, started)); self.assertEqual(rep["blocked"][0]["path"], "/etc/shadow")
        self.assertEqual(G["failure_text"](""), "No output.")
        self.assertLessEqual(len(G["failure_text"]("\n".join(f"line {i}" for i in range(500)))), 700)


class Window(unittest.TestCase):
    """The window's own code, with GTK stubbed and a mock for self (as test_sbx.py does for sbx-gui)."""
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="sbx-trace-gui-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.G = load_gui(self.tmp)

    def win(self):
        w = mock.MagicMock(); w.rows = {}; w.closing = False; w.run = None
        return w

    def test_the_page_builds(self):                                                        # no GTK, but a typo or a missing name would show here
        w = self.win(); self.G["Win"].build_page(w)
        w.entry.connect.assert_any_call("entry-activated", mock.ANY)

    def test_every_row_is_plain_text_and_has_a_copy_button_for_the_real_path(self):
        G, w = self.G, self.win(); Adw, Gtk = G["Adw"], G["Gtk"]
        Adw.ActionRow.reset_mock(); Gtk.Button.reset_mock()
        evil = "/tmp/<b>x</b>&amp;\x1b[31m\nname"
        G["Win"].path_row(w, evil, "read 1", "denied")
        kw = Adw.ActionRow.call_args.kwargs
        self.assertEqual(kw["title"], "/tmp/<b>x</b>&amp;?[31m?name"); self.assertIs(kw["use_markup"], False)
        self.assertEqual(Gtk.Button.call_args.kwargs["icon_name"], "edit-copy-symbolic")
        Gtk.Button.return_value.connect.call_args[0][1](); w.copy_path.assert_called_once_with(evil)     # what is copied is decided by copy_path, from the real name
        self.assertIn("error", Gtk.Label.call_args.kwargs["css_classes"])                  # denied is shown as an error

    def test_a_sensitive_path_is_marked_in_words_and_with_an_icon_and_a_plain_one_is_not(self):
        G, H = self.G, self.tmp; Adw = G["Adw"]
        for p in (f"{H}/.ssh", f"{H}/.ssh/id_ed25519", f"{H}/.local/share/keyrings/login.keyring", "/etc/shadow", "/etc/shadow-", "/etc/sudoers.d/x", "/etc/ssh/ssh_host_rsa_key"):
            self.assertTrue(G["sensitive"](p), p)
        for p in (f"{H}/.sshx", f"{H}/.local/share", f"{H}/.local/share/applications/a", H, "/etc/passwd", "/etc/ssh/ssh_config", "/home/other/.ssh/x", f"{H}/x/.ssh"):
            self.assertFalse(G["sensitive"](p), p)
        w = mock.MagicMock()
        for path, sub, want in ((f"{H}/.ssh/config", "read 1", "sensitive · read 1"), (f"{H}/.ssh/config", "", "sensitive"), ("/tmp/a", "read 1", "read 1")):
            Adw.ActionRow.reset_mock(); G["Win"].path_row(w, path, sub)
            self.assertEqual(Adw.ActionRow.call_args.kwargs["subtitle"], want)
            self.assertEqual(Adw.ActionRow.return_value.add_prefix.called, want.startswith("sensitive"))
        made = lambda it: ("row", it["path"]); Adw.ExpanderRow.reset_mock()
        G["Win"].entry_row(w, (f"{H}/.ssh", [{"path": f"{H}/.ssh/a", "r": 1, "err": {}}, {"path": f"{H}/.ssh/b", "r": 1, "err": {}}]), made, G["folder_subtitle"], False)
        self.assertTrue(Adw.ExpanderRow.call_args.kwargs["subtitle"].endswith("holds sensitive paths")); Adw.ExpanderRow.return_value.add_prefix.assert_called()

    def test_a_group_draws_a_few_rows_and_says_how_many_more_there_are(self):
        G, w = self.G, self.win(); Adw = G["Adw"]; group = mock.MagicMock(); made = []
        def row(it): made.append(it); return mock.MagicMock()
        Adw.ActionRow.reset_mock()
        G["Win"].fill_group(w, group, "Your home", 250, 250, list(range(250)), row, False)
        self.assertEqual(made, list(range(G["RENDER_MAX"]))); self.assertEqual(group.add.call_count, G["RENDER_MAX"] + 1)
        self.assertEqual(Adw.ActionRow.call_args.kwargs["title"], f"… {250 - G['RENDER_MAX']} more")
        group.set_title.assert_called_with("Your home (250)"); group.set_visible.assert_called_with(True)
        old = list(w.rows[group]); group.reset_mock()
        G["Win"].fill_group(w, group, "Your home", 400, 3, list(range(3)), row, True)         # a search: the count of what matched, of all of them
        self.assertEqual([c[0][0] for c in group.remove.call_args_list], old)              # the rows it drew before are taken out first
        group.set_title.assert_called_with("Your home (3 of 400)"); self.assertEqual(group.add.call_count, 3)
        group.reset_mock(); G["Win"].fill_group(w, group, "Your home", 400, 0, [], row, True); group.set_visible.assert_called_with(False)
        Adw.ActionRow.reset_mock(); G["Win"].fill_group(w, group, "The system", 3100, 3000, list(range(3000)), row, False)    # sbx-trace saves 3000 of 3100
        self.assertEqual(Adw.ActionRow.call_args.kwargs["title"], "… 2900 more")
        self.assertEqual(Adw.ActionRow.call_args.kwargs["subtitle"], "Search to narrow it down. 100 more files are not in the saved report.")
        Adw.ActionRow.reset_mock(); G["Win"].fill_group(w, group, "The system", 120, 100, list(range(100)), row, False)
        self.assertEqual(Adw.ActionRow.call_args.kwargs["title"], "… 20 more files")

    def test_start_does_nothing_without_a_command_and_stops_at_a_problem(self):
        G, w = self.G, self.win()
        w.entry.get_text.return_value = "  "; G["Win"].start_trace(w); w.toast.assert_called_once(); w.alert.assert_not_called()
        w.entry.get_text.return_value = "firefox"; G["start_problem"] = lambda words: "no pkexec"
        G["Run"] = mock.MagicMock(); G["Win"].start_trace(w)
        w.alert.assert_called_with("Can't start the trace", "no pkexec"); G["Run"].assert_not_called()
        w.run = object(); w.alert.reset_mock(); G["Win"].start_trace(w); w.alert.assert_not_called()      # one trace at a time

    def test_start_runs_sbx_trace_for_sbx_and_the_words_and_hands_the_result_to_finished(self):
        G, w = self.G, self.win()
        G["start_problem"] = lambda words: None; G["trace_cmd"] = lambda: "/x/sbx-trace"; G["sbx_cmd"] = lambda: "/usr/bin/sbx"
        run = mock.MagicMock(); run.wait.return_value = (3, "out"); G["Run"] = mock.MagicMock(return_value=run)
        rep = {"command": ["/usr/bin/sbx", "firefox", "-p"]}; G["load_report"] = lambda name=None: (rep, "")
        G["threading"] = types.SimpleNamespace(Thread=lambda target, daemon=None: types.SimpleNamespace(start=target))
        G["GLib"].idle_add.side_effect = lambda f: f()
        w.entry.get_text.return_value = "firefox -p"
        G["Win"].start_trace(w)
        G["Run"].assert_called_once_with(["/x/sbx-trace", "--", "/usr/bin/sbx", "firefox", "-p"]); w.set_busy.assert_called_once()
        cmd, started = w.finished.call_args[0][1], w.finished.call_args[0][2]
        self.assertEqual(cmd, ["/usr/bin/sbx", "firefox", "-p"]); self.assertLessEqual(started, time.time())
        self.assertEqual(w.finished.call_args[0][3:], (3, "out", rep, ""))

    def test_finished_shows_the_report_or_says_what_went_wrong(self):
        G, w = self.G, self.win(); run = object(); w.run = run
        rep = {"command": ["sbx", "a"], "time": 100.0}
        G["Win"].finished(w, run, ["sbx", "a"], 99.0, 3, "hello", rep, "")
        self.assertIsNone(w.run); self.assertIs(w.report, rep); w.fill_report.assert_called_once()
        w.toast.assert_called_with("Trace finished (the program exited with 3)"); w.alert.assert_not_called()
        w.run = run; w.report = None; w.fill_report.reset_mock()
        G["Win"].finished(w, run, ["sbx", "a"], 101.0, 1, "sbx-trace: the trace did not start: ~~~~", rep, "")     # the report is an older one
        self.assertIsNone(w.report); w.fill_report.assert_not_called()
        w.alert.assert_called_with("The trace did not finish", "sbx-trace: the trace did not start: ~~~~")
        G["Win"].finished(w, run, ["sbx", "a"], 101.0, 1, "", None, "no report yet"); w.alert.assert_called_with("The trace did not finish", "no report yet")
        w.alert.reset_mock(); w.closing = True; G["Win"].finished(w, run, ["sbx", "a"], 99.0, 0, "", rep, ""); w.alert.assert_not_called()     # window already closed

    def test_closing_while_tracing_asks_first_and_then_stops_the_trace(self):
        G, w = self.G, self.win(); Adw = G["Adw"]
        self.assertFalse(G["Win"].on_close(w))                                              # nothing running: just close
        w.run = mock.MagicMock(); Adw.AlertDialog.reset_mock()
        self.assertTrue(G["Win"].on_close(w)); Adw.AlertDialog.return_value.choose.assert_called_once()
        w.stop_and_close = lambda: G["Win"].stop_and_close(w); G["Win"].stop_and_close(w)
        self.assertTrue(w.closing); w.run.stop.assert_called_once(); w.close.assert_called_once()
        self.assertFalse(G["Win"].on_close(w))                                              # the close that follows is let through

    def test_the_noise_is_a_count_and_a_search_with_no_hits_says_so(self):
        G, w = self.G, self.win()
        rep = G["clean_report"]({"name": "a", "blocked": [], "home": {"total": 1, "rows": [{"path": "/home/u/x"}]}, "system": {"total": 0, "rows": []},
                                 "noise": {"total": 12, "rows": [{"path": "/proc/1"}]}})
        w.report = rep; w.search.get_text.return_value = ""; w.grouped.get_active.return_value = True; w.only_errors.get_active.return_value = False
        G["Win"].fill_report(w)
        w.noise.set_description.assert_called_with("/proc, /sys, and /dev and /run without errors: 12 paths not shown")
        self.assertFalse(hasattr(rep["noise"], "rows"))                                     # no rows of it are kept
        w.search.get_text.return_value = "zzz"; w.blocked.get_visible.return_value = w.home.get_visible.return_value = w.system.get_visible.return_value = False
        G["Win"].fill_report(w); w.head_row.set_subtitle.assert_called_with("Nothing matches “zzz”.")
        w.search.get_text.return_value = ""; w.only_errors.get_active.return_value = True
        G["Win"].fill_report(w); w.head_row.set_subtitle.assert_called_with("No path has an error.")


class Comparison(Base):
    def test_a_foreign_comparison_cannot_break_it(self):
        G = self.G
        for bad in ([], "x", None, {}, {"fixed": "x", "new_blocked": []}, {"fixed": [], "new_blocked": {}}):
            with self.assertRaises(ValueError, msg=repr(bad)): G["clean_diff"](bad)
        c = G["clean_diff"]({"fixed": [{"path": "/a", "now": "works"}, {"path": "/b", "now": "<b>x</b>"}, {"path": 3}, "x"],
                             "new_blocked": [{"path": "/c", "kind": "hidden", "n": 2}, {"path": "/d", "kind": "weird", "n": -1}, {"path": None}],
                             "still_blocked": True, "before": True})
        self.assertEqual(c["fixed"], [{"path": "/a", "now": "works"}, {"path": "/b", "now": "not touched"}])
        self.assertEqual(c["new_blocked"], [{"path": "/c", "kind": "hidden", "n": 2}, {"path": "/d", "kind": "denied", "n": 1}])
        self.assertEqual((c["still"], c["before"]), (0, None))
        self.assertEqual(G["compare_text"](c), "2 no longer blocked, 2 newly blocked, 0 still blocked")
        self.assertIn("compared with the run of", G["compare_text"]({**c, "before": 100.0}))

    def test_it_reads_what_the_real_sbx_trace_compares(self):
        G, H = self.G, self.home
        G["trace_cmd"] = lambda: f"{HERE}/sbx-trace"
        v1 = self.real_report(); v2 = json.loads(json.dumps(v1))
        v1["time"], v2["time"] = 1.0, 2.0
        v2["blocked"] = [b for b in v2["blocked"] if b["path"] != "/etc/shadow"]
        v2["system"]["rows"] = [r for r in v2["system"]["rows"] if r["path"] != "/etc/shadow"]
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": f"{self.tmp}/state", "HOME": H}):
            c, why = G["load_diff"]("foo"); self.assertIsNone(c); self.assertIn("no report yet", why)
            self.T.save("foo", v1)
            c, why = G["load_diff"]("foo"); self.assertIsNone(c); self.assertIn("no earlier report", why)             # one run is nothing to compare
            self.T.save("foo", v2)
            c, why = G["load_diff"]("foo")
        self.assertEqual(why, ""); self.assertEqual([(x["path"], x["now"]) for x in c["fixed"]], [("/etc/shadow", "not touched")]); self.assertEqual(c["before"], 1.0)
        self.assertEqual(G["load_diff"]("../x"), (None, "")); self.assertEqual(G["load_diff"](None), (None, ""))

    def test_the_window_draws_it_and_ignores_one_for_an_old_report(self):
        G = self.G
        w = mock.MagicMock(); w.rows = {}; w.closing = False
        c = {"fixed": [{"path": "/a", "now": "works"}], "new_blocked": [], "still": 3, "before": None}
        w.compare = c; w.compare_group = mock.MagicMock()
        G["Win"].fill_compare(w); w.compare_group.set_visible.assert_called_with(True); self.assertEqual(w.compare_group.add.call_count, 1)    # one folder row: no newly blocked
        w.compare_group.set_description.assert_called_with("1 no longer blocked, 0 newly blocked, 3 still blocked")
        w.compare = None; w.compare_group.reset_mock(); G["Win"].fill_compare(w); w.compare_group.set_visible.assert_called_with(False)
        rep, other = object(), object()
        w.report = rep; G["Win"].got_diff(w, other, c); self.assertIsNone(w.compare); w.fill_report.assert_not_called()
        G["Win"].got_diff(w, rep, c); self.assertIs(w.compare, c); w.fill_report.assert_called_once()


class Folders(Base):
    """What keeps a long report readable: one line per folder, and only the paths that had a problem."""
    def test_a_file_is_listed_under_its_first_two_folders(self):
        G, H = self.G, self.home
        for path, want in ((f"{H}/.config/mozilla/firefox/p/x.db", f"{H}/.config/mozilla"), (f"{H}/.ssh/config", f"{H}/.ssh"), (f"{H}/notes.txt", H),
                           ("/usr/lib/x86_64-linux-gnu/libc.so.6", "/usr/lib"), ("/etc/passwd", "/etc"), ("/passwd", "/")):
            self.assertEqual(G["folder_of"](path), want, path)
        self.assertEqual((G["folder_title"](f"{H}/.ssh"), G["folder_title"](H), G["folder_title"]("/etc")), ("~/.ssh", "~", "/etc"))

    def test_folders_keep_the_order_of_their_heaviest_file_and_the_flat_list_is_unchanged(self):
        G, H = self.G, self.home
        items = [{"path": p} for p in (f"{H}/.cache/a", "/etc/x", f"{H}/.cache/b", "/etc/y", "/usr/lib/z")]
        grouped = G["layout"](items, True)
        self.assertEqual([f for f, _ in grouped], [f"{H}/.cache", "/etc", "/usr/lib"])
        self.assertEqual([[i["path"] for i in its] for _, its in grouped], [[f"{H}/.cache/a", f"{H}/.cache/b"], ["/etc/x", "/etc/y"], ["/usr/lib/z"]])
        self.assertEqual(G["layout"](items, False), [(None, [i]) for i in items])

    def test_a_folder_says_what_is_in_it(self):
        G = self.G
        rows = [{"path": "/etc/a", "r": 3, "err": {}}, {"path": "/etc/b", "r": 2, "p": 2, "err": {"ENOENT": 2}}, {"path": "/etc/c", "p": 1, "err": {"ENOENT": 1, "EACCES": 1}}]
        self.assertEqual(G["folder_subtitle"](rows), "3 files · read 5 · probe 3 · errors: ENOENT ×3, EACCES ×1")
        self.assertEqual(G["blocked_subtitle"]([{"kind": "hidden"}, {"kind": "denied"}, {"kind": "hidden"}]), "denied 1 · hidden 2")

    def test_a_folder_row_opens_to_its_files_and_copies_the_folder(self):
        G, H = self.G, self.home; w = mock.MagicMock(); Adw, Gtk = G["Adw"], G["Gtk"]
        made = lambda it: ("row", it["path"])
        self.assertEqual(G["Win"].entry_row(w, (None, [{"path": "/a"}]), made, G["folder_subtitle"], False), ("row", "/a"))      # not grouped
        self.assertEqual(G["Win"].entry_row(w, (f"{H}/d", [{"path": f"{H}/d/f"}]), made, G["folder_subtitle"], False), ("row", f"{H}/d/f"))   # one file: just the file
        Adw.ExpanderRow.reset_mock(); Gtk.Button.reset_mock()
        items = [{"path": f"{H}/.cache/<b>{i}", "r": 1, "err": {}} for i in range(130)]
        row = G["Win"].entry_row(w, (f"{H}/.cache", items), made, G["folder_subtitle"], True)
        kw = Adw.ExpanderRow.call_args.kwargs
        self.assertEqual((kw["title"], kw["subtitle"], kw["use_markup"]), ("~/.cache", "130 files · read 130", False))
        row = Adw.ExpanderRow.return_value
        self.assertEqual(row.add_row.call_count, G["RENDER_MAX"] + 1)                                # the files, then a row for the rest
        self.assertEqual(G["Adw"].ActionRow.call_args.kwargs["title"], "… 30 more"); row.set_expanded.assert_called_with(True)
        Gtk.Button.return_value.connect.call_args[0][1](); w.copy_path.assert_called_once_with(f"{H}/.cache")
        G["Win"].entry_row(w, (f"{H}/.cache", items[:2]), made, G["folder_subtitle"], False); row.set_expanded.assert_called_with(False)

    def test_the_report_is_grouped_by_default_and_can_be_cut_down_to_the_errors(self):
        G, H = self.G, self.home; w = mock.MagicMock()
        rep = G["clean_report"](json.loads(json.dumps(self.real_report())))
        w.report = rep; w.search.get_text.return_value = ""; w.grouped.get_active.return_value = True; w.only_errors.get_active.return_value = False
        calls = {}
        w.fill_group.side_effect = lambda group, title, total, matched, entries, make, narrowed: calls.update({title: (total, matched, entries, narrowed)})
        G["Win"].fill_report(w)
        total, matched, entries, narrowed = calls["Your home"]
        self.assertEqual((total, matched, narrowed), (rep["home"]["total"], rep["home"]["total"], False)); self.assertEqual(entries[0][0], f"{H}/.config/foo")
        self.assertEqual(calls["Blocked"][1], len(rep["blocked"])); w.view.set_visible.assert_called_with(True)
        w.grouped.get_active.return_value = False; G["Win"].fill_report(w)
        self.assertTrue(all(f is None for f, _ in calls["Your home"][2]))                              # one entry per file
        w.only_errors.get_active.return_value = True; G["Win"].fill_report(w)
        total, matched, entries, narrowed = calls["Your home"]
        self.assertTrue(narrowed); self.assertLess(matched, total); self.assertTrue(all(it["err"] for _, its in entries for it in its))
        self.assertEqual(calls["Blocked"][1], len(rep["blocked"]))                                     # what was blocked is never cut away


class Whole(unittest.TestCase):
    def test_it_is_standalone_and_leaves_sbx_trace_alone(self):
        with open(GUI) as f: src = f.read()
        self.assertTrue(os.access(GUI, os.X_OK))
        self.assertIn('gi.require_version("Gtk", "4.0")', src); self.assertIn('gi.require_version("Adw", "1")', src)
        self.assertIsNone(re.search(r"^\s*(import|from)\s+sbx", src, re.M))                    # it only runs sbx-trace, as a program
        for word in ("importlib", "exec(", "eval("): self.assertNotIn(word, src)
        for word in ("sudo", "pkexec"): self.assertNotIn(f'"{word}"', src.replace('shutil.which("pkexec")', ""))     # it never starts a root helper itself


if __name__ == "__main__":
    unittest.main(verbosity=1)
