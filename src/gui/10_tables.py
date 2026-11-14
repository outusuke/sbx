#| 10_tables: Wording for every switch (GROUPS, SWITCHES, SHORT) and the other constants the window starts from. Data only.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# Wording for each switch: key, title, subtitle, default. The list of switches itself comes from `sbx schema` (see
# load_schema), so a switch added to sbx shows up here at once, under "Other", even before it has wording of its own.
GROUPS = [
    ("Network", [
        ("net", "Network", "Internet and your local network", True),
        ("net_private", "Keep this computer private", "Internet only: the program can't reach services running on this computer (databases, dev servers). Other devices on your local network are still reachable. Needs pasta (passt). Works only when “Network” is on", False)]),
    ("Display", [
        ("wayland", "Wayland", "Lets graphical programs open a window", True),
        ("wayland_strict", "Wayland only if restricted", "No Wayland at all when the compositor can't limit what the program may do", False),
        ("x11", "X11", "Weaker isolation than Wayland", False),
        ("theme", "Your fonts, icons and GTK settings", "Shown read-only so the program looks like your desktop, with your window buttons (minimize, maximize) and cursor", True),
        ("compat", "Graphics and toolkit variables", "Passes WGPU_*, MESA_*, SDL_*, QT_QPA_* and similar if set. Never tokens or other variables", False)]),
    ("Devices", [
        ("gpu", "Graphics card", "/dev/dri and NVIDIA devices", True),
        ("gpu_render_only", "Graphics: render only", "Of /dev/dri, share only the render nodes that programs draw with, not the card nodes that can drive the display. Does not limit NVIDIA devices. Only matters while “Graphics card” (or ROCm) is on", False),
        ("sound", "Sound", "PipeWire and PulseAudio, which includes the microphone", True),
        ("alsa", "ALSA sound devices", "/dev/snd, for programs that don't use PipeWire or PulseAudio", False),
        ("camera", "Camera", "/dev/video*", False),
        ("input", "Game controllers and input devices", "/dev/input", False),
        ("rocm", "AMD compute (ROCm)", "/dev/kfd", False),
        ("kvm", "Virtual machines", "/dev/kvm", False),
        ("printing", "Printing", "The CUPS socket", False),
        ("ssh_agent", "SSH agent", "The program can sign in as you while it runs. Far-reaching access", False)]),
    ("Desktop", [
        ("portals", "Notifications, links and file chooser", "Lets the program show notifications, open links, pick files and print through your desktop. Needs xdg-dbus-proxy", False),
        ("portal_screenshot", "Screenshots", "Lets the program ask the desktop for a screenshot. Works only when “Notifications, links and file chooser” is on", False),
        ("portal_screencast", "Screen sharing", "Lets the program ask to share your screen or a window (Discord, Teams, Slack). Works only when “Notifications, links and file chooser” is on", False),
        ("portal_location", "Location", "Lets the program ask the desktop where you are. Works only when “Notifications, links and file chooser” is on", False),
        ("portal_shortcuts", "Global shortcuts", "Keyboard shortcuts that work in any window. Works only when “Notifications, links and file chooser” is on", False),
        ("portal_wallpaper", "Wallpaper", "Lets the program ask to change your desktop wallpaper. Works only when “Notifications, links and file chooser” is on", False),
        ("portal_launcher", "Create launchers", "Lets the program ask to add its own entries to your app menu. Works only when “Notifications, links and file chooser” is on", False),
        ("portal_remote_desktop", "Remote desktop and input control", "Lets the program ask to control your mouse and keyboard. Far-reaching access. Works only when “Notifications, links and file chooser” is on", False),
        ("portal_input_capture", "Input capture", "Lets the program ask to capture your keyboard and mouse. Far-reaching access. Works only when “Notifications, links and file chooser” is on", False),
        ("portal_secret", "Secrets", "Lets the program ask the desktop for stored secrets (keyring). Far-reaching access. Works only when “Notifications, links and file chooser” is on", False),
        ("tray", "Tray icon and media keys", "Lets the program show a tray icon and answer media keys. Needs xdg-dbus-proxy", False)]),
    ("Privacy and safety", [
        ("private", "Private home erased on exit", "Nothing the program saves in its home is kept", False),
        ("hostname", "Hide the computer name", "The program sees the name sbx, also in /etc/hostname and /etc/hosts", False),
        ("privacy", "Hide your real name", "The program sees a minimal /etc/passwd: your username, root and nobody. Your full name and the other accounts on this computer stay out", False),
        ("userns", "Allow user namespaces", "Turn off to stop the program creating nested sandboxes. Chromium, Electron and Flatpak need this on", True),
        ("allow_sensitive", "Allow sharing secret folders", "Lets you share ~/.ssh, ~/.gnupg and similar. Only if you really mean it", False)]),
]
SWITCHES = {k: (t, s, d) for _, rows in GROUPS for k, t, s, d in rows}
SHORT = {"net": "network", "net_private": "this computer private", "wayland": "Wayland", "wayland_strict": "strict Wayland", "x11": "X11", "gpu": "graphics", "gpu_render_only": "render-only graphics",
         "sound": "sound", "portals": "portals", "private": "private home", "hostname": "hostname", "privacy": "real name hidden", "userns": "user namespaces",
         "theme": "theme", "camera": "camera", "alsa": "ALSA", "input": "input devices", "kvm": "KVM", "rocm": "ROCm",
         "printing": "printing", "ssh_agent": "SSH agent", "allow_sensitive": "secret folders", "compat": "compat variables",
         "portal_screenshot": "screenshots", "portal_screencast": "screen sharing", "portal_location": "location",
         "tray": "tray icon", "portal_shortcuts": "global shortcuts", "portal_wallpaper": "wallpaper", "portal_launcher": "create launchers",
         "portal_remote_desktop": "remote desktop", "portal_input_capture": "input capture", "portal_secret": "secrets"}
SENSITIVE = [".ssh", ".gnupg", ".aws"]          # replaced by the real list from sbx
SYSTEM_PROGRAMS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/opt/", "/snap/", "/nix/store/", "/gnu/store/", "/run/current-system/", "/var/lib/flatpak/")   # same as in sbx: where a system program lives
SOURCE_RANK = {"System": 0, "Downloaded": 2}   # same order as `sbx list --all`: of the programs only run from the terminal, System first, Downloaded last
LISTS = ("whitelist", "read_only")
HINT_LISTS = ("setenv",)        # lists a troubleshooting hint may add to; the window edits no other list of sbx's (its lines are kept as they are)
# What the summary line calls the switches that give a program something: key -> words.
CAN = (("gpu", "graphics"), ("sound", "sound"), ("camera", "camera"), ("input", "controllers"), ("alsa", "ALSA sound"), ("printing", "printing"),
       ("x11", "X11"), ("portals", "desktop portals"), ("tray", "tray icon"), ("kvm", "virtual machines"), ("rocm", "AMD compute"), ("ssh_agent", "your SSH agent"))
NEEDS = {"net_private": ("net",), "gpu_render_only": ("gpu", "rocm")}      # key -> switches of which one must be on for it to do anything (portal_* need portals, see needs())
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
    # An iced program whose GPU renderer failed a shader check (the GPU or driver lacks a feature): iced can draw on the CPU instead.
    (r"wgpu error: validation error[\s\S]*iced_wgpu|iced_wgpu[\s\S]*wgpu error: validation error",
     "setenv", "ICED_BACKEND=tiny-skia", "Draw on the processor instead of the graphics card (a guess)",
     "The program draws with iced, and its graphics-card renderer failed a check the card or driver could not pass. This makes it draw on the processor: slower, but it needs nothing from the card.", {}),
    # A Rust panic on a file that is not there: sbx starts a program in its private home, so files it expects in the folder it was
    # started from are missing. --here is a command-line option only (it depends on where you start), so there is no button.
    (r"panicked at [^\n]*\n[^\n]*kind: NotFound",
     None, None, "Start it from its own folder with --here (a guess)",
     "The program looked for a file and it was not there. A program starts in its private home, so files next to it are missed. In a terminal, go to the program's folder and run: sbx --here ./NAME", {}),
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
