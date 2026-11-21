#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eMMC read-back through the pen's ROM loader (BL2) - read-only path.

Protocol recovered from firmware/extract/fip.bin:

  token 0x81  S2D write : 16-byte header + payload -> ACK 16 bytes
                           used to place a 64-byte request struct at 0x93200000
                           struct: { u32 reserved @0, u32 size_bytes @4, u64 offset_bytes @8 }
  token 0x83  prepare   : runs  "mmc read <buf> <off_sectors> <cnt_sectors>"
                           result sector count kept in a global
  token 0x0a  READ_BACK : runs the mmc read again into 0x93200000 and streams
                           cnt*512 bytes back over the CDC bulk-in endpoint

Nothing here writes to flash: 0x83 only performs an eMMC read (see
0x8001b75c in fip.bin, which builds "mmc read %p 0x%llx 0x%x").
"""
import argparse
import os
import sys
import time

import serial
import serial.tools.list_ports as list_ports

BUF = 0x93200000

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


def hdr16(token, addr, size64):
    return bytes([token, 0x00, 0x10,
                  (addr >> 32) & 0xFF, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF,
                  (addr >> 8) & 0xFF, addr & 0xFF]) + size64.to_bytes(8, "little")


class Log:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line)
        self.f.write(line + "\n")
        self.f.flush()


def find_port():
    for p in list_ports.comports():
        h = (p.hwid or "").upper()
        if "3346" in h and "1000" in h:
            return p
    return None


def open_dev(log):
    p = find_port()
    if not p:
        return None, None
    s = serial.Serial()
    s.port = p.device
    s.baudrate = 921600
    s.timeout = 0.05
    s.write_timeout = 5
    s.dtr = False
    s.rts = False
    s.open()
    log("opened %s ser=%s" % (p.device, p.serial_number))
    return s, p


def ack(s, log, tag):
    """Read one 16-byte ACK, return (crc, token, b7, raw)."""
    buf = b""
    t0 = time.time()
    while len(buf) < 16 and time.time() - t0 < 2.0:
        c = s.read(16 - len(buf))
        if c:
            buf += c
            t0 = time.time()
    if len(buf) < 16:
        log("%s: no ACK (%d bytes)" % (tag, len(buf)))
        return None
    crc = (buf[2] << 8) | buf[3]
    log("%s: ACK crc=0x%04x tok=0x%02x b7=0x%02x raw=%s" % (tag, crc, buf[6], buf[7], buf.hex()))
    return crc, buf[6], buf[7], buf


def xfer(s, log, token, addr, payload=b"", tag="", expect_ack=True):
    frame = hdr16(token, addr, len(payload)) + payload
    s.write(frame)
    s.flush()
    if expect_ack:
        return ack(s, log, tag or ("tok 0x%02x" % token))
    return True


def readback(s, log, offset, length, timeout=10.0):
    """Ask the loader to read 'length' bytes at absolute flash offset 'offset'."""
    if length % 512:
        length = (length + 511) // 512 * 512
    req = (0).to_bytes(4, "little") + length.to_bytes(4, "little") + offset.to_bytes(8, "little")
    req = req.ljust(64, b"\x00")
    a = xfer(s, log, 0x81, BUF, req, "wr struct")
    if not a:
        return None
    a = xfer(s, log, 0x83, 0, b"", "mmc read setup")
    if not a:
        return None
    s.write(hdr16(0x0A, 0, 0))
    s.flush()
    got = bytearray()
    t0 = time.time()
    while len(got) < length and time.time() - t0 < timeout:
        c = s.read(min(65536, length - len(got)))
        if c:
            got += c
            t0 = time.time()
    log("readback off=0x%x len=%d got=%d" % (offset, length, len(got)))
    if len(got) < length:
        return None
    return bytes(got)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["probe", "dump"])
    ap.add_argument("--off", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--len", type=lambda x: int(x, 0), default=512)
    ap.add_argument("--chunk", type=lambda x: int(x, 0), default=0x100000)
    ap.add_argument("--out", default=None)
    ap.add_argument("--log", default=r".\logs\readback.log")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.log), exist_ok=True)
    log = Log(a.log)

    s, p = open_dev(log)
    if not s:
        log("no 3346:1000 port")
        return 2
    try:
        if a.mode == "probe":
            data = readback(s, log, a.off, a.len)
            if data is None:
                log("probe FAILED")
                return 1
            log("DATA %s" % data[:64].hex())
            if a.out:
                open(a.out, "wb").write(data)
            return 0

        out = a.out or r".\tmp\emmc_head.bin"
        done = 0
        t0 = time.time()
        with open(out, "wb") as f:
            while done < a.len:
                n = min(a.chunk, a.len - done)
                blk = readback(s, log, a.off + done, n, timeout=20.0)
                if blk is None:
                    log("FAIL at 0x%x" % (a.off + done))
                    return 1
                f.write(blk)
                done += n
                el = time.time() - t0
                log("progress %d/%d  %.1f KB/s" % (done, a.len, done / 1024.0 / max(el, 1e-6)))
        log("done -> %s (%d bytes)" % (out, done))
        return 0
    finally:
        try:
            s.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
