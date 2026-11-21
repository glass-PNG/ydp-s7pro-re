"""Upload a blob into the pen's DRAM over the 3346:1000 ROM channel.

ROM-style framing (8-byte header) for token 0x00 (write RAM):
    [token][len_hi][len_lo][addr 5B BE] + payload      (len = 8 + payload)
Max payload per packet = 248 bytes (device buffer is 256 B).
ACK is 16 bytes: [2:4]=crc16-xmodem(whole packet), [6]=token echo.

Read-back verification uses the u-boot style 0x82 command (16-byte header),
which is reliable for 4096-byte reads.

This only writes DRAM. It never touches eMMC.
"""
import argparse
import hashlib
import sys
import time

sys.path.insert(0, r'scripts')
from cvi_readback import find_port, hdr16  # noqa: E402
import serial  # noqa: E402


def crc16(data, crc=0):
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def hdr8(tok, addr, length):
    return bytes([tok, (length >> 8) & 0xFF, length & 0xFF,
                  (addr >> 32) & 0xFF, (addr >> 24) & 0xFF,
                  (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF])


def cap(s, sec, n):
    t = time.time()
    d = bytearray()
    while time.time() - t < sec and len(d) < n:
        k = s.in_waiting
        if k:
            d += s.read(min(k, n - len(d)))
        else:
            time.sleep(0.0002)
    return bytes(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--addr', default='0x80800000')
    ap.add_argument('--chunk', type=int, default=248)
    ap.add_argument('--verify-blocks', type=int, default=4)
    args = ap.parse_args()

    addr = int(args.addr, 16)
    blob = open(args.file, 'rb').read()
    print('blob %d bytes sha256=%s' % (len(blob), hashlib.sha256(blob).hexdigest()[:16]), flush=True)

    p = find_port()
    print('port', p.device, flush=True)
    s = serial.Serial()
    s.port = p.device
    s.baudrate = 921600
    s.timeout = 0
    s.write_timeout = 5
    s.dtr = False
    s.rts = False
    s.open()
    s.set_buffer_size(rx_size=1 << 21, tx_size=1 << 16)
    cap(s, 0.3, 0)

    def rd(a, n):
        s.write(hdr16(0x82, a, n))
        s.flush()
        return cap(s, 1.5, n)[:n]

    bad = 0
    t0 = time.time()
    off = 0
    while off < len(blob):
        part = blob[off:off + args.chunk]
        pkt = hdr8(0x00, addr + off, 8 + len(part)) + part
        s.write(pkt)
        s.flush()
        ack = cap(s, 1.5, 16)
        if len(ack) != 16:
            print('SHORT ACK at off', hex(off), len(ack), flush=True)
            bad += 1
            if bad > 3:
                break
        else:
            want = crc16(pkt)
            got = (ack[2] << 8) | ack[3]
            if want != got:
                print('CRC MISMATCH at off', hex(off), hex(want), hex(got), flush=True)
                bad += 1
                if bad > 3:
                    break
        off += len(part)
        if (off // args.chunk) % 400 == 0:
            print('  %d/%d bytes' % (off, len(blob)), flush=True)

    dt = time.time() - t0
    print('sent %d bytes in %.1fs (%.2f KB/s), errors=%d' % (off, dt, off / 1024.0 / max(dt, 1e-6), bad), flush=True)

    # verify: read 4096-byte windows spread over the blob
    if args.verify_blocks:
        for i in range(args.verify_blocks):
            rel = 0 if i == 0 else (len(blob) - 4096) * i // (args.verify_blocks - 1)
            rel = max(0, rel - (rel % args.chunk))
            n = min(4096, len(blob) - rel)
            got = rd(addr + rel, n)
            ok = got == blob[rel:rel + n]
            print('verify @%s: %s' % (hex(addr + rel), 'OK' if ok else 'MISMATCH'), flush=True)
            if not ok:
                print('  want', blob[rel:rel + 32].hex(), flush=True)
                print('  got ', got[:32].hex(), flush=True)
    s.close()


if __name__ == '__main__':
    main()
