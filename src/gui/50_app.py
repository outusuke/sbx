#| 50_app: Application object, startup arguments and the entry point.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

def parse_args(argv):
    """argv without the program name -> (target, trouble): sbx-gui NAME opens that program, --default the defaults,
    --log [NAME] the log. target False = main page; trouble None = no log page, "" = all programs, else a program name."""
    argv = list(argv)
    target, trouble = False, None
    if "--log" in argv:
        i = argv.index("--log")
        nxt = argv[i + 1] if i + 1 < len(argv) else ""
        trouble = nxt if NAME_RE.match(nxt) else ""
        del argv[i:i + (2 if trouble else 1)]
    if argv and argv[0] == "--default": target = None
    elif argv and NAME_RE.match(argv[0]): target = argv[0]
    return target, trouble


class App(Adw.Application):
    def __init__(self):
        # One window per login: a second start (a notification click, `sbx-gui NAME`) is handed to the running one, which
        # shows the asked page and comes to the front, instead of starting a second process nobody sees.
        super().__init__(application_id="org.sbx.Gui", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Adw.Application.do_startup(self)
        use_window_icon()

    def do_command_line(self, cmdline):
        target, trouble = parse_args(cmdline.get_arguments()[1:])
        win = self.props.active_window or Win(self)
        if trouble is not None: win.show_trouble(trouble or None)
        elif target is not False: win.show_program(target)
        win.present()
        return 0

    def do_activate(self):
        (self.props.active_window or Win(self)).present()


if __name__ == "__main__":
    for flag, on in (("--install-menu", True), ("--remove-menu", False)):
        if flag in sys.argv[1:]:                         # no window: what `sbx desktop --install-menu` runs
            try: set_menu_entry(on)
            except (OSError, subprocess.SubprocessError) as e: sys.exit(f"sbx-gui: {getattr(e, 'strerror', None) or e}")
            print(f"wrote {tilde(MENU_DESKTOP)}" if on else "removed sbx from the app menu")
            sys.exit(0)
    load_schema()
    sys.exit(App().run(sys.argv))
