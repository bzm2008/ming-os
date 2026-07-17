#!/usr/bin/env bash
# Forced command for the read-only release-vault SSH key.
set -efu

base_dir=/srv/ming-os/release-vault/v1
deny() {
    printf '%s\n' 'release-vault command denied' >&2
    exit 2
}

case "$base_dir" in
    /*) ;;
    *) deny ;;
esac
case "$base_dir" in
    *'..'*|*'//'*) deny ;;
esac
[ -d "$base_dir" ] || deny
[ ! -L "$base_dir" ] || deny
base_current=/
IFS=/ read -r -a base_parts <<< "${base_dir#/}"
for component in "${base_parts[@]}"; do
    [ -n "$component" ] || deny
    base_current="$base_current$component"
    [ ! -L "$base_current" ] || deny
    base_current="$base_current/"
done

original=${SSH_ORIGINAL_COMMAND-}
[ -n "$original" ] || deny
case "$original" in
    *'..'*|*'$'*|*';'*|*'&&'*|*'|'*|*'`'*|*'>'*|*'<'*|*'"'*|*"'"*) deny ;;
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

current=$base_dir
IFS=/ read -r -a name_parts <<< "$name"
for component in "${name_parts[@]}"; do
    current="$current/$component"
    [ ! -L "$current" ] || deny
done
[ -f "$current" ] || deny

case "$op" in
    stat)
        LC_ALL=C stat --format='%F:%s' -- "$current"
        ;;
    sha256sum)
        LC_ALL=C sha256sum -- "$current"
        ;;
    cat)
        LC_ALL=C cat -- "$current"
        ;;
esac
