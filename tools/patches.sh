#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
# fble's changes to fm1-base (the pinned submodule: its ble/ shim and MIDI service), kept as patches/fm1-base/*.patch
# and applied in name order; nothing is ever committed inside fm1-base/.
#   tools/patches.sh apply     each patch not in yet (build.sh does, before every build)
#   tools/patches.sh revert    all out again, the last first (before bumping the submodule)
set -eu
cd "$(dirname "$0")/.."
[ -f fm1-base/tools/build.py ] || git submodule update --init --recursive
case "${1:-apply}" in
apply)
    for p in patches/fm1-base/*.patch; do
        if git -C fm1-base apply --reverse --check "../$p" 2>/dev/null; then
            continue                                    # (in already)
        fi
        git -C fm1-base apply --whitespace=nowarn "../$p" || { echo "patches: $p does not fit fm1-base" >&2; exit 1; }
        echo "patches: applied $p"
    done ;;
revert)
    for p in $(ls -r patches/fm1-base/*.patch); do
        git -C fm1-base apply --reverse --check "../$p" 2>/dev/null && git -C fm1-base apply -R "../$p" &&
            echo "patches: took out $p"
    done ;;
*) echo "tools/patches.sh apply|revert" >&2; exit 2 ;;
esac
