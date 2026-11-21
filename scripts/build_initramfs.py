#!/usr/bin/env python3
"""Build a gzip cpio initramfs that dumps eMMC system partitions to userdisk,
then hands control back to the real rootfs via switch_root (no boot loop).

Dump target paths (both, for MTP visibility):
    /mnt/data/Dump/...
    /mnt/data/Music/Dump/...
"""
import gzip
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARM = os.path.join(ROOT, "tmp", "arm")
OUT_DIR = os.path.join(ROOT, "tmp", "boot")

APPLETS = [
    "sh", "mount", "umount", "dd", "mkdir", "echo", "sync", "reboot", "poweroff",
    "sleep", "df", "cat", "ls", "chmod", "mknod", "date", "cut", "sed", "grep",
    "head", "tail", "wc", "test", "true", "false", "printf", "readlink", "tr",
    "sort", "uniq", "awk", "sha256sum", "stat", "du", "free", "dmesg", "cp",
]

INIT = r"""#!/bin/busybox sh
export PATH=/bin

mount -t proc proc /proc 2>/dev/null
mount -t sysfs sys /sys 2>/dev/null
mount -t devtmpfs dev /dev 2>/dev/null

# ---- locate the userdata/userdisk ext4 partition (for the dump) ----------
UD=""
for p in 15 14 13 12; do
    mkdir -p /mnt/ud
    if mount -t ext4 /dev/mmcblk0p$p /mnt/ud 2>/dev/null; then UD=$p; break; fi
done

if [ -n "$UD" ] && [ ! -f /mnt/ud/Dump/DONE.txt ]; then
    for d in /mnt/ud/Dump /mnt/ud/Music/Dump; do
        mkdir -p $d
        {
            echo "initramfs dump marker $(date)"
            echo "userdisk partition = p$UD"
            echo "--- block devices ---"
            ls /dev/mmcblk0p* /dev/mmcblk0boot* 2>/dev/null
            echo "--- kernel cmdline ---"
            cat /proc/cmdline
            echo "--- free ---"
            df /mnt/ud
        } > $d/INFO.txt 2>&1
    done
    sync

    # raw head: GPT + reserved + partition 1
    dd if=/dev/mmcblk0 of=/mnt/ud/Dump/head8m.bin bs=1M count=8 2>/dev/null

    i=1
    : > /mnt/ud/Dump/manifest.txt
    while [ $i -le 14 ]; do
        if [ -b /dev/mmcblk0p$i ]; then
            echo "p$i $(cat /sys/class/block/mmcblk0p$i/size 2>/dev/null) sectors" >> /mnt/ud/Dump/manifest.txt
            dd if=/dev/mmcblk0p$i of=/mnt/ud/Dump/p$i.bin bs=1M 2>>/mnt/ud/Dump/manifest.txt
            sync
        fi
        i=$((i+1))
    done

    dd if=/dev/mmcblk0boot0 of=/mnt/ud/Dump/mmcblk0boot0.bin bs=1M 2>/dev/null
    dd if=/dev/mmcblk0boot1 of=/mnt/ud/Dump/mmcblk0boot1.bin bs=1M 2>/dev/null
    sync

    echo "DONE $(date)" > /mnt/ud/Dump/DONE.txt
    cp /mnt/ud/Dump/DONE.txt /mnt/ud/Music/Dump/DONE.txt 2>/dev/null
    sync
fi

[ -n "$UD" ] && umount /mnt/ud 2>/dev/null

# ---- boot the real system ------------------------------------------------
ROOTDEV=""
for w in $(cat /proc/cmdline); do
    case "$w" in
        root=*) ROOTDEV=${w#root=} ;;
    esac
done

boot_real() {
    dev=$1
    [ -b "$dev" ] || return 1
    mkdir -p /newroot
    if ! mount -t erofs -o ro $dev /newroot 2>/dev/null; then
        mount -o ro $dev /newroot 2>/dev/null || return 1
    fi
    for i in /sbin/init /init /etc/init.d/rcS /bin/init; do
        if [ -x /newroot$i ]; then
            echo "switch_root -> $dev $i" > /dev/kmsg
            exec switch_root /newroot $i
        fi
    done
    umount /newroot 2>/dev/null
    return 1
}

[ -n "$ROOTDEV" ] && boot_real "$ROOTDEV"
boot_real /dev/mmcblk0p12
boot_real /dev/mmcblk0p13

echo "initramfs: no usable rootfs, idling" > /dev/kmsg
while true; do sleep 3600; done
"""


def cpio_newc_entry(name, mode, data=b"", ino=0, nlink=1):
    hdr = b"070701"
    namesz = len(name.encode()) + 1
    hdr += b"%08X" % ino
    hdr += b"%08X" % mode
    hdr += b"%08X" % 0
    hdr += b"%08X" % 0
    hdr += b"%08X" % nlink
    hdr += b"%08X" % 0
    hdr += b"%08X" % len(data)
    hdr += b"%08X" % 0
    hdr += b"%08X" % 0
    hdr += b"%08X" % 0
    hdr += b"%08X" % 0
    hdr += b"%08X" % namesz
    hdr += b"%08X" % 0
    out = hdr + name.encode() + b"\x00"
    out += b"\x00" * ((4 - (len(out) % 4)) % 4)
    out += data
    out += b"\x00" * ((4 - (len(data) % 4)) % 4)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    bb = open(os.path.join(ARM, "busybox"), "rb").read()
    if bb[:4] != b"\x7fELF" or int.from_bytes(bb[18:20], "little") != 40:
        raise SystemExit("busybox is not ARM32 ELF")

    ino = 1
    buf = bytearray()
    buf += cpio_newc_entry("init", 0o100755, INIT.encode(), ino); ino += 1
    buf += cpio_newc_entry("bin", 0o040755, b"", ino, 2); ino += 1
    buf += cpio_newc_entry("bin/busybox", 0o100755, bb, ino); ino += 1
    for a in APPLETS:
        buf += cpio_newc_entry("bin/" + a, 0o120777, b"busybox", ino); ino += 1
    buf += cpio_newc_entry("TRAILER!!!", 0, b"", ino, 1)

    open(os.path.join(OUT_DIR, "initramfs.cpio"), "wb").write(bytes(buf))
    gz_path = os.path.join(OUT_DIR, "initramfs.cpio.gz")
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(gz_path, "wb"), mtime=0, compresslevel=9) as g:
        g.write(bytes(buf))
    print("cpio bytes:", len(buf))
    print("gz bytes:  ", os.path.getsize(gz_path))


if __name__ == "__main__":
    main()
