#| 10_tables: Declarative policy: blocked system calls, hidden paths, env allow-lists, secret folders, the settings schema. Data only, no code runs here.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# Blocked system calls, as name: (x86_64 number, aarch64 number, i386 number). The first group is the original list (same as ajail).
# The second group is kernel attack surface that desktop programs rarely need; allow_syscall=NAME turns one back on
# (io_uring stands for the three io_uring_* calls).
BASE_SYSCALLS = {
    "kexec_load": (246, 104, 283), "kexec_file_load": (320, 294, None), "perf_event_open": (298, 241, 336), "bpf": (321, 280, 357),
    "syslog": (103, 116, 103), "uselib": (134, None, 86), "acct": (163, 89, 51), "quotactl": (179, 60, 131), "add_key": (248, 217, 286),
    "keyctl": (250, 219, 288), "request_key": (249, 218, 287), "move_pages": (279, 239, 317), "mbind": (237, 235, 274),
    "get_mempolicy": (239, 236, 275), "set_mempolicy": (238, 237, 276), "migrate_pages": (256, 238, 294),
    "open_by_handle_at": (304, 265, 342), "name_to_handle_at": (303, 264, 341)}
EXTRA_SYSCALLS = {
    "userfaultfd": (323, 282, 374), "process_vm_readv": (310, 270, 347), "process_vm_writev": (311, 271, 348), "kcmp": (312, 272, 349),
    "pidfd_getfd": (438, 438, 438), "io_uring_setup": (425, 425, 425), "io_uring_enter": (426, 426, 426), "io_uring_register": (427, 427, 427),
    "open_tree": (428, 428, 428), "move_mount": (429, 429, 429), "fsopen": (430, 430, 430), "fsconfig": (431, 431, 431), "fsmount": (432, 432, 432),
    "fspick": (433, 433, 433), "mount_setattr": (442, 442, 442)}
IO_URING = ("io_uring_setup", "io_uring_enter", "io_uring_register")
ARCHES = {"x86_64": (0xC000003E, 0), "aarch64": (0xC00000B7, 1)}        # machine: (audit arch, column in the tables above)
# socket() only for unix, inet, inet6 and netlink; any other family (AF_ALG, AF_VSOCK, AF_PACKET...) gets EPERM.
SOCKET_NR = {"x86_64": 41, "aarch64": 198}
# i386 on x86_64: (audit arch, column, socket nr, socketcall nr). socketcall() args sit in memory, so the filter can't check them.
COMPAT32 = {"x86_64": (0x40000003, 2, 359, 102)}
SOCKET_FAMILIES = (1, 2, 10, 16)

# Existing system files and folders hidden from the program (a file is replaced by an empty one, a folder by an empty tmpfs).
SECRETS = ["/etc/shadow", "/etc/shadow-", "/etc/gshadow", "/etc/gshadow-", "/etc/sudoers", "/etc/sudoers.d",
           "/etc/security/opasswd", "/etc/ssl/private", "/etc/NetworkManager/system-connections"]
SECRET_GLOBS = ["/etc/ssh/ssh_host_*_key"]
# Folders emptied so other drives, other users, logs and mail stay out of sight (/home is added below if it is a real folder).
EMPTY_DIRS = ["/root", "/mnt", "/media", "/srv", "/var/tmp", "/var/log", "/var/mail", "/var/spool/mail"]
HIDE_SYS = ["/sys/devices/virtual/dmi/id"]       # serial numbers, product and vendor names; /sys/class/net too when net=0 or net_private=1
DRIVES = ("/mnt/", "/media/", "/run/media/")
OUTSIDE = DRIVES + ("/tmp/",)                    # places outside the home that can be shared
ENV_KEEP = ("LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_TIME", "TZ", "TERM", "COLORTERM",
            "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XCURSOR_THEME", "XCURSOR_SIZE")
# Variables that only tune graphics, windowing and toolkits (no tokens, keys, accounts or paths into your home). Passed through
# when compat=1 / --compat, and only if they are set.
COMPAT_ENV = ("WGPU_BACKEND", "WGPU_ADAPTER_NAME", "WGPU_POWER_PREF", "ICED_BACKEND", "LIBGL_ALWAYS_SOFTWARE", "LIBGL_DRI3_DISABLE",
              "VK_ICD_FILENAMES", "VK_DRIVER_FILES", "__GLX_VENDOR_LIBRARY_NAME", "__NV_PRIME_RENDER_OFFLOAD", "DRI_PRIME",
              "SDL_VIDEODRIVER", "SDL_AUDIODRIVER", "QT_QPA_PLATFORM", "QT_QPA_PLATFORMTHEME", "QT_SCALE_FACTOR", "GDK_BACKEND",
              "GDK_SCALE", "MOZ_ENABLE_WAYLAND")
COMPAT_PREFIXES = ("MESA_", "RADV_")
ENV_PROTECTED = ("HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "SBX_ACTIVE")    # env= and setenv= can't touch these
# Extra portals that stay off until their switch is on (only with portals=1).
PORTAL_EXTRA = {"portal_remote_desktop": "RemoteDesktop", "portal_input_capture": "InputCapture",
                "portal_launcher": "DynamicLauncher", "portal_background": "Background"}      # Background: RequestBackground can register an autostart that runs outside the sandbox
# Extra portals that are on with portals=1 unless portal_*=0 (portal_file_transfer too).
PORTAL_DEFAULT_ON = {"portal_screenshot": "Screenshot", "portal_screencast": "ScreenCast", "portal_location": "Location",
                     "portal_shortcuts": "GlobalShortcuts", "portal_usb": "Usb", "portal_clipboard": "Clipboard", "portal_wallpaper": "Wallpaper", "portal_secret": "Secret"}
# Tray icon (tray=1). The proxy can only name a bus name exactly or with a "*" at the end after a dot, so the names an app
# may own are listed: org.kde.StatusNotifierItem-PID-ID. The PID is the one inside the sandbox (it has its own PID
# namespace), so it is small. An app whose main process has a higher PID gets no tray icon; the range can be raised here.
TRAY_PIDS = range(2, 65)
TRAY_IDS = range(1, 4)
# Screen reader (a11y=1). AT_SPI_BUS_ADDRESS (where the program finds the accessibility bus) is set by sbx. These two make toolkits that would
# otherwise ask the desktop whether a screen reader is on (they can't reach it from the sandbox) turn their accessibility support on: Qt, and
# Firefox and other GNOME programs.
A11Y_ENV = {"QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1", "GNOME_ACCESSIBILITY": "1"}
# Settings (key=value files) and their defaults. Lists: LISTS. Text values: LIMITS (a pattern each value must match).
SCALARS = {"net": True, "net_private": False, "private": False, "wayland": True, "wayland_strict": False, "x11": False, "gpu": True,
           "sound": True, "gpu_render_only": False, "portals": True, "hostname": False, "privacy": False, "userns": False, "allow_32bit": False, "theme": True, "camera": False,
           "alsa": False, "input": False, "kvm": False, "ntsync": False, "rocm": False, "printing": False, "ssh_agent": False, "allow_sensitive": False,
           "compat": False, "tray": False, "mpris": False, "a11y": False, "gnome_shell": False, "gnome_shell_manage": False, "kde": False, "folder": False, "single_instance": False, "bluetooth": False, "battery": False, "avahi": False, **{k: False for k in PORTAL_EXTRA}}
# Your real folders that get a per-program subfolder (<folder>/sbx/NAME) mounted over the same folder in the private home: name -> XDG key.
SHARE_DIRS = {"Downloads": "DOWNLOAD", "Documents": "DOCUMENTS", "Pictures": "PICTURES", "Music": "MUSIC", "Videos": "VIDEOS"}
SHARE_DEFAULT = ",".join(SHARE_DIRS)
_SHARE_ONE = "(?:" + "|".join(SHARE_DIRS) + ")"
LISTS = ("whitelist", "read_only", "env", "setenv", "allow_syscall", "talk")
# gtk_portal is a value too: auto (or no line), 1 or 0. With portals=1 it decides whether GTK_USE_PORTAL=1 is set: auto sets it, except
# when the host's cursor size is 0 (the one confirmed GTK crash); 1 and 0 force it on or off.
LIMITS = {"memory": r"\d+[KMGT]?|\d+%", "tasks": r"\d+", "cpu": r"\d+%", "gtk_portal": r"auto|0|1",
          "share_dirs": _SHARE_ONE + r"(?:\s*,\s*" + _SHARE_ONE + ")*"}
# Folders in your home that hold secrets. Sharing one (or a folder around one) needs allow_sensitive=1 / --allow-sensitive.
SENSITIVE = [".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".netrc", ".git-credentials", ".password-store", ".config/gcloud",
             ".config/gh", ".config/rclone", ".local/share/keyrings", ".mozilla", ".thunderbird", ".config/google-chrome",
             ".config/chromium", ".config/BraveSoftware", ".config/op", ".config/Bitwarden", ".var/app/org.mozilla.firefox",
             "snap/firefox/common/.mozilla", ".npmrc", ".pypirc", ".cargo/credentials", ".cargo/credentials.toml",
             ".terraform.d/credentials.tfrc.json", ".bash_history", ".zsh_history", ".config/Signal", ".config/discord", ".bitcoin",
             ".electrum", ".ethereum", ".pgpass", ".pg_service.conf", ".my.cnf", ".mylogin.cnf", ".psql_history", ".mysql_history",
             ".dockercfg", ".config/containers/auth.json", ".yarnrc", ".yarnrc.yml", ".gem/credentials", ".config/composer/auth.json",
             ".composer/auth.json", ".m2/settings.xml", ".m2/settings-security.xml", ".gradle/gradle.properties", ".config/pip/pip.conf",
             ".pip/pip.conf", ".config/NuGet/NuGet.Config", ".nuget/NuGet/NuGet.Config", ".config/helm/repositories.yaml", ".mail",
             ".mutt", ".muttrc", ".config/mutt", ".config/neomutt", ".config/aerc", ".msmtprc", ".mbsyncrc", ".offlineimaprc",
             ".fetchmailrc", ".authinfo", ".authinfo.gpg", ".config/thunderbird", "snap/thunderbird/common/.thunderbird",
             ".config/evolution", ".local/share/evolution", ".config/geary", ".local/share/geary", ".local/share/kmail2",
             ".local/share/akonadi", ".config/kmail2rc", ".var/app/org.mozilla.Thunderbird", ".var/app/org.gnome.Evolution",
             ".var/app/org.gnome.Geary", ".config/Slack", ".config/Element", ".config/Session", ".config/Mattermost",
             ".config/Rocket.Chat", ".config/Zulip", ".config/teams-for-linux", ".config/discordcanary", ".config/discordptb",
             ".config/vesktop", ".local/share/TelegramDesktop", ".local/share/signal-cli", ".purple", ".weechat", ".config/weechat",
             ".irssi", ".var/app/org.signal.Signal", ".var/app/com.discordapp.Discord", ".var/app/org.telegram.desktop",
             ".var/app/com.slack.Slack", ".var/app/im.riot.Element"]
# User fonts, icons, themes and toolkit settings, shown read-only to programs that can draw a window (theme=1).
THEME = [".local/share/fonts", ".fonts", ".local/share/icons", ".icons", ".local/share/themes", ".themes",
         ".config/gtk-3.0/settings.ini", ".config/gtk-3.0/gtk.css", ".config/gtk-3.0/colors.css", ".config/gtk-4.0/settings.ini",
         ".config/gtk-4.0/gtk.css", ".config/gtk-4.0/colors.css", ".config/fontconfig", ".cache/fontconfig",
         ".config/kdeglobals", ".config/kdedefaults", ".local/share/color-schemes"]      # KDE: colors, icon theme, fonts
# Desktop portals a program may call when portals=1 (through xdg-dbus-proxy, which runs outside the sandbox).
# Everything else (Documents, ...) stays blocked, and so do the PORTAL_EXTRA ones until their own switch (portal_*=1) is on.
PORTAL_NAME = "org.freedesktop.portal.Desktop"
# FileTransfer (drag and drop from other programs) is served by the Documents portal, under another bus name and path.
PORTAL_DOCS_NAME = "org.freedesktop.portal.Documents"
PORTAL_DOCS_PATH = "/org/freedesktop/portal/documents"
PORTAL_BASIC = ("Settings", "OpenURI", "FileChooser", "Notification", "Print", "Inhibit", "Account",
                "Email", "Trash", "NetworkMonitor", "ProxyResolver", "GameMode", "MemoryMonitor",
                "PowerProfileMonitor", "Realtime")
# Basic portals that can be switched off one by one (all on, and only used when portals=1): setting -> portal interfaces. The file chooser
# and Settings (dark mode) stay in the base; the rest of PORTAL_BASIC (Account follows privacy, Game/Memory/Power/Realtime) stays as is.
PORTAL_TOGGLES = {"portal_notification": ("Notification",), "portal_openuri": ("OpenURI",), "portal_print": ("Print",),
                  "portal_trash": ("Trash",), "portal_email": ("Email",), "portal_inhibit": ("Inhibit",),
                  "portal_network": ("NetworkMonitor", "ProxyResolver")}
SCALARS.update({k: True for k in (*PORTAL_TOGGLES, *PORTAL_DEFAULT_ON, "portal_file_transfer")})
SYSTEM_PROGRAMS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/opt/", "/snap/", "/nix/store/", "/gnu/store/", "/run/current-system/", "/var/lib/flatpak/")   # a program name run from here may move between them
NIX_LINKS = ("/run/current-system", "/run/booted-system", "/run/opengl-driver", "/run/opengl-driver-32")   # NixOS and Guix
