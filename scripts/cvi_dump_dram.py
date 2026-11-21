#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump memory through the loader's D2S (token 0x82) primitive.

Usage: cvi_dump_dram.py --addr 0x80000000 --len 0x400000 --chunk 4096 --out file.bin
Read-only: the loader only performs a memory read and sends the bytes back.
"""
import argparse
import sys
import time

import serial
import serial.tools.list_ports as list_ports

_tab = []
for _i in range(256):
    _c = _i << 8
    for _ in range(8):
        _c = ((_c << 1) ^ 0x1021) & 0xFFFF if _c & 0x8000 else (_c << 1) & 0xFFFF
    _tab.append(_c)


def crc16(d, init=0):
    c = init
    for b in d:
        c = _tab[((c >> 8) ^ b) & 0xFF] ^ ((c << 8) & 0xFF00)
    return c


def hdr(token, addr, size64):
    return bytes([token, 0x00, 0x10,
                  (addr >> 32) & 0xFF, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF,
                  (addr >> 8) & 0xFF, addr & 0xFF]) + size64.to_bytes(8, "little")


def find_port():
    for p in list_ports.comports():
        h = (p.hwid or "").upper()
        if "3346" in h and "1000" in h:
            return p
    return None


def open_dev():
    p = find_port()
    if not p:
        return None, None
    s = serial.Serial()
    s.port = p.device
    s.baudrate = 921600
    s.timeout = 0.05
    s.write_timeout = 3
    s.dtr = False
    s.rts = False
    s.open()
    return s, p


def read_block(s, addr, n, tries=3):
    for attempt in range(tries):
        try:
            s.write(hdr(0x82, addr, n))
            s.flush()
        except Exception as e:
            print("write err %r" % (e,))
            return None
        got = bytearray()
        t0 = time.time()
        while len(got) < n and time.time() - t0 < 3.0:
            c = s.read(n - len(got))
            if c:
                got += c
                t0 = time.time()
        if len(got) == n:
            return bytes(got)
        print("  short read at 0x%08x: %d/%d (try %d)" % (addr, len(got), n, attempt + 1))
        time.sleep(0.2)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="0x80000000")
    ap.add_argument("--len", default="0x400000")
    ap.add_argument("--chunk", type=int, default=4096)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    addr = int(a.addr, 0)
    total = int(a.len, 0)

    s, p = open_dev()
    if not s:
        print("no port")
        return 2
    print("port %s ser=%s dump 0x%x..0x%x -> %s" % (p.device, p.serial_number, addr, addr + total, a.out))
    done = 0
    t0 = time.time()
    with open(a.out, "wb") as f:
        while done < total:
            n = min(a.chunk, total - done)
            blk = read_block(s, addr + done, n)
            if blk is None:
                print("FAILED at 0x%08x after %d B" % (addr + done, done))
                return 1
            f.write(blk)
            done += n
            if done % (a.chunk * 16) == 0:
                el = time.time() - t0
                print("  %d/%d  %.1f KB/s" % (done, total, done / 1024.0 / max(el, 1e-6)))
    print("done %d B in %.1fs" % (done, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
