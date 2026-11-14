# bash completion for sbx
_sbx() {
    local cur=${COMP_WORDS[COMP_CWORD]} i word
    local opts="--net=none --net-private --private --here --no-wayland --wayland-strict --x11 --no-gpu --gpu-render-only --no-sound --portals --tray --hostname --privacy
        --no-userns --no-theme --camera --alsa --input --kvm --rocm --printing --ssh-agent --allow-sensitive --compat --ignore-config --rebind
        --portal-screenshot --portal-screencast --portal-location --portal-shortcuts --portal-remote-desktop
        --portal-input-capture --portal-wallpaper --portal-launcher --portal-secret
        --whitelist= --read-only= --env= --setenv= --allow-syscall= --memory= --tasks= --cpu= --name="
    # the first word that isn't an option is the command or the program
    for ((i = 1; i < COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        case $word in
            --) COMPREPLY=($(compgen -c -- "$cur")); return ;;
            -*) ;;
            preview|show) [[ $word == show ]] && { COMPREPLY=($(compgen -W "$(ls ~/.config/sbx/apps 2>/dev/null | sed 's/\.conf$//')" -- "$cur")); return; } ;;
            clean) COMPREPLY=($(compgen -W "$(ls ~/.sbx-homes 2>/dev/null)" -- "$cur")); return ;;
            reset) COMPREPLY=($(compgen -W "--yes --default --all $(ls ~/.config/sbx/apps 2>/dev/null | sed 's/\.conf$//')" -- "$cur")); return ;;
            link) COMPREPLY=($(compgen -W "--remove" -- "$cur") $(compgen -c -- "$cur")); return ;;
            stop) COMPREPLY=($(compgen -W "$(ls ~/.sbx-homes 2>/dev/null)" -- "$cur")); return ;;
            doctor|schema|list) return ;;
            desktop) COMPREPLY=($(compgen -W "--install-opener --remove-opener --install-menu --remove-menu --remove" -- "$cur")); return ;;
            *) COMPREPLY=($(compgen -f -- "$cur")); return ;;
        esac
    done
    case $cur in
        --allow-syscall=*) COMPREPLY=($(compgen -W "io_uring userfaultfd process_vm_readv process_vm_writev kcmp pidfd_getfd" -- "${cur#*=}")) ;;
        --whitelist=*|--read-only=*) compopt -o filenames; COMPREPLY=($(compgen -f -- "${cur#*=}")) ;;
        -*) compopt -o nospace; COMPREPLY=($(compgen -W "$opts" -- "$cur")) ;;
        *) COMPREPLY=($(compgen -W "preview show doctor clean reset link desktop open list stop" -- "$cur") $(compgen -c -- "$cur")) ;;
    esac
}
complete -F _sbx sbx
