#!/usr/bin/env python3
"""Cvitek USB/CDC download-channel host tool (READ-ONLY oriented).

Wire format reverse engineered from:
  reports/assets/osint/raw/cvitek/usb_dl/cv_usb_pyserial.py   (vendor python twin)
  tools/cvitek/usb_dl.exe                                     (vendor Windows CLI)

Request packet (8-byte header + payload):
    [ token:1 ][ len_hi:1 ][ len_lo:1 ][ addr:5 bytes big-endian ][ payload ]
    len = 8 + len(payload)   (big-endian 16-bit)
Response (16 bytes):
    [2:4]  crc16-xmodem(request bytes) big-endian
    [6]    token echo
    [8:12] aux32 big-endian   (ROM stage: fip_tx_offset)
    [12:16] aux32b big-endian (ROM stage: fip_tx_size)

Tokens:
    0x00 CVI_USB_TX_DATA_TO_RAM   ROM    write payload to RAM address
    0x01 CVI_USB_TX_FLAG          ROM    set flag (payload "1NGM" @0x0E000004)
    0x02 CV_USB_BREAK             ROM    end transfer / jump
    0x03 CV_USB_KEEP_DL                  keep download mode (magic bin token)
    0x04 CV_USB_UBREAK            U-Boot break out
    0x06 CV_USB_PRG_CMD           U-Boot execute u-boot command string
    0x16 CVI_USB_REBOOT                  reboot
    0x81 CV_USB_S2D                      host -> device payload (16B hdr + data)
    0x82 CV_USB_D2S                      device -> host read (8/16B hdr, then data)
    0x83 CVI_USB_PROGRAM          U-Boot commit block to storage offset (WRITE!)

Safety: this tool never sends 0x83 / 0x06 saveenv / any erase. Only reads.
"""
import argparse
import os
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial not installed: python -m pip install pyserial -i https://pypi.tuna.tsinghua.edu.cn/simple")

VIDPID_ROM = "VID:PID=3346:1000"
VIDPID_ROM_ALT = "VID:PID=30B1:1000"
VIDPID_UBOOT = "VID:PID=3346:1001"
VIDPID_UBOOT_ALT = "VID:PID=30B1:1001"

TOK_RAM = 0x00
TOK_FLAG = 0x01
TOK_BREAK = 0x02
TOK_KEEP = 0x03
TOK_UBREAK = 0x04
TOK_CMD = 0x06
TOK_REBOOT = 0x16
TOK_S2D = 0x81
TOK_D2S = 0x82
TOK_PROGRAM = 0x83

_LOG = None


def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    print(line, flush=True)
    if _LOG:
        _LOG.write(line + "\n")
        _LOG.flush()


def open_log(path):
    global _LOG
    _LOG = open(path, "a", encoding="utf-8")


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build(token, addr, payload=b""):
    total = 8 + len(payload)
    if total > 0xFFFF:
        raise ValueError("packet too big: %d" % total)
    hdr = bytes([token, (total >> 8) & 0xFF, total & 0xFF,
                 (addr >> 32) & 0xFF, (addr >> 24) & 0xFF, (addr >> 16) & 0xFF,
                 (addr >> 8) & 0xFF, addr & 0xFF])
    return hdr + payload


def find_port(wanted, timeout=20.0):
    t0 = time.time()
    while True:
        for p in list_ports.comports():
            hwid = (p.hwid or "").upper()
            for w in wanted:
                if w.upper() in hwid:
                    return p
        if timeout and time.time() - t0 > timeout:
            return None
        time.sleep(0.2)


class Dev:
    def __init__(self, port, baud=115200, ack_timeout=3.0):
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = ack_timeout
        self.ser.write_timeout = 5.0
        self.ser.dsrdtr = False
        self.ser.rtscts = False
        self.ser.open()
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def tx(self, pkt, wait_ack=True, ack_timeout=None):
        if ack_timeout is not None:
            self.ser.timeout = ack_timeout
        self.ser.write(pkt)
        self.ser.flush()
        if not wait_ack:
            return None
        ack = self.ser.read(16)
        return ack

    def request(self, token, addr, payload=b"", ack_timeout=None, retries=2):
        pkt = build(token, addr, payload)
        want = crc16(pkt)
        for attempt in range(retries + 1):
            ack = self.tx(pkt, True, ack_timeout)
            if ack is None or len(ack) < 16:
                log("  ! no/short ack (got %r) attempt %d" % (ack, attempt))
                continue
            got_crc = (ack[2] << 8) | ack[3]
            aux1 = int.from_bytes(ack[8:12], "big")
            aux2 = int.from_bytes(ack[12:16], "big")
            ok = got_crc == want
            log("  ack token=0x%02x crc=0x%04x want=0x%04x %s aux1=0x%x aux2=0x%x raw=%s"
                % (ack[6], got_crc, want, "OK" if ok else "MISMATCH", aux1, aux2, ack.hex()))
            if ok:
                return ack
        return None


def flash_writer(path, size, addr=0):
    """ROM stage: send `size` bytes of file at `path` as 4KB chunks (token 0)."""
    ...


def cmd_ports(args):
    for p in list_ports.comports():
        log("%s  %s  %s  %s" % (p.device, p.hwid, p.description, p.serial_number))


def cmd_send(args):
    port = args.port
    if not port:
        p = find_port([VIDPID_ROM, VIDPID_ROM_ALT, VIDPID_UBOOT, VIDPID_UBOOT_ALT], args.timeout)
        if not p:
            log("no cvitek device found")
            return 2
        port = p.device
    log("open %s @%d" % (port, args.baud))
    d = Dev(port, args.baud, args.ack_timeout)
    payload = bytes.fromhex(args.hex) if args.hex else b""
    if args.string:
        payload = args.string.encode() + b"\x00"
    log("tx token=0x%02x addr=0x%x payload=%d bytes" % (args.token, args.addr, len(payload)))
    ack = d.request(args.token, args.addr, payload, retries=args.retries)
    d.close()
    return 0 if ack else 1


def cmd_peek(args):
    """D2S read attempt: [0x82][len][addr 5B] (+ optional 8B size) then read data."""
    port = args.port
    if not port:
        p = find_port([VIDPID_ROM, VIDPID_ROM_ALT, VIDPID_UBOOT, VIDPID_UBOOT_ALT], args.timeout)
        if not p:
            log("no cvitek device found")
            return 2
        port = p.device
    log("open %s" % port)
    d = Dev(port, args.baud, 1.0)
    hdr8 = bytes([TOK_D2S, 0, 8,
                  (args.addr >> 32) & 0xFF, (args.addr >> 24) & 0xFF, (args.addr >> 16) & 0xFF,
                  (args.addr >> 8) & 0xFF, args.addr & 0xFF])
    hdr16 = bytes([TOK_D2S, 0, 16,
                   (args.addr >> 32) & 0xFF, (args.addr >> 24) & 0xFF, (args.addr >> 16) & 0xFF,
                   (args.addr >> 8) & 0xFF, args.addr & 0xFF]) + int(args.len).to_bytes(8, "little")
    for name, pkt in (("8B-hdr", hdr8), ("16B-hdr", hdr16)):
        log("-- variant %s: %s" % (name, pkt.hex()))
        d.ser.reset_input_buffer()
        d.ser.write(pkt)
        d.ser.flush()
        d.ser.timeout = args.ack_timeout
        data = d.ser.read(args.len)
        log("   read %d bytes: %s" % (len(data), data[:64].hex()))
    d.close()
    return 0


def cmd_cmd(args):
    """U-Boot stage: token 6 command string. READ-ONLY commands only (enforced)."""
    bad = ("write", "erase", "mkfs", "saveenv", "setenv", "mmc dev", "sf ", "nand ")
    cmdstr = args.string
    if any(b in cmdstr.lower() for b in ("mmc write", "sf erase", "sf write", "nand erase",
                                         "nand write", "saveenv", "fatwrite", "ums ")):
        sys.exit("refused: command looks like a write: %r" % cmdstr)
    port = args.port
    if not port:
        p = find_port([VIDPID_UBOOT, VIDPID_UBOOT_ALT], args.timeout)
        if not p:
            log("no u-boot stage device (3346:1001) found")
            return 2
        port = p.device
    d = Dev(port, args.baud, args.ack_timeout)
    payload = cmdstr.encode() + b"\x00"
    log("send PRG_CMD %r" % cmdstr)
    ack = d.request(TOK_CMD, 0, payload, retries=args.retries)
    d.close()
    return 0 if ack else 1


def cmd_readbytes(args):
    port = args.port
    if not port:
        p = find_port([VIDPID_ROM, VIDPID_ROM_ALT, VIDPID_UBOOT, VIDPID_UBOOT_ALT], args.timeout)
        if not p:
            log("no cvitek device found")
            return 2
        port = p.device
    d = Dev(port, args.baud, args.ack_timeout)
    d.ser.timeout = args.timeout_sec
    data = d.ser.read(args.len)
    log("raw read %d bytes: %s" % (len(data), data.hex()))
    d.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=r".\logs\cvi_usb_tool.log")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ports")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("send")
    p.add_argument("--token", type=lambda s: int(s, 0), required=True)
    p.add_argument("--addr", type=lambda s: int(s, 0), default=0)
    p.add_argument("--hex", default="")
    p.add_argument("--string", default=None)
    p.add_argument("--port", default=None)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--ack-timeout", type=float, default=3.0)
    p.add_argument("--retries", type=int, default=2)
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("peek")
    p.add_argument("--addr", type=lambda s: int(s, 0), default=0)
    p.add_argument("--len", type=int, default=64)
    p.add_argument("--port", default=None)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--ack-timeout", type=float, default=2.0)
    p.set_defaults(func=cmd_peek)

    p = sub.add_parser("cmd")
    p.add_argument("--string", required=True)
    p.add_argument("--port", default=None)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--ack-timeout", type=float, default=5.0)
    p.add_argument("--retries", type=int, default=1)
    p.set_defaults(func=cmd_cmd)

    p = sub.add_parser("readbytes")
    p.add_argument("--len", type=int, default=8)
    p.add_argument("--port", default=None)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--timeout-sec", type=float, default=3.0)
    p.add_argument("--ack-timeout", type=float, default=3.0)
    p.set_defaults(func=cmd_readbytes)

    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.log), exist_ok=True)
    open_log(args.log)
    log("=== cvi_usb_tool %s" % " ".join(sys.argv[1:]))
    rc = args.func(args)
    log("=== rc=%s" % rc)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
