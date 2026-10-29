#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Jacek Mariański
# fble, the Bluetooth MIDI module for FM-1 firmwares with an fext socket (include/fext.h):
#   sh build.sh          the module: build/fble.img (tools/build_module.py), then the tests
#   sh build.sh test     the tests alone (tests/test_load.c: the loader's check on the image moved to SLOOP's slot, the write frames)
# The toolchain and the SDK: JIELI_TOOLCHAIN and AC79_SDK, or a local.env beside this file (not in git) that sets
# them (fm1-base's tools/get_toolchain.sh fetches the toolchain; the SDK: gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK).
set -e
cd "$(dirname "$0")"
[ -f local.env ] && . ./local.env
export JIELI_TOOLCHAIN AC79_SDK
sh tools/patches.sh apply
[ "$1" = test ] || python3 tools/build_module.py
mkdir -p build/test
cc -O1 -Wall -Wextra -Wno-unused-function -Iinclude -o build/test/test_load tests/test_load.c
build/test/test_load build/check/fble-sloop.bin build/check/fble-sloop.frames
