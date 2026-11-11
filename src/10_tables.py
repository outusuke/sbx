#| 10_tables: Declarative policy: blocked system calls, hidden paths, env allow-lists, secret folders, the settings schema. Data only, no code runs here.
#| Layer rule: this fragment may use names from lower-numbered fragments (and its own) only; build.py enforces it.

# Blocked system calls, as name: (x86_64 number, aarch64 number). The first group is the original list (same as ajail).
# The second group is kernel attack surface that desktop programs rarely need; allow_syscall=NAME turns one back on
# (io_uring stands for the three io_uring_* calls).
BASE_SYSCALLS = {
    "kexec_load": (246, 104), "kexec_file_load": (320, 294), "perf_event_open": (298, 241), "bpf": (321, 280),
    "syslog": (103, 116), "uselib": (134, None), "acct": (163, 89), "quotactl": (179, 60), "add_key": (248, 217),
    "keyctl": (250, 219), "request_key": (249, 218), "move_pages": (279, 239), "mbind": (237, 235),
    "get_mempolicy": (239, 236), "set_mempolicy": (238, 237), "migrate_pages": (256, 238),
    "open_by_handle_at": (304, 265), "name_to_handle_at": (303, 264)}
EXTRA_SYSCALLS = {
    "userfaultfd": (323, 282), "process_vm_readv": (310, 270), "process_vm_writev": (311, 271), "kcmp": (312, 272),
    "pidfd_getfd": (438, 438), "io_uring_setup": (425, 425), "io_uring_enter": (426, 426), "io_uring_register": (427, 427),
    "open_tree": (428, 428), "move_mount": (429, 429), "fsopen": (430, 430), "fsconfig": (431, 431), "fsmount": (432, 432),
    "fspick": (433, 433), "mount_setattr": (442, 442)}
IO_URING = ("io_uring_setup", "io_uring_enter", "io_uring_register")
ARCHES = {"x86_64": (0xC000003E, 0), "aarch64": (0xC00000B7, 1)}        # machine: (audit arch, column in the tables above)

# Existing system files and folders hidden from the program (a file is replaced by an empty one, a folder by an empty tmpfs).
SECRETS = ["/etc/shadow", "/etc/shadow-", "/etc/gshadow", "/etc/gshadow-", "/etc/sudoers", "/etc/sudoers.d",
           "/etc/security/opasswd", "/etc/ssl/private", "/etc/NetworkManager/system-connections"]
SECRET_GLOBS = ["/etc/ssh/ssh_host_*_key"]
# Folders emptied so other drives, other users, logs and mail stay out of sight (/home is added below if it is a real folder).
EMPTY_DIRS = ["/root", "/mnt", "/media", "/srv", "/var/tmp", "/var/log", "/var/mail", "/var/spool/mail"]
HIDE_SYS = ["/sys/devices/virtual/dmi/id"]       # serial numbers, product and vendor names; /sys/class/net too when net=0
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
# Extra desktop portals, each its own switch (all off). They only count when portals=1.
PORTAL_EXTRA = {"portal_screenshot": "Screenshot", "portal_screencast": "ScreenCast", "portal_location": "Location",
                "portal_shortcuts": "GlobalShortcuts", "portal_remote_desktop": "RemoteDesktop",
                "portal_input_capture": "InputCapture", "portal_wallpaper": "Wallpaper",
                "portal_launcher": "DynamicLauncher", "portal_secret": "Secret"}
# Tray icon (tray=1). The proxy can only name a bus name exactly or with a "*" at the end after a dot, so the names an app
# may own are listed: org.kde.StatusNotifierItem-PID-ID. The PID is the one inside the sandbox (it has its own PID
# namespace), so it is small. An app whose main process has a higher PID gets no tray icon; the range can be raised here.
TRAY_PIDS = range(2, 65)
TRAY_IDS = range(1, 4)
# Settings (key=value files) and their defaults. Lists: LISTS. Text values: LIMITS (a pattern each value must match).
SCALARS = {"net": True, "private": False, "wayland": True, "wayland_strict": False, "x11": False, "gpu": True,
           "sound": True, "portals": False, "hostname": False, "userns": True, "theme": True, "camera": False,
           "alsa": False, "input": False, "kvm": False, "rocm": False, "printing": False, "ssh_agent": False, "allow_sensitive": False,
           "compat": False, "tray": False, **{k: False for k in PORTAL_EXTRA}}
LISTS = ("whitelist", "read_only", "env", "setenv", "allow_syscall")
LIMITS = {"memory": r"\d+[KMGT]?|\d+%", "tasks": r"\d+", "cpu": r"\d+%"}
# Folders in your home that hold secrets. Sharing one (or a folder around one) needs allow_sensitive=1 / --allow-sensitive.
SENSITIVE = [".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".netrc", ".git-credentials", ".password-store",
             ".config/gcloud", ".config/gh", ".config/rclone", ".local/share/keyrings", ".mozilla", ".thunderbird",
             ".config/google-chrome", ".config/chromium", ".config/BraveSoftware"]
# User fonts, icons, themes and toolkit settings, shown read-only to programs that can draw a window (theme=1).
THEME = [".local/share/fonts", ".fonts", ".local/share/icons", ".icons", ".local/share/themes", ".themes",
         ".config/gtk-3.0/settings.ini", ".config/gtk-3.0/gtk.css", ".config/gtk-4.0/settings.ini",
         ".config/gtk-4.0/gtk.css", ".config/fontconfig", ".cache/fontconfig"]
# Desktop portals a program may call when portals=1 (through xdg-dbus-proxy, which runs outside the sandbox).
# Everything else (Documents, ...) stays blocked, and so do the PORTAL_EXTRA ones until their own switch (portal_*=1) is on.
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_BASIC = ("Settings", "OpenURI", "FileChooser", "Notification", "Print", "Inhibit", "Background", "Account",
                "Email", "Trash", "NetworkMonitor", "ProxyResolver", "GameMode", "MemoryMonitor",
                "PowerProfileMonitor", "Realtime")
SYSTEM_PROGRAMS = ("/usr/", "/bin/", "/sbin/", "/lib/", "/opt/", "/snap/", "/nix/store/", "/gnu/store/", "/run/current-system/", "/var/lib/flatpak/")   # a program name run from here may move between them
NIX_LINKS = ("/run/current-system", "/run/booted-system", "/run/opengl-driver", "/run/opengl-driver-32")   # NixOS and Guix
