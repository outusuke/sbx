# bash completion for sbx
_sbx() {
    local cur=${COMP_WORDS[COMP_CWORD]} i word
    local opts="--net=none --net-private --private --here --no-wayland --wayland-strict --x11 --no-gpu --gpu-render-only --no-sound --portals --tray --a11y --gnome-shell --gnome-shell-manage --kde --hostname --privacy
        --no-userns --no-theme --camera --alsa --input --kvm --rocm --printing --ssh-agent --single-instance --allow-sensitive --compat --ignore-config --rebind
        --portal-screenshot --portal-screencast --portal-location --portal-shortcuts --portal-remote-desktop
        --portal-input-capture --portal-wallpaper --portal-launcher --portal-secret --portal-background --portal-usb --portal-clipboard --portal-file-transfer
        --no-portal-notification --no-portal-openuri --no-portal-print --no-portal-trash --no-portal-email --no-portal-inhibit --no-portal-network
        --whitelist= --read-only= --env= --setenv= --allow-syscall= --memory= --tasks= --cpu= --gtk-portal= --name= --app-id= --loader="
    # the first word that isn't an option is the command or the program
    for ((i = 1; i < COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        case $word in
            --) COMPREPLY=($(compgen -c -- "$cur")); return ;;
            -*) ;;
            preview|show) [[ $word == show ]] && { COMPREPLY=($(compgen -W "$(ls ~/.config/sbx/apps 2>/dev/null | sed 's/\.conf$//')" -- "$cur")); return; } ;;
            clean) COMPREPLY=($(compgen -W "--yes --unused $(ls ~/.sbx-homes 2>/dev/null)" -- "$cur")); return ;;
            reset) COMPREPLY=($(compgen -W "--yes --default --all $(ls ~/.config/sbx/apps 2>/dev/null | sed 's/\.conf$//')" -- "$cur")); return ;;
            link) COMPREPLY=($(compgen -W "--remove --desktop" -- "$cur") $(compgen -c -- "$cur")); return ;;
            install) COMPREPLY=($(compgen -W "--launcher --link" -- "$cur") $(compgen -f -- "$cur")); return ;;
            uninstall) COMPREPLY=($(compgen -W "--yes --trash $(ls ~/.config/sbx/installed 2>/dev/null)" -- "$cur")); return ;;
            stop) COMPREPLY=($(compgen -W "$(ls ~/.sbx-homes 2>/dev/null)" -- "$cur")); return ;;
            list) COMPREPLY=($(compgen -W "--all" -- "$cur")); return ;;
            rebind) COMPREPLY=($(compgen -W "--dismiss $(ls ~/.config/sbx/pending 2>/dev/null)" -- "$cur")); return ;;
            export)
                if [[ ${COMP_WORDS[COMP_CWORD-1]} == -o ]]; then compopt -o filenames; COMPREPLY=($(compgen -f -- "$cur")); return; fi
                COMPREPLY=($(compgen -W "--default --all --force -o $(ls ~/.config/sbx/apps 2>/dev/null | sed 's/\.conf$//')" -- "$cur")); return ;;
            import) compopt -o filenames; COMPREPLY=($(compgen -W "--force" -- "$cur") $(compgen -f -- "$cur")); return ;;
            doctor|schema) return ;;
            desktop) COMPREPLY=($(compgen -W "--install-opener --remove-opener --install-installer --remove-installer --install-menu --remove-menu --remove" -- "$cur")); return ;;
            *) COMPREPLY=($(compgen -f -- "$cur")); return ;;
        esac
    done
    case $cur in
        --allow-syscall=*) COMPREPLY=($(compgen -W "io_uring userfaultfd process_vm_readv process_vm_writev kcmp pidfd_getfd" -- "${cur#*=}")) ;;
        --whitelist=*|--read-only=*) compopt -o filenames; COMPREPLY=($(compgen -f -- "${cur#*=}")) ;;
        -*) compopt -o nospace; COMPREPLY=($(compgen -W "$opts" -- "$cur")) ;;
        *) COMPREPLY=($(compgen -W "preview show doctor clean reset link desktop open list stop rebind export import install uninstall" -- "$cur") $(compgen -c -- "$cur")) ;;
    esac
}
complete -F _sbx sbx
