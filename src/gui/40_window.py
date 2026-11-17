#| 40_window: The GTK window (Win): every page of the app.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# ---- window ----
class Win(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="sbx", default_width=560, default_height=800)
        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.set_content(self.toasts)
        self.program_pages = {}                  # scope -> the program page last built for it (see refresh_program_page)
        self.badges = {}                         # name -> the green "Running" label of its row in the list (see tick)
        self.live = {}                           # scope -> what its open program page redraws when it starts or stops (see tick)
        self.main = self.main_page()
        self.nav.push(self.main)
        self.nav.connect("popped", lambda *_: self.fill_apps())
        self.connect("notify::is-active", lambda w, _p: w.is_active() and self.fill_apps())
        GLib.timeout_add_seconds(3, self.tick)

    def tick(self):
        """Every few seconds: show which programs run now, in the list (a green "Running") and on an open program page (its Stop button)."""
        for n, b in self.badges.items(): b.set_visible(program_running(n))
        stack = self.nav.get_navigation_stack()
        shown = [stack.get_item(i) for i in range(stack.get_n_items())]
        for scope, redraw in list(self.live.items()):
            if self.program_pages.get(scope) in shown: redraw()
            else: del self.live[scope]                                              # that page is gone
        return True

    def show_trouble(self, scope):
        """Called for a start from outside (a notification click): back to the main page first so Back never leads
        through a stale page, then the log. Works the same whether the window was just created or was already open."""
        self.nav.pop_to_page(self.main)
        self.open_trouble(scope)

    def show_program(self, scope):
        self.nav.pop_to_page(self.main)
        self.open(scope)

    def refresh_program_page(self, scope):
        """Build SCOPE's program page again if it is in the page stack. A page reads the settings file once, when it is built, so a
        setting changed from another page (a suggestion on the log page) would otherwise stay invisible there, with the old value
        shown under the switch. The pages above it stay as they are. Nothing happens when the page is not open (a notification
        click opens the log with no program page below it)."""
        mine = self.program_pages.get(scope)
        stack = self.nav.get_navigation_stack()
        pages = [stack.get_item(i) for i in range(stack.get_n_items())]
        if mine is None or mine not in pages: return
        keep = list(CURRENT)
        new = self.program_page(scope)                                            # registers itself in program_pages
        CURRENT[:] = keep
        self.nav.replace([new if p is mine else p for p in pages])

    def page(self, title, content, end=(), banner=None, start=()):
        view = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        for b in start: bar.pack_start(b)
        for b in end: bar.pack_end(b)
        view.add_top_bar(bar)
        if banner: view.add_top_bar(banner)
        view.set_content(content)
        return Adw.NavigationPage(child=view, title=title)

    def alert(self, heading, body="", child=None):
        d = Adw.AlertDialog(heading=heading, body=body)
        if child: d.set_extra_child(child)
        d.add_response("ok", "OK")
        d.present(self)

    def copy_text(self, text, done="Copied"):
        try: self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))
        except (TypeError, GLib.Error):
            return self.toasts.add_toast(Adw.Toast(title="Couldn't copy"))
        self.toasts.add_toast(Adw.Toast(title=done))

    def ask(self, heading, body, label, then):
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("cancel", "Cancel"); d.add_response("go", label)
        d.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_close_response("cancel")
        d.choose(self, None, lambda dlg, res: dlg.choose_finish(res) == "go" and then())

    # -- main page --
    def main_page(self):
        page = Adw.PreferencesPage()
        mg = Adw.PreferencesGroup()
        menu = Adw.SwitchRow(title="Show sbx in the app menu", subtitle="Adds a launcher for this window to your application menu.")
        menu.set_active(menu_entry_exists())

        def on_menu(r, _p):
            try:
                set_menu_entry(r.get_active())
                self.toasts.add_toast(Adw.Toast(title="Added to the app menu" if r.get_active() else "Removed from the app menu"))
            except (OSError, subprocess.SubprocessError) as e:
                self.toasts.add_toast(Adw.Toast(title=f"Could not change it: {getattr(e, 'strerror', None) or e}"))
                menu.handler_block_by_func(on_menu); menu.set_active(menu_entry_exists()); menu.handler_unblock_by_func(on_menu)
        menu.connect("notify::active", on_menu)
        mg.add(menu)
        page.add(mg)
        self.pending_banner = Adw.Banner(title="", button_label="Review", revealed=False)       # filled in by fill_apps
        self.pending_banner.connect("button-clicked", lambda *_: self.review_pending())
        self.apps_group = Adw.PreferencesGroup(title="Programs")
        self.app_rows = []
        self.others_open = False                 # is the terminal-only group open?
        page.add(self.apps_group)
        bg = Adw.PreferencesGroup(title="Backup", description="Saves the default settings and every program's own settings in one text file, and loads such a file back. "
                                                              "Private homes are not included.")
        exp_row = Adw.ActionRow(title="Export settings…", activatable=True)
        exp_row.add_suffix(Gtk.Image.new_from_icon_name("document-save-symbolic"))
        exp_row.connect("activated", lambda *_: self.export_settings("--all", "sbx-settings.txt"))
        imp_row = Adw.ActionRow(title="Import settings…", activatable=True)
        imp_row.add_suffix(Gtk.Image.new_from_icon_name("document-open-symbolic"))
        imp_row.connect("activated", lambda *_: self.import_settings())
        bg.add(exp_row); bg.add(imp_row)
        page.add(bg)
        rg = Adw.PreferencesGroup()
        rbtn = Gtk.Button(label="Reset all settings…", halign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        rbtn.connect("clicked", lambda *_: self.reset_all())
        rg.add(rbtn)
        page.add(rg)
        self.fill_apps()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add a program")
        add.connect("clicked", lambda *_: self.add_program())
        ts = Gtk.Button(icon_name="utilities-terminal-symbolic", tooltip_text="Troubleshooting: the health checks and what happened when programs started or stopped")
        ts.connect("clicked", lambda *_: self.open_trouble(None))
        return self.page("sbx", page, [ts], banner=self.pending_banner, start=[add])

    def open_trouble(self, scope):
        """Health checks (sbx doctor) and the recent log, for one program or all."""
        page = Adw.PreferencesPage()
        hl = Gtk.Label(xalign=0, wrap=True, selectable=True, focusable=False, margin_top=6, margin_bottom=6, css_classes=["monospace"])      # still selectable with the mouse; it just doesn't take the focus (and select all of itself) when the page opens
        sg = Adw.PreferencesGroup(title="Suggestions", description="Based on this program's last run, which ended with an error. "
                                  "Nothing changes until you press a button; it applies the next time the program starts.")
        sg.set_visible(False)
        sug_rows = []

        def show_hints():
            for r in sug_rows: sg.remove(r)
            sug_rows.clear()
            hints = log_hints(log_lines(scope), effective_switches(scope), effective_lists(scope)) if scope else []
            for key, want, title, why in hints:
                row = Adw.ActionRow(title=title, subtitle=why, use_markup=False)
                if key is not None:                                   # advice with no setting behind it has no button
                    btn = Gtk.Button(label="Add" if key in HINT_LISTS else "Turn on" if want else "Turn off", valign=Gtk.Align.CENTER)

                    def apply(_b, key=key, want=want):
                        try:
                            if key in HINT_LISTS: add_item(scope, key, want)
                            else: set_key(scope, key, want)
                            self.refresh_program_page(scope)                  # the page below shows the new value when you go back
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
        sw = Gtk.ScrolledWindow(min_content_height=320, child=tv, css_classes=["card"])
        lg.add(sw)
        hg = Adw.PreferencesGroup(title="Health", description="The checks of sbx doctor.")
        hg.add(hl)
        page.add(hg)                                      # the checks, then what to change, then the log it is based on
        page.add(sg)
        page.add(lg)
        buf = tv.get_buffer()
        tags = {k: buf.create_tag(k) for k in LOG_COLORS}
        for k in ("info", "exit_ok", "exit_err"): tags[k].set_property("weight", 700)

        def paint(*_):
            dark = 1 if Adw.StyleManager.get_default().get_dark() else 0
            for k, t in tags.items():
                rgba = Gdk.RGBA()
                rgba.parse(LOG_COLORS[k][dark])
                t.set_property("foreground-rgba", rgba)
        paint()
        Adw.StyleManager.get_default().connect("notify::dark", paint)

        def show_log():
            lines = log_lines(scope)[-200:]
            buf.set_text("")
            if not lines:
                buf.set_text("Nothing logged yet." if os.path.exists(LOG) else "No launches logged yet.")
                return
            for i, line in enumerate(lines):
                ts, name, text, kind = log_line_parts(line)
                for chunk, tag in ((ts, "ts"), (name, "name"), (text + ("\n" if i < len(lines) - 1 else ""), kind)):
                    if chunk: buf.insert_with_tags(buf.get_end_iter(), chunk, tags[tag]) if tag else buf.insert(buf.get_end_iter(), chunk)
            GLib.idle_add(lambda: sw.get_vadjustment().set_value(sw.get_vadjustment().get_upper()) and False)     # newest line in view

        latest = [0]

        def refresh(*_):
            latest[0] += 1
            mine = latest[0]
            hl.set_text("Running the checks…")

            def work():
                text = doctor_text()
                def show():
                    if mine == latest[0]: hl.set_text(text)                       # a newer refresh has its own answer coming
                    return False
                GLib.idle_add(show)
            threading.Thread(target=work, daemon=True).start()                    # sbx doctor can take seconds: the window never waits for it
            show_log()
            show_hints()
        rb = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        rb.connect("clicked", refresh)
        cb = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy the health checks and the log, to paste when asking for help")
        cb.connect("clicked", lambda *_: self.copy_text(hl.get_text() + "\n\n" + buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False),
                                                       "Copied the checks and the log"))
        refresh()
        pg = self.page("Troubleshooting", page, [rb, cb])
        pg.connect("shown", lambda *_: self.set_focus(None))                      # the health text is the first thing here: don't open with all of it selected
        self.nav.push(pg)

    def app_row(self, n, waiting=False, linked=None, menu=None, plain=False):
        """A program in the list, with a green "Running" while it runs and the arrow to its page. PLAIN (a program that is set up): what it may use,
        as in ajail. Not plain (only run from the terminal, so nothing to show yet): where it comes from and what is set."""
        sub = (("Waiting for your decision · " if waiting else "") + default_summary(n)) if plain else row_subtitle(n, waiting, linked, menu, show_running=False)
        r = Adw.ActionRow(title=n, subtitle=sub, activatable=True, use_markup=False)
        badge = Gtk.Label(label="Running", valign=Gtk.Align.CENTER, visible=program_running(n), css_classes=["success", "caption"])
        r.add_suffix(badge)
        self.badges[n] = badge                                                    # tick keeps it up to date
        r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        r.connect("activated", lambda _r, n=n: self.open(n))
        return r

    def stop_running(self, name):
        """sbx stop NAME. True if it was asked to stop (it takes a moment to end); otherwise says why not."""
        try: r = subprocess.run([sbx_cmd(), "stop", name], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            self.alert("Couldn't run sbx", str(e)); return False
        if r.returncode != 0:
            self.alert(f"Couldn't stop {name}", (r.stderr or r.stdout).strip()[-700:] or f"Exit code {r.returncode}"); return False
        self.toasts.add_toast(Adw.Toast(title=f"Stopping {name}…"))
        return True

    def review_pending(self):
        """The banner's button: open the first program that waits for a decision (the decision itself is on its page)."""
        names = sorted(pending_decisions())
        if names: self.open(names[0])

    def fill_apps(self):
        for r in self.app_rows: self.apps_group.remove(r)
        self.app_rows.clear()
        self.badges.clear()
        waiting = pending_decisions()
        count = len(waiting)
        self.pending_banner.set_title(f"{count} program{' is' if count == 1 else 's are'} waiting for your decision")
        self.pending_banner.set_revealed(count > 0)
        linked, menu = linked_programs(), launcher_programs()
        setup, others = program_groups()
        self.apps_group.set_description("Select a program to change what it can access. Changes apply the next time it starts." if setup or others else
                                        "No programs yet. Press + to add one, or just start one with sbx.")
        dr = Adw.ActionRow(title=DEFAULTS_TITLE, subtitle=default_summary(), activatable=True, use_markup=False)      # first, like ajail's "All apps (default)"
        dr.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        dr.connect("activated", lambda *_: self.open(None))
        self.apps_group.add(dr); self.app_rows.append(dr)
        for n in setup:
            r = self.app_row(n, n in waiting, linked, menu, True)
            self.apps_group.add(r); self.app_rows.append(r)
        if others:
            ex = Adw.ExpanderRow(title=f"Only run from the terminal ({len(others)})", subtitle="No settings, link or app menu launcher yet", use_markup=False)
            ex.set_expanded(self.others_open or not setup)
            ex.connect("notify::expanded", lambda e, _p: setattr(self, "others_open", e.get_expanded()))     # the list is rebuilt often; keep it as the person left it
            unused = unused_programs(others)
            if unused:
                cl = Adw.ActionRow(title=f"Remove the {len(unused)} unused", subtitle="Almost no data. Their private homes are moved to the Trash", activatable=True, use_markup=False)
                cl.connect("activated", lambda *_: self.ask_remove_unused())
                ex.add_row(cl)
            for n in others: ex.add_row(self.app_row(n, n in waiting, linked, menu))
            self.apps_group.add(ex); self.app_rows.append(ex)

    def ask_remove_unused(self):
        names = unused_programs(program_groups()[1])
        if not names: return self.toasts.add_toast(Adw.Toast(title="Nothing unused"))
        shown = ", ".join(names[:10]) + (f" and {len(names) - 10} more" if len(names) > 10 else "")
        self.ask(f"Remove {len(names)} unused program{'s' if len(names) != 1 else ''}?",
                 f"{shown}. None has settings, a link or an app menu launcher, and each holds under {UNUSED_BYTES // 1024} KiB. Their private homes are moved to the Trash.",
                 "Remove", lambda: self.remove_unused(names))

    def remove_unused(self, names):
        done = 0
        for n in names:
            if program_running(n): continue
            try: forget_program(n)
            except GLib.Error as e:
                self.fill_apps()
                return self.alert("Couldn't move a private home to the Trash", f"{n}: {e.message}")
            done += 1
        self.fill_apps()
        self.toasts.add_toast(Adw.Toast(title=f"Removed {done} unused program{'s' if done != 1 else ''}"))

    def add_program(self):
        entry = Gtk.Entry(placeholder_text="firefox", activates_default=True)
        d = Adw.AlertDialog(heading="Add a program", body="The name you type to start it, for example firefox.")
        d.set_extra_child(entry)
        d.add_response("cancel", "Cancel"); d.add_response("add", "Add")
        d.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("add"); d.set_close_response("cancel")
        d.set_response_enabled("add", False)
        body = d.get_body()

        def typing(*_):
            name = program_name(entry.get_text())
            d.set_response_enabled("add", bool(name))
            d.set_body(body if name == entry.get_text().strip() or not name else f"It will be called “{name}”: only letters, digits, . _ and - are kept.")
        entry.connect("changed", typing)

        def done(dlg, res):
            if dlg.choose_finish(res) != "add": return
            name = program_name(entry.get_text())
            if not name: return
            if not os.path.exists(path_for(name)): save(name, [])
            self.fill_apps(); self.open(name)
        d.choose(self, None, done)

    def open(self, scope):
        CURRENT[:] = [scope]
        self.nav.push(self.program_page(scope))

    # -- program page --
    def program_page(self, scope):
        """One program's page, or (scope None) the settings every program starts from. Top to bottom, as in ajail: a decision that
        blocks it, the program itself (where it comes from, Log, Stop, Launch, how else it starts), what it can use, folders and
        variables, its storage, the technical details, then the two red buttons."""
        page = Adw.PreferencesPage()
        page_rows = []                                                            # redrawn when a switch changes (the details, switches that depend on others)
        if scope is not None:
            pg = self.pending_group(scope)                                        # a decision that blocks it comes before everything
            if pg: page.add(pg)
            page.add(self.program_group(scope))
        tech, refresh_tech = self.tech_group(scope)
        page_rows.append(refresh_tech)
        found = []                                                                # (group, [(row, words searched)]) for the search
        for title, rows in GROUPS:
            g = Adw.PreferencesGroup(title=title, description=GROUP_INFO.get(title, ""))
            members, more = [], None
            for key, _t, _s, _d in rows:
                r = self.switch_row(scope, key, page_rows)
                if key in ADVANCED:                                               # seldom needed: one collapsed row, "More desktop services"
                    if more is None:
                        more = Adw.ExpanderRow(title="More desktop services", subtitle="Location, shortcuts, wallpaper, launchers, remote control, secrets, background", use_markup=False)
                        g.add(more)
                    more.add_row(r)
                else: g.add(r)
                members.append((r, key))
            page.add(g)
            found.append((g, members, more))

        def filter_rows(entry):
            words = entry.get_text().lower().split()
            for g, members, more in found:
                shown = 0
                for r, key in members:
                    hit = all(w in f"{r.get_title()} {r.get_subtitle() or ''} {key}".lower() for w in words)
                    r.set_visible(hit); shown += hit
                    if more is not None and key in ADVANCED and hit and words: more.set_expanded(True)      # a match inside the collapsed row opens it
                g.set_visible(shown > 0)
        search = Gtk.SearchEntry(placeholder_text="Search settings", hexpand=True)
        search.connect("search-changed", filter_rows)
        searchbar = Gtk.SearchBar(child=search, show_close_button=False)
        searchbar.connect_entry(search)
        toggle = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search the settings")
        toggle.connect("toggled", lambda b: searchbar.set_search_mode(b.get_active()))
        def closed(bar, _p):
            toggle.set_active(bar.get_search_mode())
            if not bar.get_search_mode(): search.set_text("")                    # closing the search shows everything again
        searchbar.connect("notify::search-mode-enabled", closed)
        page.add(self.limits_group(scope, refresh_tech))
        for grp in self.folders_group(scope, refresh_tech): page.add(grp)
        page.add(self.env_group(scope, refresh_tech))
        if scope is not None: page.add(self.storage_group(scope))
        page.add(tech)
        dg = Adw.PreferencesGroup()
        ebtn = Gtk.Button(label="Export settings…", halign=Gtk.Align.CENTER)
        ebtn.connect("clicked", lambda *_: self.export_settings(scope or "--default", f"sbx-{scope or 'default'}.txt"))
        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)                  # a group puts its rows right against each other
        btns.append(ebtn)
        rbtn = Gtk.Button(label="Reset settings…", halign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        rbtn.connect("clicked", lambda *_: self.ask("Reset settings?",
                                                    ("The default settings are deleted. Programs with their own settings keep them. " if scope is None
                                                     else "This program's own settings are deleted, so it uses the default settings again. ") + "Program data is not touched.",
                                                    "Reset", lambda: self.reset(scope)))
        btns.append(rbtn)
        dg.add(btns)
        page.add(dg)
        if scope is not None:
            installed = is_installed(scope)
            body = "Its settings are deleted and its private home is moved to the Trash, where you can restore it from. "
            if installed: body += f"It was made by sbx install, so its folder ~/Apps/{scope} is moved to the Trash too, with its terminal link and app menu launcher."
            elif link_is_ours(scope) or launcher_is_ours(scope): body += "Its terminal link and app menu launcher stay: turn those off first if you don't want them."
            body = body.rstrip()
            xg = Adw.PreferencesGroup(description=body)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            fbtn = Gtk.Button(label="Forget this program…", halign=Gtk.Align.CENTER, css_classes=["destructive-action"])
            fbtn.connect("clicked", lambda *_: self.ask(f"Forget {scope}?", body, "Forget", lambda: self.remove_program(scope)))
            col.append(fbtn)
            xg.add(col)
            page.add(xg)
        refresh_tech()
        pg = self.page(DEFAULTS_TITLE if scope is None else scope, page, [toggle], banner=searchbar)
        self.program_pages[scope] = pg
        return pg

    def program_group(self, scope):
        """ajail's App group: the program with Log, Stop and Launch, then the two ways of starting it through sbx without this window."""
        g = Adw.PreferencesGroup(title="Program")
        row = self.source_row(scope) or Adw.ActionRow(title=scope, subtitle="Not started through sbx yet", use_markup=False)
        badge = Gtk.Label(label="Running", valign=Gtk.Align.CENTER, css_classes=["success", "caption"])
        logb = Gtk.Button(label="Log", valign=Gtk.Align.CENTER)
        stop = Gtk.Button(label="Stop", valign=Gtk.Align.CENTER)
        launch = Gtk.Button(label="Launch", valign=Gtk.Align.CENTER)
        for w in (badge, logb, stop, launch): row.add_suffix(w)

        def redraw(*_):
            live = program_running(scope)
            badge.set_visible(live)
            stop.set_sensitive(live)
            launch.set_tooltip_text("Running now. Starts another copy with these settings" if live else "Starts it with these settings")
            return False                                                          # also used as a one-shot timeout

        def watch(want_live, tries):
            """A start or a stop takes a moment: look again every half second until the state is the wanted one (or it gives up)."""
            left = [tries]
            def look():
                redraw(); left[0] -= 1
                return left[0] > 0 and program_running(scope) != want_live        # True: look again
            GLib.timeout_add(500, look)

        def start(*_):
            self.run_program(scope)
            watch(True, 20)

        def stop_it(*_):
            if self.stop_running(scope): watch(False, 30)
        logb.connect("clicked", lambda *_: self.open_trouble(scope))
        stop.connect("clicked", stop_it)
        launch.connect("clicked", start)
        self.live[scope] = redraw                                                 # tick redraws it too: a program may also be started or stopped elsewhere
        redraw()
        g.add(row)
        g.add(self.app_menu_row(scope))
        g.add(self.link_row(scope))
        return g

    def tech_group(self, scope):
        """ajail's Technical details: what the program can do in a sentence and, for a program, the command sbx would run (with a button to
        copy it). Returns (the group, a function that fills it in again from the settings now in the files)."""
        g = Adw.PreferencesGroup()
        exp = Adw.ExpanderRow(title="Technical details", use_markup=False)
        summary_row = Adw.ActionRow(title="What it can do", use_markup=False)
        exp.add_row(summary_row)
        cmd = None
        if scope is not None:
            cmd = Adw.ActionRow(title="Command", subtitle="…", subtitle_selectable=True, use_markup=False, css_classes=["monospace"])
            copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Copy the command")
            copy.connect("clicked", lambda *_: self.copy_text(cmd.get_subtitle() or ""))
            cmd.add_suffix(copy)
            exp.add_row(cmd)
        g.add(exp)
        latest = [0]

        def refresh(*_):
            summary_row.set_subtitle(capabilities(scope))
            if cmd is None: return False
            latest[0] += 1
            mine = latest[0]

            def work():
                text = self.command_text(scope)
                def show():
                    if mine == latest[0]: cmd.set_subtitle(text)                 # a newer change has its own answer coming
                    return False
                GLib.idle_add(show)
            threading.Thread(target=work, daemon=True).start()                    # `sbx preview` is quick, but the window never waits for it
            return False
        return g, refresh

    def command_text(self, name):
        """What `sbx preview` prints for NAME, or why it can't (a program that can't be started from here has nothing to preview)."""
        argv, problem = preview_command(name)
        if problem: return problem
        try: r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            return f"Couldn't run sbx: {getattr(e, 'strerror', None) or e}"
        return (r.stdout or r.stderr).strip()

    def storage_group(self, scope):
        """ajail's Storage: the program's private home with its size, Open and Reset… (to the Trash, so it can be restored)."""
        g = Adw.PreferencesGroup(title="Storage")
        home = f"{HOMES}/{scope}"
        row = Adw.ActionRow(title="Private home", subtitle=f"… · {tilde(home)}", use_markup=False)

        def show_size():
            def work():
                text = f"{dir_size(home)} · {tilde(home)}"
                GLib.idle_add(lambda: row.set_subtitle(text) and False)           # a big home takes a moment: the page doesn't wait for it
            threading.Thread(target=work, daemon=True).start()

        def open_home(*_):
            if not os.path.isdir(home): return self.toasts.add_toast(Adw.Toast(title="Nothing saved yet"))
            try: p = subprocess.Popen(["xdg-open", home], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as e: return self.toasts.add_toast(Adw.Toast(title=f"Couldn't open it: {e.strerror or e}"))
            threading.Thread(target=p.wait, daemon=True).start()                  # reaped when it ends, no zombie
        openb = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
        openb.connect("clicked", open_home)
        resetb = Gtk.Button(label="Reset…", valign=Gtk.Align.CENTER, css_classes=["destructive-action"])
        resetb.connect("clicked", lambda *_: self.ask(f"Move the private home of {scope} to the Trash?",
                                                      "Everything this program saved (its settings, logins and files) is moved to the Trash and can be restored from there.",
                                                      "Move to Trash", lambda: self.trash_home(scope, row, show_size)))
        row.add_suffix(openb); row.add_suffix(resetb)
        g.add(row)
        show_size()
        return g

    def source_row(self, scope):
        """Where the program comes from, as sbx recorded it when it first ran (None when it never ran through sbx)."""
        s = origin_source(scope)
        if not s: return None
        parts = source_parts(s)
        more = parts[1:] + source_tags(scope)
        if s["missing"]: more.insert(0, "The recorded file is gone. If you moved it, start the program from its new place.")
        return Adw.ActionRow(title=parts[0], subtitle=" · ".join(more), use_markup=False)

    def pending_group(self, scope):
        """What to decide when a file showed up under this program's name that sbx can't tell apart from a different program
        (see check_origin in sbx). Both buttons run `sbx rebind`. Where the original still exists (rule d) the warning is stronger
        and accepting is destructive-styled and asked twice. None when nothing waits."""
        p = pending_decisions().get(scope)
        if not p: return None
        other = p["rule"] == "d"
        text = (f"The original is still at {tilde(p['old'])}. A different file now wants the name {scope} and would share its settings and private home. "
                "Accept only if it really is the same program; for a different one start it with --name=OTHER in a terminal instead."
                if other else
                f"{scope} was first run from {tilde(p['old'])}, which is gone. Now it is started from {tilde(p['new'])}. "
                "If that is the same program, accept it. Until you decide, sbx won't start it.")
        g = Adw.PreferencesGroup(title="Waiting for your decision", description=GLib.markup_escape_text(text))
        g.add(Adw.ActionRow(title=f"{tilde(p['old'])} → {tilde(p['new'])}", use_markup=False))
        accept = Gtk.Button(label="Same program, accept", valign=Gtk.Align.CENTER, css_classes=["destructive-action" if other else "suggested-action"])
        dismiss = Gtk.Button(label="Dismiss", valign=Gtk.Align.CENTER, css_classes=["flat"])
        if other:
            accept.connect("clicked", lambda *_: self.ask(f"Accept a different file as {scope}?",
                                                          f"The original is still at {tilde(p['old'])}. {tilde(p['new'])} would take over the settings and private home of {scope}.",
                                                          "Accept anyway", lambda: self.decide_pending(scope, True)))
        else:
            accept.connect("clicked", lambda *_: self.decide_pending(scope, True))
        dismiss.connect("clicked", lambda *_: self.decide_pending(scope, False))
        box = Gtk.Box(spacing=6)
        box.append(dismiss); box.append(accept)
        g.set_header_suffix(box)
        return g

    def decide_pending(self, name, accept):
        """Same program, accept / Dismiss. sbx itself decides (sbx rebind), like the link and app menu switches do."""
        try: r = subprocess.run([sbx_cmd(), "rebind"] + ([] if accept else ["--dismiss"]) + [name], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            return self.alert("Couldn't run sbx", str(e))
        self.nav.replace([self.main, self.program_page(name)])                   # either way the page shows how things stand now
        self.fill_apps()
        if r.returncode != 0:
            return self.alert("Couldn't decide", (r.stderr or r.stdout).strip()[-700:] or f"Exit code {r.returncode}")
        self.toasts.add_toast(Adw.Toast(title=f"{name} is accepted. Start it again." if accept else f"Dismissed. sbx asks again the next time {name} starts from there."))

    def switch_row(self, scope, key, page_rows=None):
        """page_rows: the refresh functions of every switch on this page, so a switch others depend on can update them."""
        title, sub, default = SWITCHES[key]
        row = Adw.SwitchRow(title=title, use_markup=False)
        undo = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                          tooltip_text="Use the default again")
        row.add_suffix(undo)
        busy = [False]

        WL_WHY = "Your desktop can't restrict programs (it lacks the wp_security_context_v1 Wayland protocol), so this only turns Wayland off."

        def blocked():
            """Why this switch can't be turned on right now, or "" (e.g. the extra portals do nothing until portals itself is on)."""
            req = needs(key)
            if req and not any(is_on(scope, k) for k in req):
                return "Turn on “" + "” or “".join(SWITCHES[k][0] for k in req if k in SWITCHES) + "” first"
            if key == "wayland_strict" and not WL_RESTRICTED: return WL_WHY
            return ""

        def inherited(dsc):
            """What this switch is without its own line: the default file's value for a program, else the built-in one."""
            return dsc[key] if key in dsc else WL_RESTRICTED if key == "wayland_strict" else default

        def show():
            sc = parse(scope)[0]
            dsc = parse(None)[0] if scope is not None else {}
            own = key in sc
            busy[0] = True
            row.set_active(sc[key] if own else dsc.get(key, default))
            busy[0] = False
            prob = folder_problem(scope) if key == "folder" and scope is not None and row.get_active() else None
            row.set_subtitle(sub + (f"\n⚠ {prob}" if prob else ""))      # an overridden switch shows the undo button; a folder that can't be shared says why
            undo.set_visible(own)                                           # on the defaults page it goes back to the built-in value
            undo.set_tooltip_text("Back to the " + ("default" if scope is not None else "built-in setting") + ": " + ("on" if inherited(dsc) else "off"))
            why = blocked()
            can_undo = own
            if why:                         # greying the row greys its undo button too, so a row holding an override stays usable
                row.set_sensitive(can_undo or (key == "wayland_strict" and row.get_active()))
                row.set_tooltip_text(why)
            else:
                row.set_sensitive(True); row.set_tooltip_text(None)
            if key == "wayland_strict" and not WL_RESTRICTED:
                row.set_subtitle(sub + f"\n{WL_WHY}")

        def changed():
            show()
            for refresh in page_rows or (): refresh()

        def toggled(*_):
            if busy[0]: return
            why = blocked()
            if row.get_active() and why:       # a row kept usable for its undo button still can't be turned on
                busy[0] = True; row.set_active(False); busy[0] = False
                self.toasts.add_toast(Adw.Toast(title=why))
                return
            val = row.get_active()
            dsc = parse(None)[0] if scope is not None else {}
            if bool(inherited(dsc)) == val: val = None      # back to what the default (or, on the defaults page, the built-in) says: drop the line, so the undo button goes too
            set_key(scope, key, val)
            if key == "net_private" and row.get_active() and not shutil.which("pasta"):
                self.toasts.add_toast(Adw.Toast(title="pasta (passt) is not installed: the program won't start until you install it"))
            changed()
        row.connect("notify::active", toggled)
        undo.connect("clicked", lambda *_: (set_key(scope, key, None), changed()))
        if page_rows is not None and needs(key): page_rows.append(show)
        show()
        return row

    def limits_group(self, scope, on_change=None):
        """ajail's Resource limits: how much memory, CPU and how many processes the program may use. Written as memory=, cpu= and tasks= lines."""
        g = Adw.PreferencesGroup(title="Resource limits",
                                 description="0 means no limit. Keeps one program from using all your memory or CPU. Needs systemd (systemd-run --user): "
                                             "without it sbx won't start a program that has a limit.")
        for key, title, sub, upper, step in LIMIT_ROWS: g.add(self.limit_row(scope, key, title, sub, upper, step, on_change))
        return g

    def limit_row(self, scope, key, title, sub, upper, step, on_change=None):
        """One limit as a spin row with an undo button (shown while this program's own file sets it). A value the row can't show exactly,
        like memory=50%, is left alone until the row is changed; its subtitle says what the file contains."""
        row = Adw.SpinRow(title=title, subtitle=sub, use_markup=False,
                          adjustment=Gtk.Adjustment(value=0, lower=0, upper=upper, step_increment=step, page_increment=step * 10))
        undo = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Use the default again")
        row.add_suffix(undo)
        busy = [False]

        def show():
            text = limit_text(scope, key)
            n, exact = limit_number(key, text)
            busy[0] = True
            row.set_value(min(n, upper))
            busy[0] = False
            row.set_subtitle(sub if exact and n <= upper else f"{sub} The settings file says {text}; changing this replaces it.")
            undo.set_visible(limit_own(scope, key) is not None)

        def changed(*_):
            if busy[0]: return
            set_limit(scope, key, limit_choice(scope, key, int(row.get_value())))
            show()
            if on_change: on_change()

        def reset(*_):
            set_limit(scope, key, None)
            show()
            if on_change: on_change()
        row.connect("notify::value", changed)
        undo.connect("clicked", reset)
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

    def link_row(self, scope):
        """Switch that makes typing the program's name in a terminal start it through sbx too, through `sbx link NAME`."""
        row = Adw.SwitchRow(title="Also when typed in a terminal", use_markup=False,
                            subtitle=f"Makes ~/.local/bin/{scope} a link to sbx, so typing {scope} starts it in the sandbox (like firejail's firecfg)")
        busy = [False]

        def set_active(v):
            busy[0] = True; row.set_active(v); busy[0] = False

        def toggled(*_):
            if busy[0]: return
            want = row.get_active()
            try: r = subprocess.run([sbx_cmd(), "link"] + ([] if want else ["--remove"]) + [scope], capture_output=True, text=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as e:
                set_active(not want); return self.alert("Couldn't run sbx", str(e))
            if r.returncode != 0:
                set_active(link_is_ours(scope))
                return self.alert("Couldn't change the link", (r.stderr or r.stdout).strip()[-700:] or f"Exit code {r.returncode}")
            if r.stderr.strip(): self.alert("Done, but check this", r.stderr.strip()[-700:])
            else: self.toasts.add_toast(Adw.Toast(title="Typing the name now uses sbx" if want else "Typing the name starts it directly again"))
        set_active(link_is_ours(scope))
        row.connect("notify::active", toggled)
        return row

    def folders_group(self, scope, on_change=None):
        """One list of shared folders and files, in the order of the settings file. Each row has a button that says Read only or Read and write and flips it, and a button to stop sharing it."""
        g = Adw.PreferencesGroup(title="Shared folders and files",
                                 description="Everything not listed here is read-only or hidden from the program.")
        inherited = Adw.PreferencesGroup(title="Shared with all programs",
                                         description=f"Set in “{DEFAULTS_TITLE}”. Tap one to change it there.")
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
            size = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)                  # the two labels are not equally wide; the buttons are
            for key, v in shared_items(scope):
                mine = is_installed_folder(scope, v)
                r = Adw.ActionRow(title=v, use_markup=False, **({"subtitle": INSTALLED_NOTE} if mine else {}))
                ro = key == "read_only"
                mode = Gtk.Button(label="Read only" if ro else "Read and write", valign=Gtk.Align.CENTER,       # the label says how it is now; a click flips it
                                  tooltip_text="Read only. Click to let the program change it." if ro else "Read and write. Click to make it read only.")
                rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Stop sharing")
                size.add_widget(mode)

                def flip(_b, v=v, ro=ro):
                    move_item(scope, "read_only" if ro else "whitelist", v, "whitelist" if ro else "read_only")
                    self.toasts.add_toast(Adw.Toast(title=f"{v} is now " + ("read and write" if ro else "read only")))
                    GLib.idle_add(refill)                                        # not while the button is still handling its own signal

                def stop_sharing(_b, v=v, key=key, mine=mine):
                    def go():
                        gone = take_item(scope, key, v)
                        t = Adw.Toast(title=f"Stopped sharing {v}", button_label="Undo")          # the toast's own time is the time to undo
                        t.connect("button-clicked", lambda *_: (restore_item(scope, key, v, gone), GLib.idle_add(refill)))
                        self.toasts.add_toast(t)
                        GLib.idle_add(refill)
                    if mine: self.ask("Stop sharing the program’s own folder?",
                                      f"{scope} may not start without {v}. You can undo this right afterwards, or add the folder again with Add folder.", "Stop sharing", go)
                    else: go()
                mode.connect("clicked", flip)
                rm.connect("clicked", stop_sharing)
                r.add_suffix(mode); r.add_suffix(rm); g.add(r); rows.append(r)
            if not rows:
                r = Adw.ActionRow(title="None"); g.add(r); rows.append(r)
            if on_change: on_change()

        def pick(folder):
            dlg = Gtk.FileDialog(title="Choose a folder to share" if folder else "Choose a file to share")

            def done(d, res):
                try: f = d.select_folder_finish(res) if folder else d.open_finish(res)
                except GLib.Error: return
                bad = path_problem(f.get_path())
                if bad: self.alert("Can't share that", bad)
                elif not add_item(scope, "read_only", tilde(f.get_path())):      # starts read only; the row's toggle changes it
                    self.alert("Can't share that", "Names containing ' #' or a line break can't be stored in the settings file.")
                else: refill()
            (dlg.select_folder if folder else dlg.open)(self, None, done)
        box = Gtk.Box(spacing=6)
        for label, folder in (("Add folder", True), ("Add file", False)):
            b = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, folder=folder: pick(folder))
            box.append(b)
        g.set_header_suffix(box)
        refill()
        return [g, inherited] if scope is not None else [g]

    def env_group(self, scope, on_change=None):
        """Environment variables, one editable row each: NAME=value is set for the program (setenv=), a bare NAME passes your own
        value through (env=). The row on top adds one; a row's text is changed where it stands (Enter or the check mark)."""
        g = Adw.PreferencesGroup(title="Environment variables",
                                 description="sbx starts the program with a clean environment. NAME=value sets a variable for it, "
                                             "for example SDL_VIDEODRIVER=x11. A bare NAME passes your own value through.")
        rows = []

        def apply(row, old):
            """old: (list key, value) of the entry this row shows, None for the row that adds."""
            if old is not None and row.get_text().strip() == old[1]: return           # nothing changed
            bad = set_env_entry(scope, row.get_text(), old)
            if bad:
                self.alert("Can't use that", bad)
                if old is not None: row.set_text(old[1])
                return
            GLib.idle_add(refill)                                                      # not while the row is still handling its own signal
            if on_change: on_change()

        def refill():
            for r in rows: g.remove(r)
            rows.clear()
            new = Adw.EntryRow(title="New variable (NAME=value)", show_apply_button=True)
            new.connect("apply", lambda r: apply(r, None))
            g.add(new); rows.append(new)
            for key in ("setenv", "env"):
                for v in raw_list(scope, key):
                    r = Adw.EntryRow(title="Variable" if key == "setenv" else "Passed through from your own environment",
                                     text=v, show_apply_button=True)
                    r.connect("apply", lambda r, old=(key, v): apply(r, old))
                    rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                                    tooltip_text=f"Remove {v.partition('=')[0]}")
                    rm.connect("clicked", lambda _b, key=key, v=v: (remove_item(scope, key, v), GLib.idle_add(refill), on_change and on_change()))
                    r.add_suffix(rm); g.add(r); rows.append(r)
        refill()
        return g

    # -- actions --
    def run_program(self, name):
        # bwrap --die-with-parent kills the program when its parent exits, so a child of this window would die with it.
        # A transient user service has the systemd user manager as parent instead (its output goes to the journal).
        argv, problem = start_command(name)
        if problem: return self.alert(f"Can't start {name}", problem)
        sr = shutil.which("systemd-run")
        if sr:
            cmd = [sr, "--user", "--collect", "--quiet", "--same-dir", f"--unit={unit_name(name)}",
                   f"--property=SyslogIdentifier={name}"]
            for k in ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                      "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XCURSOR_THEME", "XCURSOR_SIZE",
                      "LANG", "LANGUAGE", "LC_ALL", "TZ", "PATH"):
                if k in os.environ: cmd.append(f"--setenv={k}={os.environ[k]}")
            cmd += argv
        else:
            cmd = argv
        err = tempfile.TemporaryFile()
        try: p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, start_new_session=True)
        except OSError as e:
            err.close()
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

    def open_defaults(self):
        CURRENT[:] = [None]
        self.nav.replace([self.main, self.program_page(None)])            # like reset(): no stale copy of this page to come back to

    def reset(self, scope):
        done = reset_settings(scope)
        self.nav.replace([self.main, self.program_page(scope)])
        self.toasts.add_toast(Adw.Toast(title="Settings reset to the defaults" if done else "Already using the defaults"))

    def cli_error(self, r):
        """The message sbx printed, without its 'sbx: ' prefix. Shown in a toast, which never parses markup from it."""
        lines = [l for l in (r.stderr or "").splitlines() if l.strip()]
        return re.sub(r"^sbx: ", "", lines[-1] if lines else f"sbx exited with {r.returncode}")

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text))

    def sbx_run(self, *args):
        try: return subprocess.run([sbx_cmd(), *args], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e: return subprocess.CompletedProcess(args, 127, "", f"sbx: {getattr(e, 'strerror', None) or e}")

    def export_settings(self, target, name):
        """Save settings to a text file with 'sbx export'. TARGET is --all, --default or a program name; NAME is the suggested file name.
        The file dialog already asks before replacing a file, hence --force."""
        dlg = Gtk.FileDialog(title="Export settings", initial_name=name)
        def cb(d, res):
            try: f = d.save_finish(res)
            except GLib.Error: return
            if not f.get_path(): return self.toast("Choose a folder on this computer")
            r = self.sbx_run("export", target, "-o", f.get_path(), "--force")
            self.toast("Settings exported" if r.returncode == 0 else self.cli_error(r))
        dlg.save(self, None, cb)

    def import_settings(self):
        """Load an export with 'sbx import'. Settings that would be replaced are only replaced after a confirmation."""
        dlg = Gtk.FileDialog(title="Import settings")
        def cb(d, res):
            try: f = d.open_finish(res)
            except GLib.Error: return
            path = f.get_path()
            if not path: return self.toast("Choose a file on this computer")
            def go(force):
                r = self.sbx_run("import", path, *(["--force"] if force else []))
                if r.returncode == 0:
                    self.fill_apps()
                    self.toast(r.stdout.strip().capitalize() or "Settings imported")
                elif not force and "--force" in r.stderr:           # some of them exist and differ
                    self.ask("Replace existing settings?", self.cli_error(r).replace(" Nothing was imported; --force replaces them", "")
                             + " The imported settings replace them; nothing else changes.", "Replace", lambda: go(True))
                else: self.toast(self.cli_error(r))
            go(False)
        dlg.open(self, None, cb)

    def reset_all(self):
        n = len(settings_files())
        if not n: return self.toasts.add_toast(Adw.Toast(title="Nothing to reset"))
        self.ask("Reset all settings?", f"Deletes every settings file ({n}): the default settings and each program's own. "
                 "Private homes and app menu launchers are not touched.", "Reset all", lambda: self.do_reset_all())

    def do_reset_all(self):
        n = reset_all_settings()
        self.fill_apps()
        self.toasts.add_toast(Adw.Toast(title=f"Reset {n} settings file{'s' if n != 1 else ''}"))

    def remove_program(self, name):
        if program_running(name):
            return self.alert("It is running", f"Stop it first (sbx stop {name}), then remove it.")
        if is_installed(name):                                              # made by sbx install: its folder, link and menu entry go too (to the Trash)
            err = self.run_uninstall(name)
            if err: return self.alert("Couldn't uninstall", err)
        try: forget_program(name)
        except GLib.Error as e:
            return self.alert("Couldn't move its private home to the Trash", e.message)
        CURRENT[:] = []
        self.nav.replace([self.main]); self.fill_apps()
        self.toasts.add_toast(Adw.Toast(title=f"Removed {name} from the list"))

    def run_uninstall(self, name):
        """`sbx uninstall --yes --trash NAME`. Returns None on success, else the reason."""
        try: r = subprocess.run([sbx_cmd(), "uninstall", "--yes", "--trash", name], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e: return str(e)
        if r.returncode != 0: return (r.stderr or r.stdout).strip()[-700:] or f"Exit code {r.returncode}"
        return None

    def trash_home(self, name, row, refresh=None):
        """Storage's Reset…: the private home to the Trash (restorable). REFRESH shows the new size in ROW afterwards."""
        if program_running(name):
            return self.alert("It is running", f"Stop it first (sbx stop {name}), then move its home to the Trash.")
        if not os.path.isdir(f"{HOMES}/{name}"):
            return self.toasts.add_toast(Adw.Toast(title="Nothing saved yet"))
        try: Gio.File.new_for_path(f"{HOMES}/{name}").trash(None)
        except GLib.Error as e:
            return self.alert("Couldn't move it to the Trash", e.message)
        if refresh: refresh()
        self.toasts.add_toast(Adw.Toast(title="Moved to the Trash"))
