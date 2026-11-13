#| 50_app: Application object, startup arguments and the entry point.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

class App(Adw.Application):
    def __init__(self, target, trouble=None):
        # --log starts its own instance: a second start of a unique application only activates the first one and drops the arguments
        super().__init__(application_id="org.sbx.Gui", **({"flags": Gio.ApplicationFlags.NON_UNIQUE} if trouble is not None else {}))
        self.target = target
        self.trouble = trouble                           # None, "" (all programs) or a program name: open the Troubleshooting page

    def do_startup(self):
        Adw.Application.do_startup(self)
        use_window_icon()

    def do_activate(self):
        first = self.props.active_window is None
        win = self.props.active_window or Win(self)
        win.present()
        if first and self.trouble is not None: win.open_trouble(self.trouble or None)
        elif first and self.target is not False: win.open(self.target)


if __name__ == "__main__":
    for flag, on in (("--install-menu", True), ("--remove-menu", False)):
        if flag in sys.argv[1:]:                         # no window: what `sbx desktop --install-menu` runs
            try: set_menu_entry(on)
            except (OSError, subprocess.SubprocessError) as e: sys.exit(f"sbx-gui: {getattr(e, 'strerror', None) or e}")
            print(f"wrote {tilde(MENU_DESKTOP)}" if on else "removed sbx from the app menu")
            sys.exit(0)
    load_schema()
    argv = sys.argv[:]
    target, trouble = False, None                        # sbx-gui NAME opens that program, --default the defaults, --log [NAME] the log
    if "--log" in argv:
        i = argv.index("--log")
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        trouble = nxt if NAME_RE.match(nxt) else ""
        del argv[i:i + (2 if trouble else 1)]
    if len(argv) > 1 and argv[1] == "--default": target = None; del argv[1]
    elif len(argv) > 1 and NAME_RE.match(argv[1]): target = argv[1]; del argv[1]
    sys.exit(App(target, trouble).run(argv))
