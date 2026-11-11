#| 10_tables: Wording for every switch (GROUPS, SWITCHES, SHORT) and the other constants the window starts from. Data only.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# Wording for each switch: key, title, subtitle, default. The list of switches itself comes from `sbx schema` (see
# load_schema), so a switch added to sbx shows up here at once, under "Other", even before it has wording of its own.
GROUPS = [
    ("Network", [("net", "Network", "Internet and your local network", True)]),
    ("Display", [
        ("wayland", "Wayland", "Lets graphical programs open a window", True),
        ("wayland_strict", "Wayland only if restricted", "No Wayland at all when the compositor can't limit what the program may do", False),
        ("x11", "X11", "Weaker isolation than Wayland", False),
        ("theme", "Your fonts, icons and GTK settings", "Shown read-only so the program looks like your desktop, with your window buttons (minimize, maximize) and cursor", True),
        ("compat", "Graphics and toolkit variables", "Passes WGPU_*, MESA_*, SDL_*, QT_QPA_* and similar if set. Never tokens or other variables", False)]),
    ("Devices", [
        ("gpu", "Graphics card", "/dev/dri and NVIDIA devices", True),
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
        ("userns", "Allow user namespaces", "Turn off to stop the program creating nested sandboxes. Chromium, Electron and Flatpak need this on", True),
        ("allow_sensitive", "Allow sharing secret folders", "Lets you share ~/.ssh, ~/.gnupg and similar. Only if you really mean it", False)]),
]
SWITCHES = {k: (t, s, d) for _, rows in GROUPS for k, t, s, d in rows}
SHORT = {"net": "network", "wayland": "Wayland", "wayland_strict": "strict Wayland", "x11": "X11", "gpu": "graphics",
         "sound": "sound", "portals": "portals", "private": "private home", "hostname": "hostname", "userns": "user namespaces",
         "theme": "theme", "camera": "camera", "alsa": "ALSA", "input": "input devices", "kvm": "KVM", "rocm": "ROCm",
         "printing": "printing", "ssh_agent": "SSH agent", "allow_sensitive": "secret folders", "compat": "compat variables",
         "portal_screenshot": "screenshots", "portal_screencast": "screen sharing", "portal_location": "location",
         "tray": "tray icon", "portal_shortcuts": "global shortcuts", "portal_wallpaper": "wallpaper", "portal_launcher": "create launchers",
         "portal_remote_desktop": "remote desktop", "portal_input_capture": "input capture", "portal_secret": "secrets"}
SENSITIVE = [".ssh", ".gnupg", ".aws"]          # replaced by the real list from sbx
LISTS = ("whitelist", "read_only")
CURRENT = []                                              # the scope (None or a name) of the page being shown
