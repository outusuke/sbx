#| 40_window: The GTK window (Win): every page of the app.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---- window ----
class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="sbx", default_width=600, default_height=760)
        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)
        self.main = self.main_page()
        self.nav.push(self.main)
        self.nav.connect("popped", lambda *_: self.fill_apps())

    def page(self, title, content, end=()):
        view = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        for b in end: bar.pack_end(b)
        view.add_top_bar(bar)
        view.set_content(content)
        return Adw.NavigationPage(child=view, title=title)

    def alert(self, heading, body="", child=None):
        d = Adw.AlertDialog(heading=heading, body=body)
        if child: d.set_extra_child(child)
        d.add_response("ok", "OK")
        d.present(self)

    def ask(self, heading, body, label, then):
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_responses("cancel", "Cancel", "go", label)
        d.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")
        d.choose(self, None, lambda dlg, res: dlg.choose_finish(res) == "go" and then())

    # -- main page --
    def main_page(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(description="Settings for programs started with sbx. Changes apply the next time a program starts.")
        row = Adw.ActionRow(title="Default settings", subtitle="Used by every program unless it has its own", activatable=True)
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        row.connect("activated", lambda *_: self.open(None))
        g.add(row)
        page.add(g)
        mg = Adw.PreferencesGroup()
        menu = Adw.SwitchRow(title="Show sbx in the app menu", subtitle="Adds a launcher for this window to your application menu.")
        menu.set_active(menu_entry_exists())

        def on_menu(r, _p):
            try:
                set_menu_entry(r.get_active())
                self.toasts.add_toast(Adw.Toast(title="Added to the app menu" if r.get_active() else "Removed from the app menu"))
            except (OSError, subprocess.SubprocessError) as e:
                self.toasts.add_toast(Adw.Toast(title=f"Could not change it: {getattr(e, 'strerror', None) or e}"))
        menu.connect("notify::active", on_menu)
        mg.add(menu)
        page.add(mg)
        self.apps_group = Adw.PreferencesGroup(title="Programs")
        self.app_rows = []
        page.add(self.apps_group)
        self.fill_apps()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add a program")
        add.connect("clicked", lambda *_: self.add_program())
        ts = Gtk.Button(icon_name="utilities-terminal-symbolic", tooltip_text="Troubleshooting")
        ts.connect("clicked", lambda *_: self.open_trouble(None))
        return self.page("sbx", page, [add, ts])

    def open_trouble(self, scope):
        """Health checks (sbx doctor) and the recent log, for one program or all."""
        page = Adw.PreferencesPage()
        hg = Adw.PreferencesGroup(title="Health", description="The checks of sbx doctor.")
        hl = Gtk.Label(xalign=0, wrap=True, selectable=True, margin_top=6, margin_bottom=6, css_classes=["monospace"])
        hg.add(hl)
        page.add(hg)
        sg = Adw.PreferencesGroup(title="Suggestions", description="Based on this program's last run, which ended with an error. "
                                  "Nothing changes until you press the button; it applies the next time the program starts.")
        sg.set_visible(False)
        sug_rows = []
        page.add(sg)

        def show_hints():
            for r in sug_rows: sg.remove(r)
            sug_rows.clear()
            hints = log_hints(log_lines(scope), effective_switches(scope)) if scope else []
            for key, want, title, why in hints:
                row = Adw.ActionRow(title=title, subtitle=why, use_markup=False)
                btn = Gtk.Button(label="Turn on" if want else "Turn off", valign=Gtk.Align.CENTER)

                def apply(_b, key=key, want=want):
                    try:
                        set_key(scope, key, want)
                        self.toasts.add_toast(Adw.Toast(title="Saved. It applies the next time the program starts."))
                    except OSError as e:
                        self.toasts.add_toast(Adw.Toast(title=f"Could not save: {e.strerror or e}"))
                    show_hints()
                btn.connect("clicked", apply)
                row.add_suffix(btn)
                sg.add(row); sug_rows.append(row)
            sg.set_visible(bool(hints))
        lg = Adw.PreferencesGroup(title="Recent activity" + (f" · {scope}" if scope else ""),
                                  description="Starts, exits, warnings, and what programs print when they have no terminal. Paste this when asking for help.")
        tv = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                          left_margin=8, right_margin=8, top_margin=8, bottom_margin=8)
        lg.add(Gtk.ScrolledWindow(min_content_height=320, child=tv, css_classes=["card"]))
        page.add(lg)

        def refresh(*_):
            try:
                r = subprocess.run([sbx_cmd(), "doctor"], capture_output=True, text=True, timeout=20)
                out = (r.stdout + r.stderr).strip()
            except (OSError, subprocess.TimeoutExpired) as e:
                out = f"Couldn't run sbx doctor: {e}"
            hl.set_text(out or "No output. Is sbx installed?")
            tv.get_buffer().set_text(log_tail(scope))
            show_hints()
        rb = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        rb.connect("clicked", refresh)
        refresh()
        self.nav.push(self.page("Troubleshooting", page, [rb]))

    def fill_apps(self):
        for r in self.app_rows: self.apps_group.remove(r)
        self.app_rows.clear()
        for n in app_names():
            r = Adw.ActionRow(title=n, subtitle=summary(n), activatable=True, use_markup=False)
            r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            r.connect("activated", lambda _r, n=n: self.open(n))
            self.apps_group.add(r); self.app_rows.append(r)
        if not self.app_rows:
            r = Adw.ActionRow(title="No programs yet", subtitle="Press + to add one. Programs also show up here after their first run.")
            self.apps_group.add(r); self.app_rows.append(r)

    def add_program(self):
        entry = Gtk.Entry(placeholder_text="firefox", activates_default=True)
        d = Adw.AlertDialog(heading="Add a program", body="The name you type to start it, for example firefox.")
        d.set_extra_child(entry)
        d.add_responses("cancel", "Cancel", "add", "Add")
        d.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("add"); d.set_close_response("cancel")

        def done(dlg, res):
            if dlg.choose_finish(res) != "add": return
            name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(entry.get_text().strip())).lstrip("._-")[:64]
            if not name: return
            if not os.path.exists(path_for(name)): save(name, [])
            self.fill_apps(); self.open(name)
        d.choose(self, None, done)

    def open(self, scope):
        CURRENT[:] = [scope]
        self.nav.push(self.program_page(scope))

    # -- program page --
    def program_page(self, scope):
        page = Adw.PreferencesPage()
        page_rows = []
        for title, rows in GROUPS:
            g = Adw.PreferencesGroup(title=title)
            for key, t, s, _d in rows: g.add(self.switch_row(scope, key, page_rows))
            page.add(g)
        for grp in self.folders_group(scope): page.add(grp)
        if scope is not None:
            rg = Adw.PreferencesGroup(title="Run")
            run = Adw.ActionRow(title=f"Run {scope}", subtitle="Starts it with these settings", activatable=True, use_markup=False)
            run.add_suffix(Gtk.Image.new_from_icon_name("media-playback-start-symbolic"))
            run.connect("activated", lambda *_: self.run_program(scope))
            cmd = Adw.ActionRow(title="Show command", subtitle="The bwrap command sbx would run", activatable=True)
            cmd.connect("activated", lambda *_: self.show_command(scope))
            lg = Adw.ActionRow(title="Show log", subtitle="Starts, exits and errors of this program", activatable=True)
            lg.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            lg.connect("activated", lambda *_: self.open_trouble(scope))
            rg.add(run); rg.add(cmd); rg.add(lg)
            page.add(rg)
            mg = Adw.PreferencesGroup(title="App menu")
            mg.add(self.app_menu_row(scope))
            page.add(mg)
        dg = Adw.PreferencesGroup(title="Reset")
        rs = Adw.ActionRow(title="Reset settings", subtitle="Removes everything above for " + ("all programs" if scope is None else scope),
                           activatable=True, use_markup=False)
        rs.connect("activated", lambda *_: self.ask("Reset settings?", "Folders and switches go back to the defaults. Program data is not touched.", "Reset",
                                                    lambda: self.reset(scope)))
        dg.add(rs)
        if scope is not None and os.path.isdir(f"{HOMES}/{scope}"):
            tr = Adw.ActionRow(title="Move private home to Trash", subtitle=tilde(f"{HOMES}/{scope}"), activatable=True, use_markup=False)
            tr.connect("activated", lambda *_: self.ask("Move private home to Trash?", "Everything this program saved is moved to the Trash and can be restored from there.",
                                                        "Move to Trash", lambda: self.trash_home(scope, tr)))
            dg.add(tr)
        page.add(dg)
        return self.page("Default settings" if scope is None else scope, page)

    def switch_row(self, scope, key, page_rows=None):
        """page_rows: the refresh functions of every switch on this page, so a switch others depend on can update them."""
        title, sub, default = SWITCHES[key]
        row = Adw.SwitchRow(title=title, use_markup=False)
        undo = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                          tooltip_text="Use the default again")
        row.add_suffix(undo)
        busy = [False]

        def show():
            sc = parse(scope)[0]
            dsc = parse(None)[0] if scope is not None else {}
            own = key in sc
            busy[0] = True
            row.set_active(sc[key] if own else dsc.get(key, default))
            busy[0] = False
            row.set_subtitle(sub)                                  # an overridden switch shows the undo button; no note on the rest
            undo.set_visible(scope is not None and own)
            undo.set_tooltip_text("Back to the default: " + ("on" if dsc.get(key, default) else "off"))
            if key.startswith("portal_"):                          # the extra portals do nothing until portals itself is on
                row.set_sensitive(portals_on(scope))

        def changed():
            show()
            for refresh in page_rows or (): refresh()

        def toggled(*_):
            if not busy[0]: set_key(scope, key, row.get_active()); changed()
        row.connect("notify::active", toggled)
        undo.connect("clicked", lambda *_: (set_key(scope, key, None), changed()))
        if page_rows is not None and (key == "portals" or key.startswith("portal_")): page_rows.append(show)
        show()
        return row

    def app_menu_row(self, scope):
        """Switch that puts the program in the desktop's app menu as 'NAME (sbx)', through `sbx desktop NAME`."""
        row = Adw.SwitchRow(title="Add to the app menu through sbx", use_markup=False,
                            subtitle=f"Makes a launcher called {scope} (sbx) that starts the program in the sandbox")
        busy = [False]

        def set_active(v):
            busy[0] = True; row.set_active(v); busy[0] = False

        def toggled(*_):
            if busy[0]: return
            want = row.get_active()
            try: r = subprocess.run([sbx_cmd(), "desktop"] + ([] if want else ["--remove"]) + [scope], capture_output=True, text=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as e:
                set_active(not want); return self.alert("Couldn't run sbx", str(e))
            if r.returncode != 0:
                set_active(launcher_is_ours(scope))
                return self.alert("Couldn't change the app menu", (r.stderr or r.stdout).strip()[-700:] or f"Exit code {r.returncode}")
            self.toasts.add_toast(Adw.Toast(title="Added to the app menu" if want else "Removed from the app menu"))
        set_active(launcher_is_ours(scope))
        row.connect("notify::active", toggled)
        return row

    def folders_group(self, scope):
        """One list of shared folders and files. Each row has its own access choice: read and write, read only, or not shared."""
        g = Adw.PreferencesGroup(title="Shared folders and files",
                                 description="Everything not listed here is read-only or hidden from the program.")
        inherited = Adw.PreferencesGroup(title="Shared with all programs",
                                         description="Set in Default settings. Tap one to change it there.")
        inherited.set_visible(False)
        inh_rows = []
        rows = []
        MODES = (("whitelist", "Read and write"), ("read_only", "Read only"))

        def refill():
            for r in rows: g.remove(r)
            rows.clear()
            for r in inh_rows: inherited.remove(r)
            inh_rows.clear()
            if scope is not None:
                for key, label in MODES:
                    for v in parse(None)[1][key]:
                        r = Adw.ActionRow(title=v, subtitle=label, activatable=True, use_markup=False)
                        r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                        r.connect("activated", lambda *_: self.open_defaults())
                        inherited.add(r); inh_rows.append(r)
                inherited.set_visible(bool(inh_rows))
            for key, _label in MODES:
                for v in parse(scope)[1][key]:
                    r = Adw.ActionRow(title=v, use_markup=False)
                    dd = Gtk.DropDown(model=Gtk.StringList.new([l for _k, l in MODES] + ["Not shared"]), valign=Gtk.Align.CENTER)
                    dd.set_selected([k for k, _l in MODES].index(key))

                    def changed(w, _p, v=v, key=key):
                        i = w.get_selected()
                        if i >= len(MODES): remove_item(scope, key, v)           # "Not shared"
                        elif MODES[i][0] != key:
                            remove_item(scope, key, v); add_item(scope, MODES[i][0], v)
                        else: return
                        GLib.idle_add(refill)                                    # not while the menu is still closing
                    dd.connect("notify::selected", changed)
                    r.add_suffix(dd); g.add(r); rows.append(r)
            if not rows:
                r = Adw.ActionRow(title="None"); g.add(r); rows.append(r)

        def pick(folder):
            dlg = Gtk.FileDialog(title="Choose a folder to share" if folder else "Choose a file to share")

            def done(d, res):
                try: f = d.select_folder_finish(res) if folder else d.open_finish(res)
                except GLib.Error: return
                bad = path_problem(f.get_path())
                if bad: self.alert("Can't share that", bad)
                elif not add_item(scope, "read_only", tilde(f.get_path())):      # starts read only; the row's menu changes it
                    self.alert("Can't share that", "Names containing ' #' can't be stored in the settings file.")
                else: refill()
            (dlg.select_folder if folder else dlg.open)(self, None, done)
        box = Gtk.Box(spacing=6)
        for label, folder in (("Folder…", True), ("File…", False)):
            b = Gtk.Button(label=label, css_classes=["flat"])
            b.connect("clicked", lambda _b, folder=folder: pick(folder))
            box.append(b)
        g.set_header_suffix(box)
        refill()
        return [g, inherited] if scope is not None else [g]

    # -- actions --
    def run_program(self, name):
        # bwrap --die-with-parent kills the program when its parent exits, so a child of this window would die with it.
        # A transient user service has the systemd user manager as parent instead (its output goes to the journal).
        sr = shutil.which("systemd-run")
        if sr:
            cmd = [sr, "--user", "--collect", "--quiet", "--same-dir", f"--unit=sbx-{name}-{os.getpid()}"]
            for k in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                      "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XCURSOR_THEME", "XCURSOR_SIZE",
                      "LANG", "LANGUAGE", "LC_ALL", "TZ", "PATH"):
                if k in os.environ: cmd.append(f"--setenv={k}={os.environ[k]}")
            cmd += [sbx_cmd(), name]
        else:
            cmd = [sbx_cmd(), name]
        err = tempfile.TemporaryFile()
        try: p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
        except OSError as e:
            return self.alert("Couldn't start sbx", str(e))
        self.toasts.add_toast(Adw.Toast(title=f"Starting {name}…"))

        def check():
            rc = p.poll()
            if rc not in (None, 0):
                err.seek(0)
                self.alert(f"{name} stopped with an error", err.read(4096).decode(errors="replace")[-700:] or f"Exit code {rc}")
            err.close()
            return False
        GLib.timeout_add(2000, check)

    def show_command(self, name):
        try: r = subprocess.run([sbx_cmd(), "preview", name], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            return self.alert("Couldn't run sbx", str(e))
        lab = Gtk.Label(label=(r.stdout or r.stderr).strip(), wrap=True, selectable=True, xalign=0, css_classes=["monospace"])
        sw = Gtk.ScrolledWindow(min_content_height=220, min_content_width=420, child=lab)
        self.alert("Command", "", sw)

    def open_defaults(self):
        CURRENT[:] = [None]
        self.nav.replace([self.main, self.program_page(None)])            # like reset(): no stale copy of this page to come back to

    def reset(self, scope):
        save(scope, [])
        self.nav.replace([self.main, self.program_page(scope)])

    def trash_home(self, name, row):
        try: Gio.File.new_for_path(f"{HOMES}/{name}").trash(None)
        except GLib.Error as e:
            return self.alert("Couldn't move it to the Trash", e.message)
        row.set_sensitive(False)
        self.toasts.add_toast(Adw.Toast(title="Moved to the Trash"))
