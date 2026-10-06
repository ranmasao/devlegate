#!/bin/sh
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
set -eu

package=${1:?usage: validate_arch_host.sh PACKAGE}
root=$(mktemp -d "${TMPDIR:-/tmp}/devlegate-pacman.XXXXXX")
state=$(mktemp "${TMPDIR:-/tmp}/devlegate-user-state.XXXXXX")
trap 'rm -rf "$root" "$state"' EXIT
mkdir -p "$root/var/lib/pacman" "$root/var/cache/pacman/pkg"
printf '%s\n' 'unrelated user/project state' >"$state"

pacman_args="--root $root --dbpath $root/var/lib/pacman --cachedir $root/var/cache/pacman/pkg"
echo '$ pacman --root TESTROOT --dbpath TESTROOT/var/lib/pacman -Qip PACKAGE'
pacman $pacman_args -Qip "$package"
echo '$ pacman --root TESTROOT --dbpath TESTROOT/var/lib/pacman -U PACKAGE'
pacman $pacman_args --noconfirm -U "$package"
echo '$ pacman --root TESTROOT --dbpath TESTROOT/var/lib/pacman -Qqo /usr/bin/devlegate'
owner=$(pacman $pacman_args -Qqo /usr/bin/devlegate)
test "$owner" = devlegate
echo "owner: $owner"
echo '$ devlegate --version'
PATH="$root/usr/bin:$PATH" "$root/usr/bin/devlegate" --version
echo '$ devlegate --help'
PATH="$root/usr/bin:$PATH" "$root/usr/bin/devlegate" --help >/dev/null
echo '$ pacman --root TESTROOT --dbpath TESTROOT/var/lib/pacman -Rns devlegate'
pacman $pacman_args --noconfirm -Rns devlegate
test ! -e "$root/usr/bin/devlegate"
test -f "$state"
echo 'package-owned files removed; unrelated state preserved'
