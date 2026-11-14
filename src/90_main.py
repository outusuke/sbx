#| 90_main: Entry point.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if argv is None and linked_name(sys.argv[0]): return run_linked(sys.argv[0], a)
    if not a or a[0] in ("-h", "--help", "help"): print(USAGE); return
    if a[0] in ("-V", "--version", "version"): print(f"sbx {VERSION}"); return
    cmd = a.pop(0) if a[0] in COMMANDS else None
    if cmd == "doctor": sys.exit(doctor())
    if cmd == "schema": print(json.dumps(schema(), indent=1)); return
    if cmd == "clean": return cmd_clean(a)
    if cmd == "reset": return cmd_reset(a)
    if cmd == "link": return cmd_link(a)
    if cmd == "rebind": return cmd_rebind(a)
    if cmd == "list": return cmd_list(a)
    if cmd == "stop": return cmd_stop(a)
    if cmd == "desktop": return cmd_desktop(a)
    if cmd == "open": return cmd_open(a)
    if cmd == "show": return cmd_show(a)
    opts, a = parse_options(a)
    if not a: die("usage: sbx [preview] [options] PROGRAM [args]")
    if cmd == "preview":
        if not opts.get("loader") and "/" in a[0] and os.path.isfile(a[0]) and not os.access(a[0], os.X_OK) and elf_interpreter(os.path.abspath(a[0])):
            opts = {**opts, "loader": elf_interpreter(os.path.abspath(a[0]))}      # run() sets the execute bit for the run (or uses the loader); preview shows the loader form without touching the file
        _, cmdline, st = build(a[0], a[1:], False, opts)
        print(shlex.join(st["prefix"] + st["wrap"] + cmdline)); return
    run(a[0], a[1:], opts)


if __name__ == "__main__":
    main()
