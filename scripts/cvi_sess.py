#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive session against the pen's 3346:1000 loader (16-byte header proto).

Header layout confirmed on hardware:
    [token][0x00][0x10][addr 5B big endian][size64 8B little endian]
The device answers a 16 byte ACK whose [2:4] is CRC-16/XMODEM over the 16 header
bytes and whose [6] echoes the token.

Sub-commands:
    info    - send nothing, list ports
    read    - D2S read: --addr --len [--token 0x82]
    write   - S2D write: --addr --payload hex (default 4 bytes of 0x00)
    magic   - send the 128 byte cv_dl_magic payload to addr 0xff with token 0x81
"""
import argparse
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


def ports():
    return [p for p in list_ports.comports()
            if "3346" in (p.hwid or "").upper() and "1000" in (p.hwid or "").upper()]


def hdr(token, addr, size64):
    return bytes([token, 0x00, 0x10,
                  (addr >> 32) & 0xFF, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF,
                  (addr >> 8) & 0xFF, addr & 0xFF]) + size64.to_bytes(8, "little")


def open_first(baud=921600):
    ps = ports()
    if not ps:
        print("no 3346:1000 port present")
        return None, None
    p = ps[0]
    for _ in range(5):
        try:
            s = serial.Serial()
            s.port = p.device
            s.baudrate = baud
            s.timeout = 0.3
            s.write_timeout = 2
            s.dtr = False
            s.rts = False
            s.open()
            print("opened %s ser=%s" % (p.device, p.serial_number))
            return s, p.serial_number
        except Exception as e:
            print("open retry: %r" % (e,))
            time.sleep(0.3)
    return None, None


def drain(s, total, wait=0.6):
    got = b""
    t0 = time.time()
    while time.time() - t0 < wait and len(got) < total:
        try:
            c = s.read(total - len(got))
        except Exception as e:
            print("read error %r after %d B" % (e, len(got)))
            break
        if c:
            got += c
            t0 = time.time()
    return got


def report(tag, got):
    print("[%s] %d B: %s" % (tag, len(got), got.hex()))
    if len(got) >= 16:
        print("[%s]   ack crc=%04x tok=%02x tail=%s"
              % (tag, int.from_bytes(got[2:4], "big"), got[6], got[8:16].hex()))
    if len(got) > 16:
        print("[%s]   payload(%d): %s" % (tag, len(got) - 16, got[16:].hex()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["info", "read", "write", "magic"])
    ap.add_argument("--addr", default="0x0")
    ap.add_argument("--len", type=int, default=16)
    ap.add_argument("--token", default="0x82")
    ap.add_argument("--payload", default=None)
    ap.add_argument("--no-payload", action="store_true")
    a = ap.parse_args()

    if a.cmd == "info":
        for p in ports():
            print(p.device, p.hwid, p.serial_number)
        return 0

    addr = int(a.addr, 0)
    token = int(a.token, 0)
    s, sn = open_first()
    if not s:
        return 2
    try:
        if a.cmd == "magic":
            magic = open(r".\reports\assets\osint\raw\cvitek\usb_dl\cv_dl_magic.bin", "rb").read()
            h = hdr(0x81, 0xFF, len(magic))
            pkt = h + magic
            print("send magic hdr_crc=%04x len=%d" % (crc16(h), len(pkt)))
            s.write(pkt)
            s.flush()
            report("magic", drain(s, 16))
        elif a.cmd == "read":
            h = hdr(token, addr, a.len)
            print("send read tok=%02x addr=0x%x len=%d hdr_crc=%04x"
                  % (token, addr, a.len, crc16(h)))
            s.write(h)
            s.flush()
            report("read", drain(s, a.len + 16, wait=1.0))
        else:
            if a.no_payload:
                pl = b""
            elif a.payload:
                pl = bytes.fromhex(a.payload.replace(" ", ""))
            else:
                pl = b"\x00\x00\x00\x00"
            h = hdr(token, addr, len(pl))
            pkt = h + pl
            print("send write tok=%02x addr=0x%x len=%d hdr_crc=%04x"
                  % (token, addr, len(pl), crc16(h)))
            s.write(pkt)
            s.flush()
            report("write", drain(s, 16))
    finally:
        try:
            s.close()
        except Exception:
            pass
    time.sleep(0.3)
    print("after:", [(p.device, p.serial_number) for p in ports()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
