# scripts

全部为**只读/辅助**工具，针对 `VID_3346&PID_1000` 的下载模式与本机 OTA 实验环境。
默认参数里的 IP、序列号、凭据已脱敏成占位符，请按自己的环境改。

## 协议 / 设备侧

| 脚本 | 用途 |
|---|---|
| `cvi_usb_tool.py` | 最底层的帧构造与收发（`ports` / `send` / `peek` / `cmd`），可作为库引用 |
| `cvi_rom_boot2.py` | 按厂商 cv182x 流程做 ROM 自举（magic → fip → flag → BREAK），**含 token 0x03，会掉线** |
| `cvi_readback.py` | `0x83` + `0x0a` 读回封装；失败时会拿到旧缓冲（全零），注意分辨 |
| `cvi_dump_dram.py` | 用 `0x82` 分块 dump 内存（4096 对齐），**别读未映射地址** |
| `upload_ram.py` | 用 `0x00` 分 248 B/包把文件写进内存，带回读校验 |
| `scan_pen_ports.py` | 扫笔的 TCP 端口，找隐藏服务 |

## 打包 / 镜像

| 脚本 | 用途 |
|---|---|
| `build_bootonly.py` | 造「只写 boot」的包（注释掉 package-file 其它行） |
| `rebuild_pkg.py` | 从零重建 YDIH 容器（偏移规则 `%0x800 == 0xC`） |
| `build_fit.py` | 往原厂 FIT 里追加 ramdisk 节点（不碰 kernel/dtb 字节） |
| `build_initramfs.py` | 生成备份用 initramfs（busybox + `/init` + 39 个 applet 软链） |
| `fit_parse.py` | FIT（FDT）解析与属性打印 |

## 实验环境

| 脚本 | 用途 |
|---|---|
| `ota_stage6.py` | 本地假 OTA 服务（:80，支持 Range/206、伪造 checkVersion） |
| `fake_dns_ota.py` | UDP 53 假 DNS，只劫持 `*abupdate*`，其余转发 |

## 使用前请确认

- 只在**自己的**设备与自己的局域网上运行
- `scan_pen_ports.py` / `fake_dns_ota.py` 会改动网络行为，用完请还原 DHCP 的 DNS
- 所有会写 flash 的操作都不在本目录的脚本里（风险太高，只写方法不提供一键脚本）
