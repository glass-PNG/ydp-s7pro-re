#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a *safe* OTA package: same vendor bytes, but package-file only asks
the device to write two partitions (install + boot).

Commented out:
    bootloader  fip.bin        <- never touch the bootloader
    parameter   partition_emmc.xml
    system      rootfs.img     <- keep the running system untouched
    2nd         yoc.bin

Kept:
    install     install.img    <- the installer itself (same bytes as vendor)
    boot        boot.emmc      <- same bytes as vendor (byte-identical)

So slot A is untouched, slot B receives exactly the bytes it already has, and
the only new information is whether the manifest is authoritative.

Usage: python scripts/build_bootonly.py [out.img]
"""
import hashlib
import os
import re
import sys

ROOT = r"."
SRC = os.path.join(ROOT, "firmware", "update_4.23.0.img")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "firmware", "update_bootonly.img")

PKG_FILE_OFF = 0x100C
PKG_FILE_LEN = 190

DROP = [b"bootloader", b"parameter", b"system", b"2nd"]


def main():
    data = bytearray(open(SRC, "rb").read())
    blk = bytes(data[PKG_FILE_OFF:PKG_FILE_OFF + PKG_FILE_LEN])
    print("--- original ---")
    print(blk.decode("ascii", "replace"))

    lines = blk.split(b"\n")
    out_lines = []
    for ln in lines:
        stripped = ln.lstrip()
        hit = None
        for name in DROP:
            if stripped.startswith(name):
                hit = name
                break
        if hit is not None and not stripped.startswith(b"#"):
            # keep the byte length identical: overwrite the first char with '#'
            out_lines.append(b"#" + ln[1:])
            print("comment: %r" % ln)
        else:
            out_lines.append(ln)

    new_blk = b"\n".join(out_lines)
    if len(new_blk) != PKG_FILE_LEN:
        raise SystemExit("length drift: %d != %d" % (len(new_blk), PKG_FILE_LEN))

    data[PKG_FILE_OFF:PKG_FILE_OFF + PKG_FILE_LEN] = new_blk
    print("--- patched ---")
    print(new_blk.decode("ascii", "replace"))

    d = bytes(data)
    open(OUT, "wb").write(d)
    print("out : %s" % OUT)
    print("size: %d" % len(d))
    print("md5 : %s" % hashlib.md5(d).hexdigest())
    print("sha : %s" % hashlib.sha256(d).hexdigest())


if __name__ == "__main__":
    main()
