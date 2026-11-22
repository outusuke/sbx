#| 10_tables: Wording for every switch (GROUPS, SWITCHES, SHORT) and the other constants the window starts from. Data only.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# Wording for each switch: key, title, subtitle, default. The list of switches itself comes from `sbx schema` (see
# load_schema), so a switch added to sbx shows up here at once, under "Other", even before it has wording of its own.
GROUPS = [
    ("What can this program use?", [
        ("net", "Internet", "Network access. Also reaches localhost and your local network.", True),
        ("net_private", "Keep localhost private", "Internet only: the program can't reach services running on this computer (databases, dev servers). Other devices on your local network are still reachable. Needs pasta (passt).", False),
        ("sound", "Audio", "Play sound. Also allows microphone access.", True),
        ("wayland", "Wayland", "Lets graphical programs open a window", True),
        ("wayland_strict", "Wayland only if restricted", "No Wayland at all when the compositor can't limit what the program may do", False),
        ("x11", "X11 compatibility", "Only for programs that won't start otherwise. Weaker isolation.", False),
        ("printing", "Printing", "Talk to the print service (CUPS)", False),
        ("ssh_agent", "SSH agent", "Use your SSH keys without seeing them. The program can sign in as you.", False)]),
    ("Devices", [
        ("gpu", "Graphics acceleration", "Needed by most programs; turn off only if the program crashes", True),
        ("gpu_render_only", "Graphics: render only", "Give the program only the GPU's render devices, not the ones that can also drive the display. Works for most programs; turn it off if graphics break. Needs Graphics acceleration.", False),
        ("camera", "Camera", "Webcams (/dev/video*)", False),
        ("input", "Game controllers and input devices", "Raw access to /dev/input", False),
        ("alsa", "ALSA sound devices", "/dev/snd, for programs that don't use PipeWire or PulseAudio", False),
        ("rocm", "AMD compute (ROCm)", "/dev/kfd", False),
        ("kvm", "Virtual machines", "/dev/kvm", False),
        ("compat", "Pass graphics and toolkit settings", "For programs that draw wrongly or won't start. Copies the graphics and toolkit variables of your session that are set (WGPU_*, MESA_*, SDL_*, QT_QPA_* and similar). Never tokens or other variables.", False)]),
    ("Desktop integration", [
        ("portals", "Notifications and portals", "Notifications, opening links, the file chooser, dark mode (needs xdg-dbus-proxy)", False),
        ("portal_notification", "Notifications", "Show desktop notifications (with the portals switch on)", True),
        ("portal_openuri", "Open links and files", "Let the program ask the desktop to open a link or file in another program", True),
        ("portal_print", "Printing dialog", "Let the program use the desktop's print dialog", True),
        ("portal_trash", "Move files to trash", "Let the program move files it can reach to the trash", True),
        ("portal_email", "Compose email", "Let the program open your mail program with a new message", True),
        ("portal_inhibit", "Keep screen awake", "Let the program stop the screen from sleeping or locking", True),
        ("portal_network", "Network state", "Let the program ask whether you are online and which proxy to use", True),
        ("portal_file_transfer", "Drag and drop files", "Lets the program receive files you drag in from other programs (browsers print a FileTransfer error without it). Only files you drop on it, through the Documents portal.", False),
        ("portal_screenshot", "Screenshots", "Let the program ask the desktop for a screenshot. The desktop shows its own prompt.", False),
        ("portal_screencast", "Screen sharing", "Let the program ask to share your screen or a window (Discord, Teams, Slack). The desktop shows its own picker.", False),
        ("tray", "Tray icon and media keys", "Show a tray icon and answer media keys (needs xdg-dbus-proxy)", False),
        ("a11y", "Screen reader support", "Let a screen reader such as Orca read the program (needs xdg-dbus-proxy)", False),
        ("gnome_shell", "GNOME Shell integration", "Let the browser's GNOME Shell connector list and install extensions (the Shell asks you first; needs xdg-dbus-proxy)", False),
        ("gnome_shell_manage", "Manage GNOME Shell extensions", "Also let it enable, disable and uninstall extensions (needs GNOME Shell integration)", False),
        ("kde", "KDE Plasma integration", "Let the browser's Plasma integration show downloads as Plasma jobs, answer KRunner and the media controls, and keep the computer awake while it downloads (needs xdg-dbus-proxy)", False),
        ("theme", "Your fonts and themes", "Read-only access to your installed fonts, icons and GTK settings, so the program looks like your desktop", True),
        ("portal_location", "Location", "Lets the program ask the desktop where you are.", False),
        ("portal_shortcuts", "Global shortcuts", "Keyboard shortcuts that work in any window.", False),
        ("portal_wallpaper", "Wallpaper", "Lets the program ask to change your desktop wallpaper.", False),
        ("portal_launcher", "Create launchers", "Lets the program ask to add its own entries to your app menu.", False),
        ("portal_remote_desktop", "Remote desktop and input control", "Lets the program ask to control your mouse and keyboard. Far-reaching access.", False),
        ("portal_input_capture", "Input capture", "Lets the program ask to capture your keyboard and mouse. Far-reaching access.", False),
        ("portal_background", "Run in the background", "Lets the program ask to keep running when closed or to start at login. The autostart it registers runs outside the sandbox.", False),
        ("portal_usb", "USB devices", "Lets the program ask to use a USB device. The desktop shows its own prompt.", False),
        ("portal_clipboard", "Shared clipboard", "Lets a remote-desktop session read and set your clipboard (needs Remote desktop and input control).", False),
        ("portal_secret", "Secrets", "Lets the program ask the desktop for stored secrets (keyring). Far-reaching access.", False)]),
    ("Hardening", [
        ("private", "Temporary home", "Everything the program saves in its private home is erased when it closes. Shared folders are not affected.", False),
        ("hostname", "Hide the computer name", "The program sees the name sbx, also in /etc/hostname and /etc/hosts", False),
        ("privacy", "Hide your real name", "The program sees a minimal /etc/passwd: your username, root and nobody", False),
        ("userns", "Allow the program's own sandbox", "Chromium, Electron and Flatpak programs need this. Turn off for stricter isolation.", True),
        ("folder", "Start in the program's own folder", "For programs that read or create files in the folder they start in. The folder the program file sits in is shared read-write and the program starts there. Put the program in a folder of its own, or a subfolder: your home, Downloads, Desktop, Documents and hidden folders are never shared. On by itself when the folder is named after the program; switch it off here", False),
        ("single_instance", "Open files in the running copy", "A second launch of the program (for example a browser opened by double-clicking a file) finds the first one through a private temp folder shared by this program's sandboxes only. /tmp stays private.", False),
        ("allow_sensitive", "Allow sharing secret folders", "Lets you share ~/.ssh, ~/.gnupg and similar. Only if you really mean it", False)]),
]
WL_RESTRICTED = False      # can this desktop restrict Wayland programs (wp_security_context_v1)? Learned from `sbx schema`; wayland_strict is on by default only where it can
GROUP_INFO = {"What can this program use?": "Changes apply the next time the program starts. Everything else on your computer stays hidden from it."}      # a line under a group's title
ADVANCED = ("portal_file_transfer", "portal_notification", "portal_openuri", "portal_print", "portal_trash", "portal_email", "portal_inhibit", "portal_network", "portal_location", "portal_shortcuts", "portal_wallpaper", "portal_launcher", "portal_remote_desktop", "portal_input_capture", "portal_secret", "portal_background", "portal_usb", "portal_clipboard")      # shown in one collapsed row, "More desktop services"
SWITCHES = {k: (t, s, d) for _, rows in GROUPS for k, t, s, d in rows}
SHORT = {"net": "network", "net_private": "this computer private", "wayland": "Wayland", "wayland_strict": "strict Wayland", "x11": "X11", "gpu": "graphics", "gpu_render_only": "render-only graphics",
         "sound": "sound", "portals": "portals", "private": "private home", "hostname": "hostname", "privacy": "real name hidden", "userns": "user namespaces",
         "theme": "theme", "camera": "camera", "alsa": "ALSA", "input": "input devices", "kvm": "KVM", "rocm": "ROCm",
         "printing": "printing", "ssh_agent": "SSH agent", "allow_sensitive": "secret folders", "compat": "compat variables",
         "portal_notification": "notifications", "portal_openuri": "open links", "portal_print": "print dialog", "portal_trash": "trash",
         "portal_email": "email", "portal_inhibit": "keep awake", "portal_network": "network state", "portal_screenshot": "screenshots", "portal_screencast": "screen sharing", "portal_location": "location",
         "tray": "tray icon", "a11y": "screen reader", "gnome_shell": "GNOME Shell extensions", "gnome_shell_manage": "manage extensions", "kde": "KDE Plasma integration", "portal_shortcuts": "global shortcuts", "portal_wallpaper": "wallpaper", "portal_launcher": "create launchers",
         "folder": "own folder", "portal_remote_desktop": "remote desktop", "portal_input_capture": "input capture", "portal_secret": "secrets", "portal_background": "background", "portal_usb": "USB devices", "portal_clipboard": "shared clipboard", "portal_file_transfer": "drag and drop"}
SENSITIVE = [".ssh", ".gnupg", ".aws"]          # replaced by the real list from sbx
SYSTEM_PROGRAMS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/opt/", "/snap/", "/nix/store/", "/gnu/store/", "/run/current-system/", "/var/lib/flatpak/")   # same as in sbx: where a system program lives
SOURCE_RANK = {"System": 0, "Downloaded": 2}   # same order as `sbx list --all`: of the programs only run from the terminal, System first, Downloaded last
LISTS = ("whitelist", "read_only")
# Resource limits: (key in the settings file, row title, subtitle, largest value, step). A row shows memory in MB, cpu in % of one core and tasks as a count;
# the file takes what sbx takes (2G, 50%, 200%, 512). The patterns are replaced by sbx's own, see load_schema.
LIMIT_ROWS = (("memory", "Memory limit (MB)", "The program can't use more memory than this.", 262144, 256),
              ("cpu", "CPU limit (% of one core)", "100 is one full core, 400 is four.", 6400, 25),
              ("tasks", "Process limit", "Most processes and threads the program may have at once.", 100000, 50))
LIMIT_PATTERNS = {"memory": r"\d+[KMGT]?|\d+%", "tasks": r"\d+", "cpu": r"\d+%"}
HINT_LISTS = ("setenv",)        # lists a troubleshooting hint may add to
ENV_PROTECTED = ("HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "SBX_ACTIVE")    # same as in sbx: env= and setenv= can't touch these
# The window edits whitelist, read_only, setenv and env; allow_syscall and the limits stay in the settings file (other lines are kept as they are).
# What the summary line calls the switches that give a program something: key -> words.
CAN = (("gpu", "graphics"), ("sound", "sound"), ("camera", "camera"), ("input", "controllers"), ("alsa", "ALSA sound"), ("printing", "printing"),
       ("x11", "X11"), ("portals", "desktop portals"), ("tray", "tray icon"), ("a11y", "screen reader"), ("gnome_shell", "GNOME Shell extensions"), ("kde", "KDE Plasma integration"), ("kvm", "virtual machines"), ("rocm", "AMD compute"), ("ssh_agent", "your SSH agent"))
DESKTOP_NAMES = {"GNOME": "GNOME Shell", "KDE": "KDE Plasma"}      # desktop -> the title of its expander; a new desktop is one line here and one in DESKTOP_ONLY
DESKTOP_ONLY = {"gnome_shell": "GNOME", "gnome_shell_manage": "GNOME", "kde": "KDE"}      # switch -> the desktop it is for; hidden on the other one (see desktop_hidden())
NEEDS = {"gnome_shell_manage": ("gnome_shell",), "net_private": ("net",), "gpu_render_only": ("gpu", "rocm"), "wayland_strict": ("wayland",)}      # key -> switches of which one must be on for it to do anything (portal_* need portals, see needs())
# Troubleshooting hints: a pattern in a failed run's log -> the one setting that usually fixes it. Each entry is
# (pattern, setting, wanted value, title, why, only while these hold). A hint is shown only if the setting is not already as wanted.
# SETTING is a switch (WANTED True/False), a list key such as "setenv" (WANTED "NAME=value", added by the button), or None for
# advice with no button (WANTED None: something sbx cannot save as a setting). Nothing is ever changed without a click.
LOG_HINTS = [
    (r"no usable sandbox|suid sandbox|setuid sandbox|failed to move to a new (user )?namespace|clone.{0,40}operation not permitted",
     "userns", True, "Allow the program's own sandbox",
     "The program tried to create its own sandbox and could not. Chromium, Electron and Flatpak programs need this.", {}),
    (r"libegl warning|mesa: error|/dev/dri|failed to create (an )?(egl|gl)|no (suitable|available|compatible) (gpu|vulkan|adapter)",
     "gpu", True, "Turn on the graphics card", "The program could not find a graphics device.", {}),
    (r"libegl warning|mesa: error|/dev/dri|failed to create (an )?(egl|gl)|no (suitable|available|compatible) (gpu|vulkan|adapter)",
     "gpu_render_only", False, "Turn off \"Graphics: render only\"",
     "The program may need a graphics device that this mode hides.", {"gpu": True}),
    (r"failed to connect to (the )?wayland|wayland.{0,30}(connection|display).{0,30}(fail|refused)",
     "wayland", True, "Turn on Wayland", "The program looked for Wayland, which is off. Better than X11 where it works.", {}),
    (r"cannot open display|could not connect to display|unable to open display|platform plugin .?xcb|failed to connect to (the )?wayland",
     "x11", True, "Turn on X11", "The program looks for X11. This weakens isolation, so use it only for this program.", {}),
    (r"temporary failure in name resolution|name or service not known|network is unreachable",
     "net", True, "Allow the network", "The program tried to use the network.", {}),
    (r"/dev/video\d|no (such )?(video )?(capture )?device.{0,30}camera|camera.{0,30}(not found|no device)",
     "camera", True, "Allow the camera", "The program tried to open a camera.", {}),
    # A wgpu program whose shader needs a feature the chosen graphics adapter lacks. Tried on a real case: OpenGL got past it and
    # keeps the card; for iced programs the processor renderer is the second choice. Both are listed, OpenGL first.
    (r"wgpu error: validation error[\s\S]*requires capability",
     "setenv", "WGPU_BACKEND=gl", "Draw with OpenGL instead of Vulkan (a guess)",
     "The program's graphics library chose a Vulkan device that lacks a feature the program needs. OpenGL keeps the graphics card and often works.", {}),
    (r"wgpu error: validation error[\s\S]*iced_wgpu|iced_wgpu[\s\S]*wgpu error: validation error",
     "setenv", "ICED_BACKEND=tiny-skia", "If that does not help: draw on the processor (a guess)",
     "The program draws with iced, which can skip the graphics card entirely. Slower, but it needs nothing from the card.", {}),
    # A Rust panic on a file that is not there: sbx starts a program in its private home, so files it expects in the folder it was
    # started from are missing. "folder" starts it in the folder its file sits in (never Downloads or the home folder).
    (r"panicked at [^\n]*\n[^\n]*kind: NotFound|no such file or directory",
     "folder", True, "Start it in the program's own folder (a guess)",
     "The program looked for a file and it was not there. A program starts in its private home, so files next to it are missed. This starts it in the folder its file sits in, which only works from a folder of its own (a subfolder): your home, Downloads and hidden folders are never shared. In a terminal, sbx --here ./NAME starts it in the folder you are in.", {}),
    # A silent crash (segfault, abort, trap) while user namespaces are off: Chrome, Chromium and Electron die like this when they
    # cannot make their own sandbox, often with only a core dump and no message. Shown only while "userns" is off.
    (r"\] EXIT code=(13[2-6]|139|159)\b",
     "userns", True, "Allow the program's own sandbox (a guess)",
     "The program crashed and left no reason in the log. Chrome, Chromium and Electron crash like this when they cannot create their own sandbox, and user namespaces are off for this program.", {"userns": False}),
    # A guess, last so any specific X11 message above wins: a crash right after start, with nothing in the log saying why
    # (installers often send stderr to /dev/null). A GUI that only speaks X11 dies like this.
    (r"aborted \(core dumped\)|\] EXIT code=134\b",
     "x11", True, "Try X11 (a guess)",
     "The program crashed right after starting and left no reason in the log. It may be a GUI that needs X11. This weakens isolation, so use it only for this program.", {}),
]
# Log page colors: the first pattern that matches a line's text picks its kind (see log_line_parts).
LOG_KINDS = [
    (r"^EXIT code=0\b", "exit_ok"),
    (r"^EXIT code=", "exit_err"),
    (r"^START\b", "info"),
    (r"^WARNING\b|\bwarning\b", "warn"),
    (r"^ALSA lib\b", "dim"),
    (r"\bpanicked\b|\bfatal\b|\berror\b|\bfailed\b|\bcannot\b|\bdenied\b|not permitted|^Caused by|^note: ", "err"),
]
LOG_COLORS = {      # kind -> (light theme, dark theme); readable on both
    "ts": ("#77767b", "#9a9996"), "name": ("#613583", "#dc8add"), "dim": ("#77767b", "#9a9996"),
    "info": ("#1c71d8", "#99c1f1"), "warn": ("#9c5a00", "#f8e45c"), "err": ("#c01c28", "#ff7b63"),
    "exit_ok": ("#1a7f4b", "#8ff0a4"), "exit_err": ("#c01c28", "#ff7b63"),
}
CURRENT = []                                              # the scope (None or a name) of the page being shown
DEFAULTS_TITLE = "All programs (default)"                 # the first row of the list and the title of its page: the settings every program starts from
