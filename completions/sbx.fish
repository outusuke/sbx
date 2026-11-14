# fish completion for sbx. SPDX-License-Identifier: GPL-3.0-or-later
# Install: cp completions/sbx.fish ~/.config/fish/completions/
function __sbx_apps
    ls ~/.config/sbx/apps 2>/dev/null | string replace -r '\.conf$' ''
end
function __sbx_homes
    ls ~/.sbx-homes 2>/dev/null
end
set -l cmds preview show doctor clean reset link desktop open schema list stop
set -l c "not __fish_seen_subcommand_from $cmds"
complete -c sbx -f -n $c -a "$cmds"
complete -c sbx -n $c -F
# commands that take a name
complete -c sbx -f -n "__fish_seen_subcommand_from show" -a "(__sbx_apps)"
complete -c sbx -f -n "__fish_seen_subcommand_from clean" -a "(__sbx_homes)"
complete -c sbx -f -n "__fish_seen_subcommand_from reset" -a "(__sbx_apps)"
complete -c sbx -f -n "__fish_seen_subcommand_from reset" -l default -d "Reset the default settings"
complete -c sbx -f -n "__fish_seen_subcommand_from reset" -l all -d "Reset every settings file"
complete -c sbx -f -n "__fish_seen_subcommand_from reset" -l yes -d "Don't ask"
complete -c sbx -f -n "__fish_seen_subcommand_from link" -l remove -d "Remove a link made by sbx"
complete -c sbx -f -n "__fish_seen_subcommand_from link" -a "(__fish_complete_command)"
complete -c sbx -f -n "__fish_seen_subcommand_from stop" -a "(__sbx_homes)"
complete -c sbx -f -n "__fish_seen_subcommand_from desktop" -l install-opener -d "Add \"Run in sbx\" to Open With"
complete -c sbx -f -n "__fish_seen_subcommand_from desktop" -l install-menu -d "Add sbx-gui to the app menu"
complete -c sbx -f -n "__fish_seen_subcommand_from desktop" -l remove-menu -d "Remove it again"
complete -c sbx -f -n "__fish_seen_subcommand_from desktop" -l remove-opener -d "Remove it again"
complete -c sbx -f -n "__fish_seen_subcommand_from desktop" -l remove -d "Remove a launcher made by sbx"
# options (before the program)
complete -c sbx -n $c -l net -x -a none -d "No network (--net=none)"
complete -c sbx -n $c -l net-private -d "Internet, but not this computer's own services (needs pasta)"
complete -c sbx -n $c -l private -d "Private home erased on exit"
complete -c sbx -n $c -l here -d "Share the current folder"
complete -c sbx -n $c -l no-wayland -d "Hide the Wayland socket"
complete -c sbx -n $c -l wayland-strict -d "No Wayland at all if it can't be restricted"
complete -c sbx -n $c -l x11 -d "Share the X11 socket (weaker isolation)"
complete -c sbx -n $c -l no-gpu -d "Hide /dev/dri and NVIDIA devices"
complete -c sbx -n $c -l gpu-render-only -d "Share only the GPU render nodes"
complete -c sbx -n $c -l no-sound -d "Hide PipeWire and PulseAudio"
complete -c sbx -n $c -l portals -d "Notifications and basic portals"
complete -c sbx -n $c -l tray -d "Tray icon and media keys"
complete -c sbx -n $c -l hostname -d "Hostname 'sbx' inside"
complete -c sbx -n $c -l privacy -d "Minimal /etc/passwd: no real name, no other accounts"
complete -c sbx -n $c -l no-userns -d "No nested user namespaces"
complete -c sbx -n $c -l no-theme -d "Don't show your fonts, icons and GTK settings"
for d in camera alsa input kvm rocm printing ssh-agent
    complete -c sbx -n $c -l $d -d "Share that device or socket"
end
complete -c sbx -n $c -l allow-sensitive -d "Allow sharing ~/.ssh, ~/.gnupg and similar"
complete -c sbx -n $c -l compat -d "Pass graphics and toolkit variables"
complete -c sbx -n $c -l ignore-config -d "Ignore the settings files for this run"
complete -c sbx -n $c -l rebind -d "This file is the same program, just moved"
for p in screenshot screencast location shortcuts remote-desktop input-capture wallpaper launcher secret
    complete -c sbx -n $c -l portal-$p -d "One more desktop portal (needs --portals)"
end
complete -c sbx -n $c -l whitelist -r -F -d "Share a file or folder (read-write)"
complete -c sbx -n $c -l read-only -r -F -d "Share a file or folder (read-only)"
complete -c sbx -n $c -l env -x -d "Pass a variable through"
complete -c sbx -n $c -l setenv -x -d "Set a variable (NAME=VALUE)"
complete -c sbx -n $c -l allow-syscall -x -a "io_uring userfaultfd process_vm_readv process_vm_writev kcmp pidfd_getfd" -d "Turn a blocked system call back on"
complete -c sbx -n $c -l memory -x -d "Memory limit, e.g. 2G"
complete -c sbx -n $c -l tasks -x -d "Task limit"
complete -c sbx -n $c -l cpu -x -d "CPU limit, e.g. 200%"
complete -c sbx -n $c -l name -x -d "Use another home and settings name"
