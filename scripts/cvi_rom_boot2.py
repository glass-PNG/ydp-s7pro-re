#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirror of usb_dl.exe `_usb_rom_dl_2x` (0x404313) over a COM port.

Reconstructed from the vendor binary (image base 0x400000):
  send_chunk(ctx, buf, size, _, _, addr_lo, addr_hi, token)
      header = [token][len_be16 = 16+size][addr 5B BE][0 x8]  + payload
      write(header+payload) -> read 16 B ACK, ACK[2:4] must equal crc16_xmodem(request)
  send_file (ctx, path, offset, length, addr_lo, addr_hi, token)
      256-byte packets (16 hdr + 240 payload), addr advances by payload bytes
  flow:
      query 3346:1000
      magic128  token=3 addr=0xFF
      send_file(fip, 0, 0x10000, 0xC0120000, token=0)
      flag "1NGM" token=1 addr=0x0E000004
      break       token=2 addr=0xFF
      query 3346:1000 again (device restarts its ROM download mode)
      magic128  token=3 addr=0xFF
      send_file(fip, 0, 0 (whole), 0x80800000, token=1)
      word "1000" token=0 addr=0x0C021FFC
      break -> device should come up as 3346:1001 (u-boot cvi_utask)

Nothing here writes flash: the only tokens used are 0/1/2/3.
"""
import argparse
import hashlib
import os
import sys
import time

import serial
import serial.tools.list_ports as list_ports

MAGIC = bytes.fromhex(
    "080000140 0c018d5 0006a0d2 401018d5 40000094 40000035 00000014 a03f0010"
    .replace(" ", "")) if False else None

_crc_tab = []
for _i in range(256):
    _c = _i << 8
    for _ in range(8):
        _c = ((_c << 1) ^ 0x1021) & 0xFFFF if _c & 0x8000 else (_c << 1) & 0xFFFF
    _crc_tab.append(_c)


def crc16(data):
    c = 0
    for b in data:
        c = _crc_tab[((c >> 8) ^ b) & 0xFF] ^ ((c << 8) & 0xFF00)
    return c


class Log:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def find_port(pid="1000"):
    for p in list_ports.comports():
        hwid = (p.hwid or "").upper()
        if "3346" in hwid and pid in hwid:
            return p
    return None


def wait_port(log, pid="1000", timeout=30.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        p = find_port(pid)
        if p:
            return p
        time.sleep(0.15)
    return None


class Dev:
    def __init__(self, log, port):
        self.log = log
        self.s = serial.Serial()
        self.s.port = port
        self.s.baudrate = 921600
        self.s.bytesize = 8
        self.s.parity = "N"
        self.s.stopbits = 1
        self.s.timeout = 3.0
        self.s.write_timeout = 3.0
        self.s.dsrdtr = False
        self.s.rtscts = False
        self.s.open()
        try:
            self.s.dtr = False
            self.s.rts = False
        except Exception:
            pass
        self.s.reset_input_buffer()
        self.s.reset_output_buffer()
        self.ok = 0
        self.bad = 0

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    def read_exact(self, n, timeout=3.0):
        t0 = time.time()
        buf = b""
        while len(buf) < n and time.time() - t0 < timeout:
            c = self.s.read(n - len(buf))
            if c:
                buf += c
        return buf

    def xfer(self, token, addr, payload, tag):
        n = 16 + len(payload)
        hdr = bytes([token, (n >> 8) & 0xFF, n & 0xFF,
                     (addr >> 32) & 0xFF, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF,
                     (addr >> 8) & 0xFF, addr & 0xFF]) + b"\x00" * 8
        pkt = hdr + payload
        self.s.write(pkt)
        self.s.flush()
        ret = self.read_exact(16)
        if len(ret) < 16:
            self.log("  %s/%s @0x%x len=%d -> NO ACK (%s)" % (tag, token, addr, len(payload), ret.hex()))
            self.bad += 1
            return None
        exp, got = crc16(pkt), ret[2] << 8 | ret[3]
        good = exp == got
        if good:
            self.ok += 1
        else:
            self.bad += 1
        self.log("  %s/%s @0x%x len=%d ack=%s crc=%04x/%04x tok=%d aux=%s|%s %s" % (
            tag, token, addr, len(payload), ret.hex(), exp, got, ret[6],
            ret[8:12].hex(), ret[12:16].hex(), "OK" if good else "BAD"))
        return ret if good else None

    def send_file(self, blob, addr, token, tag, limit=None, chunk=240):
        off = 0
        total = len(blob) if limit in (None, 0) else min(limit, len(blob))
        t0 = time.time()
        while off < total:
            n = min(chunk, total - off)
            r = self.xfer(token, addr, blob[off:off + n], "%s[%d]" % (tag, off))
            if r is None:
                self.log("  %s stalled at 0x%x after %d/%d B" % (tag, off, off, total))
                return False
            off += n
            addr += n
            if off % (chunk * 32) == 0:
                rate = off / max(1e-6, time.time() - t0) / 1024
                self.log("  %s %d/%d B (%.0f KiB/s)" % (tag, off, total, rate))
        self.log("  %s done %d B in %.1fs" % (tag, off, time.time() - t0))
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fip", default=r".\firmware\extract\fip.bin")
    ap.add_argument("--magic", default=r".\reports\assets\osint\raw\cvitek\usb_dl\cv_dl_magic.bin")
    ap.add_argument("--log", default=r".\logs\rom_boot2.log")
    ap.add_argument("--stage1-only", action="store_true")
    ap.add_argument("--first-len", type=lambda s: int(s, 0), default=0x10000)
    args = ap.parse_args()

    log = Log(args.log)
    fip = open(args.fip, "rb").read()
    magic = open(args.magic, "rb").read()
    log("=== rom_boot2 start: fip %d B sha256=%s magic %d B ===" % (
        len(fip), hashlib.sha256(fip).hexdigest()[:16], len(magic)))

    p = wait_port(log, "1000", 300)
    if not p:
        log("no 3346:1000 port")
        return 2
    log("port %s %s" % (p.device, p.hwid))
    time.sleep(0.3)
    d = Dev(log, p.device)
    try:
        if d.xfer(3, 0xFF, magic, "magic#1") is None:
            log("magic#1 not accepted - abort (device still holding ROM?)")
            return 3
        time.sleep(1.0)
        d.send_file(fip, 0xC0120000, 0, "fip-head", limit=args.first_len)
        d.xfer(1, 0x0E000004, b"1NGM", "flag")
        d.xfer(2, 0xFF, b"", "break#1")
        log("break sent; waiting for ROM to come back as 3346:1000")
        d.close()
        if args.stage1_only:
            return 0
        p2 = wait_port(log, "1000", 30)
        if not p2:
            p1 = find_port("1001")
            if p1:
                log("device came back as 3346:1001 directly: %s" % p1.device)
                return 0
            log("device did not come back as 3346:1000 or 1001")
            return 4
        log("reopened %s" % p2.device)
        d = Dev(log, p2.device)
        if d.xfer(3, 0xFF, magic, "magic#2") is None:
            return 5
        d.send_file(fip, 0x80800000, 1, "fip-ddr", limit=0)
        d.xfer(0, 0x0C021FFC, b"1000", "tag1000")
        d.xfer(2, 0xFF, b"", "break#2")
        d.close()
        log("waiting for u-boot 3346:1001")
        t0 = time.time()
        while time.time() - t0 < 40:
            q = find_port("1001")
            if q:
                log("*** u-boot on %s %s ***" % (q.device, q.hwid))
                s = serial.Serial(q.device, 921600, timeout=3)
                s.dtr = False
                s.rts = False
                b = b""
                t1 = time.time()
                while time.time() - t1 < 5:
                    c = s.read(64)
                    if c:
                        b += c
                log("u-boot banner %d B: %s" % (len(b), b[:64].hex()))
                s.close()
                return 0
            time.sleep(0.2)
        log("no 3346:1001 appeared")
        return 6
    finally:
        try:
            d.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
