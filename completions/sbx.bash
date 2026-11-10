# bash completion for sbx
_sbx() {
    local cur=${COMP_WORDS[COMP_CWORD]} i word
    local opts="--net=none --private --here --no-wayland --wayland-strict --x11 --no-gpu --no-sound --portals --hostname
        --no-userns --no-theme --camera --alsa --input --kvm --rocm --printing --allow-sensitive --ignore-config
        --whitelist= --read-only= --env= --setenv= --allow-syscall= --memory= --tasks= --cpu= --name="
    # the first word that isn't an option is the command or the program
    for ((i = 1; i < COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        case $word in
            --) COMPREPLY=($(compgen -c -- "$cur")); return ;;
            -*) ;;
            preview|show) [[ $word == show ]] && { COMPREPLY=($(compgen -W "$(ls ~/.config/sbx/apps 2>/dev/null | sed 's/\.conf$//')" -- "$cur")); return; } ;;
            clean) COMPREPLY=($(compgen -W "$(ls ~/.sbx-homes 2>/dev/null)" -- "$cur")); return ;;
            doctor|schema) return ;;
            *) COMPREPLY=($(compgen -f -- "$cur")); return ;;
        esac
    done
    case $cur in
        --allow-syscall=*) COMPREPLY=($(compgen -W "io_uring userfaultfd process_vm_readv process_vm_writev kcmp pidfd_getfd" -- "${cur#*=}")) ;;
        --whitelist=*|--read-only=*) compopt -o filenames; COMPREPLY=($(compgen -f -- "${cur#*=}")) ;;
        -*) compopt -o nospace; COMPREPLY=($(compgen -W "$opts" -- "$cur")) ;;
        *) COMPREPLY=($(compgen -W "preview show doctor clean desktop" -- "$cur") $(compgen -c -- "$cur")) ;;
    esac
}
complete -F _sbx sbx
