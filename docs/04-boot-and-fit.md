# Boot 镜像与 initramfs 备份方案

## 1. `boot.emmc` 是一个 FIT（Flattened Image Tree）

| 节点 | 属性 | 值 |
|---|---|---|
| `/images/kernel-1` | data | 4,730,240 B |
| | `arch` / `compression` | `arm` / `none` |
| | `load` / `entry` | `0x80108000` |
| | `hash-2` | crc32 `729edb39` |
| `/images/fdt-cv1813ha_wevb_0007a_emmc` | data | 21,151 B（sha256 `f174bc1c…`） |
| `/configurations/config-…` | kernel / fdt | 只有这两项，**没有 bootargs** |

内层 DTB：`model="Cvitek Cv1826 for Youdao Y08 V0 version"`、`compatible="cvitek,cv181x"`、
`/memory@80000000` reg = `0x80000000, 0x20000000`（**512 MiB**）。

**原厂 FIT 里的 bootargs 是 SDK 默认模板**（`mtdparts=… root=/dev/mtdblock8 rootfstype=squashfs`，
甚至带 `earlycon=sbi riscv.fwsz`），与设备实际不符 ⇒ 真实参数来自 u-boot env，
**改 FIT 里的 bootargs 无效**。

## 2. `boot0` 硬件分区（1 MiB）

```
0x00000  CVBL01\n          fip 头
0x01000  BL2（0x6A00）      AArch64，入口 b #0x1020
0x07A00  CVLD02\n          loader_2nd 头（字段 0xcafe667f/0x7c00/0x2000/…/0x80000000）
0x09C00  （BL2 内容在此之后继续/对齐）
0x9A400  u-boot 明文       未压缩 AArch64，含完整命令行字符串
```

注意：**设备 boot0 里的 fip 与 OTA 包里的 `fip.bin` 不是同一构建**
（cksum `0xcafeb37a` vs `0xcafee953`），别混用。

## 3. u-boot 里确实有「读回 flash」的实现

从 boot0 的明文 u-boot 里能直接读到这些字符串（文件偏移）：

```
NOTICE:  CVI_USB_READ_BACK            @0x73c63
mmc read %p 0x%llx 0x%x               @0x73c4b
NOTICE:  CVI_USB_READ_BACK_FIP        @0x73bef
mmc read %p 0 0x800                   @0x73bcf
NOTICE:  CVI_USB_READ_VENDOR          @0x73ce8
NOTICE:  CVI_USB_PRG_CMD              @0x73d48
NOTICE:  CVI_USB_JUMP to %llx         @0x73b51
NOTICE:  CVI_USB_JUMP back            @0x73b88
NOTICE:  Overwrite fip_src to FIP_SRC_USB @0x740db
mmc dev 0 1 / mmc dev 0 0 / mmc write %p 0x%llx 0x%x
```

也就是说：**只要能进 `3346:1001`（u-boot 的 `cvi_utask`），就有现成的读回命令**。
问题是 ROM 阶段的 jump/BREAK 语义没有对上（token `0x03` 一发就重启），
这是目前最想解决的一个点。

## 4. initramfs 备份方案（已造好，未部署）

思路：不换内核、不换 dtb，只往 FIT 里**加一个 ramdisk**；内核起来后先在内存里
把整盘 dd 到 `userdisk`，再 `switch_root` 回真正的系统。

### 4.1 为什么可行

- 内核里有 `Unpacking initramfs` 字符串 ⇒ 支持 initramfs
- 内核内置 `erofs` / `EXT4-fs` / `mmcblk`
- RAM 512 MiB，ramdisk 放 `0x84000000` 安全
- `userdisk`（p15）是 ext4，挂上就能写；同时写一份到 `Music/` 下以便 MTP 可见

### 4.2 产物

| 文件 | 大小 | 说明 |
|---|---|---|
| `busybox` | 1,381,616 B | Debian `busybox-static 1:1.37.0-6+b9` **armhf**（ELF32 machine 40） |
| `initramfs.cpio.gz` | 893,059 B | cpio 1,388,356 B，`/init` + busybox + 39 个 applet 软链 |
| `boot_dump.emmc` | 5,645,676 B | 原 kernel + 原 dtb 原字节 + `ramdisk-1`（load `0x84000000`） |
| `update_dumpboot.img` | 19,510,828 B | 重建的 YDIH 容器（7 个载荷，round-trip 校验通过） |

### 4.3 init 逻辑

1. 挂 `devtmpfs` / `proc` / `sys`
2. 依次尝试 `/dev/mmcblk0p15 / p14 / p13 / p12` → ext4 挂到 `/mnt/ud`
3. 若没有 `Dump/DONE.txt`：
   - `head8m.bin`（mmcblk0 前 8 MiB：GPT + 保留区 + p1）
   - `p1.bin` … `p14.bin`
   - `mmcblk0boot0.bin` / `mmcblk0boot1.bin`
   - `INFO.txt`（cmdline、分区表、`/proc/partitions`）
   - `manifest.txt`（文件名 + 大小 + `md5sum`）
   - 同时写一份到 `/mnt/ud/Music/Dump/`（MTP 可见）
4. 写 `DONE.txt`
5. 解析 `/proc/cmdline` 里的 `root=`，挂 EROFS 到 `/newroot`，
   `exec switch_root /newroot /sbin/init`（失败再试 `/init`、`/etc/init.d/rcS`）
   —— **必须 switch_root**，否则 initramfs 会死循环
6. 全都失败才 `sleep` 挂起

FIT 写入器可信度：用同一个 builder 重建「不含 ramdisk」的 FIT，
与厂商原文件对比 `off_struct`、`off_strings` 完全一致，只有 `totalsize` 差 0x3A4
（厂商字符串表里多出约 932 B 未使用字符串）。

## 5. 风险

**这是单向操作。** 已经确认：

- ROM 通道 `token 0x03` 一发就重启，不是可用的 flash 写通道
- ROM 的 `0x83` 组出来的 `mmc read` 在读用户区时失败
- 没有 UART（笔是一体成型，拆机要动屏幕）

所以如果 bootloader 不接受这个 FIT，笔会卡在开机，**目前没有救回手段**。
这也是方案已经造好、却仍然没有部署的原因。

如果你想在自己的设备上试，建议的顺序是：
1. 先用 `package-file` 只写 `boot_b`（不动 A 槽），看系统是否切槽并正常启动
2. 再考虑把带 ramdisk 的 FIT 写进**非当前槽**
3. 每一步都保留可回退的官方 `boot.emmc`
