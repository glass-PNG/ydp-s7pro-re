# 有道词典笔 S7 Pro 逆向笔记（YDP S7 Pro / CV1826）

> 目标：**在不拆机、不用 UART 的前提下，为这台笔做一份完整 flash 备份**，然后再谈定制。
> 现状：**读回通道仍未打通**，写通道已经被完整验证。欢迎各路朋友一起想办法。

本仓库记录的是对**我自己购买的设备**做的研究：协议帧格式、分区表、OTA 容器、
boot 镜像结构、以及每一步的真机实测结果（含失败）。所有结论都带地址/偏移/日志证据。

---

## 1. 设备概要

| 项 | 值 |
|---|---|
| 型号（笔内） | `YDPS7-2`（SKU `OVERHEAD_Y08_SKU_CHN_PLUS`） |
| SoC | Cvitek **CV1826**（ARM64 核跑 BL2/U-Boot，Linux 侧为 **32 位 ARM**） |
| 系统 | Linux `5.10.4-tag--g50ee58fab3b9`，OS 版本 4.23.0 |
| 存储 | eMMC **29.12 GiB**（`mmcblk0`，另有 `mmcblk0boot0/boot1`） |
| 根文件系统 | **EROFS 只读**（`root=/dev/mmcblk0p12`，A/B 双槽） |
| 正常态 USB | `18d1:0001` → `18d1:0011`（MTP + ADB，伪装 Nexus） |
| 下载模式 USB | 按住「回城键」插 USB → **`3346:1000`**（CDC-ACM，屏幕彩色方格） |
| U-Boot 下载任务 | `3346:1001`（`cvi_utask`，本机尚未成功进入） |
| UART | `console=ttyS0,115200`（需拆机，本机未使用） |

## 2. 已确认的结论（附证据位置）

### 2.1 分区表（来自设备 boot0 里的 u-boot cmdline，非 SDK 模板）

```
blkdevparts=mmcblk0:2048K@0x800000(2nd),64K(PARAM),4096K(pic),128K(ENV),4096K(misc),
4096K(uboot),4096K(trust_a),4096K(trust_b),16384K(boot_a),16384K(boot_b),
65536K(recovery),1572864K(rootfs_a),1572864K(rootfs_b),262144K(userdata),-(userdisk);
mmcblk0boot0:1M(fip),1M(fip_bak);
```

即 `p1=2nd … p12=rootfs_a, p13=rootfs_b, p14=userdata, p15=userdisk`，p1..p14 合计约 3.37 GiB。

### 2.2 下载模式（`3346:1000`）的帧格式是**混用**的

同一个固件对不同的 token 使用**两种不同的帧头**，这是本仓库最有用的一条结论：

| 用途 | 帧头长度 | 布局 |
|---|---|---|
| 数据面（token `0x00` 写内存） | **8 字节** | `[token][len_hi][len_lo][addr 5B 大端]`，`len = 8 + payload` |
| 控制面（`0x81/0x82/0x83/0x0a/0x0c`） | **16 字节** | `[token][0x00][0x10][addr 5B 大端][size 8B 小端]` |

CRC 都是 **CRC-16/XMODEM**（init 0，无 final xor），覆盖**整包**（头 + 载荷）；
ACK 恒为 16 字节：`[2:4]` = CRC 大端、`[6]` = token 回显、`[7]` = 自增命令计数器。
**18 字节 / 512B / 1KB 的变体全部无效**，8 字节头配大载荷也会失败（上限 256 B）。

### 2.3 token 实测矩阵

| token | 行为 | 结论 |
|---|---|---|
| `0x00` | 写内存 `memcpy(addr, payload, len-8)` | ✅ 可用，≤256 B/包，回读一致 |
| `0x01` | 纯 ACK（置标志） | ✅ ACK |
| `0x02` | 未实现，落未知分支 | ⚠️ |
| `0x03` | 跳转后**设备立刻掉 USB、随后重启** | ❌ 不可用作写盘/引导 |
| `0x06` | ACK 但不执行 `mmc dev` / `mmc read` / `mw.b` | ⚠️ 不是 shell |
| `0x0a` | 读回命令：失败时**仍把旧缓冲发出来** | ⚠️ |
| `0x0c` | 固定读 `boot0` 1 MiB | ✅ **7.36 MB/s**，稳定 |
| `0x81` | 写内存 / DMA 路径 | ⚠️ 高地址路径非普通 memcpy |
| `0x82` | 读内存 | ✅ 16/64/128/256/4096 可读；512/1024/2048 无响应 |
| `0x83` | 组 `mmc read` 命令 | ⚠️ ACK 正常但读主分区失败（全零） |

### 2.4 OTA 通道完全可控（写方向）

- 容器 `YDIH`、条目 `YDII`、7 个载荷；`package-file` 是**活的刷写清单**：
  把某一行的首字符改成 `#` 就能让设备**不写那个分区**。
- 设备接受伪造的 `checkVersion` 响应（`sign` 不校验），按 Range 分 4 段下载整包。
- 实测：只含 `install` + `boot` 的包 → 下载 18,620,428 B、安装、重启、**系统正常、版本不变**。
- 复现环境：本地假 DNS + 本地 HTTP :80 + 把路由器 DHCP 的 DNS 指向本机。

### 2.5 `boot.emmc` 是明文 FIT

- `/images/kernel-1`：4,730,240 B，`arch=arm`，`compression=none`，`load=entry=0x80108000`，crc32 `729edb39`
- `/images/fdt-…`：21,151 B
- `/configurations/…`：只有 `kernel` + `fdt`，**没有 bootargs**
- 内核里有 `Unpacking initramfs`，**支持 initramfs**；DTB 的 `/memory` 为 512 MiB
- 内核里**没有** `dwc2` / `g_mass_storage` / `configfs` ⇒ 做不了 USB 大容量设备

## 3. 卡在哪（希望一起解决的点）

1. **读回整盘**：ROM 只有 `0x0c` 这个「固定读 boot0」的硬编码路径；
   `0x83` 组出来的 `mmc read` 在 ROM 上下文里返回失败。需要找到能让它读用户区的方法。
2. **进 `3346:1001`（u-boot `cvi_utask`）**：fip 里的 u-boot 明明有
   `NOTICE: CVI_USB_READ_BACK` / `mmc read %p 0x%llx 0x%x` 这套读回实现，
   但 ROM 的 jump/BREAK 语义与厂商 CLI 的假设对不上（`0x03` 直接重启）。
3. **救砖通道**：没有 flash 写通道，任何 boot 改动都是单向的。
   这也是本仓库迟迟没有部署「自定义 boot + initramfs」方案的原因。

## 4. 目录

```
docs/01-device.md            设备与分区
docs/02-usb-rom-protocol.md  3346:1000 帧格式、token 表、实测矩阵
docs/03-ota-channel.md       YDIH 容器、package-file、假 DNS + 本地 OTA 复现
docs/04-boot-and-fit.md      FIT 解剖、initramfs 备份方案与风险
docs/05-roadmap.md           待解问题 + 建议的验证顺序
docs/06-ethics.md            边界与免责声明
scripts/                     脱敏后的工具脚本
evidence/checksums.txt       已取得的样本哈希
```

## 5. 免责声明

本项目仅用于**自己设备**的备份、修复与学习。不要用它去改别人的设备、
不要用泄露的凭据去请求厂商服务、不要把工具用在你不拥有的硬件上。
文中的凭据类字段（deviceSecret / productSecret / 序列号等）在整理本仓库时已全部剔除。
