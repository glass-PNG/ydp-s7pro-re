# 设备与分区

## 1. 硬件/系统

| 项 | 值 | 来源 |
|---|---|---|
| 笔内型号 | `YDPS7-2` | 系统属性 / OTA register 报文 |
| SKU | `OVERHEAD_Y08_SKU_CHN_PLUS` | OTA register 报文 |
| SoC | Cvitek CV1826 | `/proc/cpuinfo`、DTB `compatible="cvitek,cv181x"` |
| BL2/U-Boot 架构 | AArch64 | fip 里 BL2 入口 `08 00 00 14`（`b #0x1020`） |
| Linux 架构 | **armv7l（32 位）** | 内核 Image `arch=arm`，`load=0x80108000` |
| 内核 | `5.10.4-tag--g50ee58fab3b9` (root@jenkins-2) | 内核 Image 内字符串 |
| 根文件系统 | EROFS，只读 | 交接记录 + `rootfs.img` 分析 |
| eMMC | 29.12 GiB | `partition_emmc.xml` 的 `size_in_kb=30539776` |
| UART | `console=ttyS0,115200` | cmdline（需拆机，本机未用） |
| 屏幕 | 下载模式显示彩色方格 | 实机 |

## 2. 分区表

设备自己的 u-boot cmdline（从 boot0 偏移 `0x318BB` 起的明文里取到）：

```
blkdevparts=mmcblk0:2048K@0x800000(2nd),64K(PARAM),4096K(pic),128K(ENV),4096K(misc),
4096K(uboot),4096K(trust_a),4096K(trust_b),16384K(boot_a),16384K(boot_b),
65536K(recovery),1572864K(rootfs_a),1572864K(rootfs_b),262144K(userdata),-(userdisk);
mmcblk0boot0:1M(fip),1M(fip_bak);
```

| 分区 | 大小 | 说明 |
|---|---|---|
| `p1 2nd` | 2 MiB @0x800000 | `yoc.bin`（C-SKY 二级核媒体固件，`cimgxV4` 头） |
| `p2 PARAM` | 64 KiB | |
| `p3 pic` | 4 MiB | 资源/图片 |
| `p4 ENV` | 128 KiB | u-boot 环境变量（**真正的 bootargs 在这里**） |
| `p5 misc` | 4 MiB | A/B 槽与升级标志 |
| `p6 uboot` | 4 MiB | 与 `boot0` 里的 fip 同源 |
| `p7/p8 trust_a/b` | 4 MiB ×2 | 可信固件 |
| `p9/p10 boot_a/b` | 16 MiB ×2 | Linux 内核 FIT（**明文**） |
| `p11 recovery` | 64 MiB | |
| `p12/p13 rootfs_a/b` | 1.5 GiB ×2 | EROFS 只读根 |
| `p14 userdata` | 256 MiB | 挂 `/mnt/cfg` |
| `p15 userdisk` | 余量（约 24.5 GiB） | 挂 `/mnt/data`，用户文件 |
| `mmcblk0boot0/1` | 1 MiB ×2 | fip / fip_bak |

> 一个容易误判的点：`boot.emmc` 里的内层 DTB 带着一套 **SDK 默认的 SPI-NOR/RISC-V
> bootargs**（`mtdparts=… root=/dev/mtdblock8 rootfstype=squashfs …`），
> 与设备实际用的 eMMC/EROFS 参数完全不符。**真实参数由 u-boot env 覆盖**，
> 改 FIT 里的 bootargs 没有任何作用。

## 3. USB 形态

| 模式 | VID:PID | 说明 |
|---|---|---|
| 正常（内核） | `18d1:0001` → `18d1:0011` | 伪装 Nexus，MTP + ADB 复合设备 |
| 下载模式（ROM） | `3346:1000` | iProduct `USB Com Port`，两个 CDC-ACM 接口 |
| u-boot 下载任务 | `3346:1001` | `cvi_utask`，**本次未成功进入** |

进入下载模式：**关机 → 按住「回城键」→ 插 USB → 全程按住不放**。
松手立即退出下载模式并正常引导。ROM 有自己的空闲超时。

ROM 阶段 USB 描述符（实测）：
- 设备 `bcdUSB=0x0201`、`speed=high` ⇒ **只有 USB 2.0 HS（480 Mbps）**
- intf0：CDC 通信口 `02/02/01`，interrupt IN `0x82` maxpkt 10
- intf1：CDC 数据口 `0a`，bulk OUT `0x01` / bulk IN `0x81` maxpkt **512**

实测吞吐（`0x0c` 读 1 MiB）：**7.36 MB/s**（约 59 Mbps），29.12 GiB 约需 71 分钟。
瓶颈在设备侧 CDC 栈，换 USB3 口/线无效。

## 4. ADB 现状

ADB 存在但被登录墙拦截：

```
$ adb shell
login with "adb shell auth" to continue.
```

- 密码脚本 `adb_auth.sh`，**3 次上限**
- 9 种 raw service 全部拦截
- `adb pull` 也会失败：`protocol fault: stat response has wrong message id: 1768386412`
  （1768386412 = `"logi"` 小端，即那句登录 banner 被当成 sync 响应）
- 因此 **ADB 目前不可作为备份通道**

## 5. 网络侧

- 笔通过 Wi-Fi 接入，OTA 用 `iotapi.abupdate.com` / `iotsapi.abupdate.com`
- `checkVersion` / `register` 走 HTTP，`IMG` 走 Range 分片下载
- 详见 `03-ota-channel.md`

## 6. 手上有的官方物料

| 文件 | 大小 | 说明 |
|---|---|---|
| `update_4.23.0.img` | 18,620,428 B | OTA 全量包（YDIH 容器） |
| `boot.emmc` | 4,753,272 B | 内核 FIT（明文，含 kernel + dtb） |
| `fip.bin` | 631,808 B | BL2 + loader_2nd（**未签名**，`FIP_FLAGS=0`） |
| `install.img` | 6,291,456 B | ext4 镜像，含 `update_engine`（ELF32 ARM）与 8 个 shell 脚本 |
| `rootfs.img` | 5,595,238 B | **不是裸文件系统**：`HDIFF19&&crc32` 私有容器（熵 7.71） |
| `yoc.bin` | 1,336,864 B | C-SKY 媒体固件，`cimgxV4` 头 |
| `partition_emmc.xml` | 1,489 B | 分区/升级描述 |
| `package-file` | 190 B | **活的刷写清单**（见 OTA 文档） |

> 注意：OTA 包里**没有** uboot 分区内容、没有 rootfs_a/b 的完整镜像（rootfs 是差分容器）、
> 也没有 userdata/userdisk。所以「拿 OTA 包当完整备份」是不成立的。
