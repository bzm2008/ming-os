#!/usr/bin/env bash
# Forced command for the read-only release-vault SSH key.
set -efu

base_dir=/srv/ming-os/release-vault/v1
deny() {
    printf '%s\n' 'release-vault command denied' >&2
    exit 2
}

# The configured root itself must be canonical.  This rejects a symlinked
# vault before any object path is opened.
base_real=`readlink -f -- "$base_dir"` || deny
[ "$base_real" = "$base_dir" ] || deny
[ -d "$base_real" ] || deny

original=${SSH_ORIGINAL_COMMAND-}
[ -n "$original" ] || deny
case "$original" in
    ' '*|*' '|*$'\t'*|*'\n'*|*'\r'*|*'..'*|*'$'*|*';'*|*'&&'*|*'||'*|*'`'*|*'>'*|*'<'*|*'"'*|*"'"*) deny ;;
esac

op=''
arg=''
target=''
extra=''
words=()
IFS=' ' read -r -a words <<< "$original"
op=${words[0]-}
arg=${words[1]-}
target=${words[2]-}
extra=${words[3]-}

case "$op" in
    stat)
        [ "$arg" = '--format=%F:%s' ] || deny
        [ "${#words[@]}" -eq 3 ] || deny
        [ -n "$target" ] || deny
        ;;
    sha256sum|cat)
        [ "${#words[@]}" -eq 2 ] || deny
        target=$arg
        [ -n "$target" ] || deny
        ;;
    *)
        deny
        ;;
esac

case "$target" in
    "$base_dir"/*) ;;
    *) deny ;;
esac
name=${target#"$base_dir"/}
case "$name" in
    ''|*/*) deny ;;
esac
[[ "$name" =~ ^recovery-bundle-[0-9]+\.(age|sha256|json)$ ]] || deny

# stat reports the object type without following a symlink.  Once that lstat
# equivalent succeeds, every read below is bound to the opened descriptor.
target_type=`LC_ALL=C stat --format='%F' -- "$target"` || deny
[ "$target_type" = 'regular file' ] || deny
exec {vault_fd}<"$target" || deny
vault_fd_path=/proc/self/fd/$vault_fd
resolved_target=`readlink -f -- "$vault_fd_path"` || deny
case "$resolved_target" in
    "$base_real"/*) ;;
    *) deny ;;
esac
[ "$resolved_target" = "$target" ] || deny

case "$op" in
    stat)
        LC_ALL=C stat --format='%F:%s' -- "$vault_fd_path"
        ;;
    sha256sum)
        LC_ALL=C sha256sum -- "$vault_fd_path"
        ;;
    cat)
        LC_ALL=C cat -- "$vault_fd_path"
        ;;
esac
