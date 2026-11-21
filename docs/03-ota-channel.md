# OTA 通道：容器格式与可控写盘

> 本文只描述**协议与容器结构**。所有设备凭据（`productSecret` / `deviceSecret` /
> `deviceId` / 序列号）在公开版本中已替换为占位符。

## 1. 容器 `YDIH`

```
offset 0x00  magic       "YDIH"
offset 0x04  version/type
offset 0x08  u32         total_size（等于整包大小）
offset 0x18  u64         0x88（条目区相对偏移）
offset 0x88  magic       "YDII"
offset 0xA0  u32         条目数（本包为 7）
offset 0xA4  条目基址     0x124 + k * 0x88
```

每个条目（0x88 = 136 字节）：

| 偏移 | 类型 | 含义 |
|---|---|---|
| +0x00 | char[32] | name1（如 `boot.emmc`） |
| +0x20 | char[32] | name2 |
| +0x40 | — | 填充 |
| +0x60 | u64 | 载荷偏移 |
| +0x68 | u64 | `0xFFFFFFFFFFFFFFFF` |
| +0x70 | u64 | 载荷大小 |
| +0x80 | u32 | 标志（`0x800` / `0x488800`） |
| +0x84 | u32 | crc（**不是** zlib.crc32，且实测**不校验**） |

本机的 7 个载荷：

| 名称 | 偏移 | 大小 |
|---|---|---|
| `package-file` | 0x100C | 190 |
| `fip.bin` | 0x180C | 631,808 |
| `partition_emmc.xml` | 0x9C00C | 1,489 |
| `install.img` | 0x9C80C | 6,291,456 |
| `boot.emmc` | 0x69C80C | 4,753,272 |
| `rootfs.img` | 0xB2500C | 5,595,238 |
| `yoc.bin` | 0x107B80C | 1,336,864 |

**载荷偏移规则（实测）**：每个载荷起始 ≥ 上一段末尾，且满足 `offset % 0x800 == 0xC`
的最小值。按此规则可以从零重建整个容器，7 个载荷 round-trip 完全一致。

## 2. `package-file`：活的刷写清单

原始内容（190 字节，注意用 tab 对齐）：

```
# NAME          PATH
#HWDEF          HWDEF
 package-file   package-file
 bootloader     fip.bin
 parameter      partition_emmc.xml
 install        install.img
 boot           boot.emmc
 system         rootfs.img
 2nd            yoc.bin
```

**把某一行的首字符改成 `#`，设备就不会写那个分区**（保持字节长度不变，
不要在前面插入 `#`，否则长度变了会被工具自己的断言拦下，设备侧也可能解析失败）。

实测：只留 `install` + `boot` 的包 → 设备照常下载、安装、重启、系统正常、版本不变。

## 3. HTTP 接口

```
POST /product/<PRODUCT_ID>/<DEVICE_ID>/ota/checkVersion
POST /product/<PRODUCT_ID>/<DEVICE_ID>/ota/register
GET  /product/<PRODUCT_ID>/<DEVICE_ID>/ota/IMG
```

`checkVersion` 请求体（设备发出，字段就是这些）：

```json
{ "timestamp": 1795263560,
  "sign": "…",              // HMAC-MD5(deviceSecret, deviceId+productId+timestamp)
  "mid": "<序列号>",
  "productId": "<PRODUCT_ID>",
  "version": "4.23.0",
  "networkType": "WIFI" }
```

**设备不校验我们返回的响应**：只要返回体里有 `versionName` / `size` / `md5`，
它就去下载。我们用一个伪造的 `4.24.0` 响应把**原封不动的 4.23.0 包**推给设备，
设备照单全收、安装、重启，版本号仍显示 4.23.0（符合预期）。

`IMG` 请求带 `Range: bytes=a-b`，服务端回 206 即可。实测分 4 段：
`0-5242879`、`5242880-10485759`、`10485760-15728639`、`15728640-18620427`，
**18,620,428 B 全部下完**。

## 4. 复现环境（在自家局域网内）

1. 本地起一个 HTTP 服务监听 :80，按上面的接口应答；IMG 支持 Range/206。
2. 起一个假 DNS（UDP 53），把 `*abupdate*` 劫持到本机，其余转发给真实网关。
3. 把路由器 DHCP 下发的 DNS 改成这台机器（华为 AX3 的
   `/api/ntwk/lan_server` 接口，`DNSServerone/two` + `dnsmode`）。
4. 在笔上「检查更新」。

> **坑**：`kill` 掉服务进程的父 job 并不会杀掉 python 子进程，
> 于是 :80 上会有**两个监听者**，设备的请求被旧进程吞掉，表现为「没有可更新版本」。
> 排查方式：`netstat -ano | findstr :80`，按 PID 杀干净再验证只有一个监听者。

## 5. 为什么这条路只能写、不能读

- 设备只从我们这里**下载**，不向服务端上传任何分区内容。
- 升级过程由 rootfs 里的 `ydupdate_ab_ota` 二进制执行（`ydupdate_ab_ota.sh` 里
  `$1/ydupdate_ab_ota`），它不在 OTA 包里，也无法被我们替换。
- `install.img` 里的 8 个 shell 脚本**不会在 OTA 过程中被执行**：
  我们给 10 个脚本都注入了 `sleep 90`，设备仍在 17 秒内装完。
- `rootfs.img` 是 `HDIFF19&&crc32` 私有差分容器（全文件无 erofs/ext4/squashfs 魔数，
  熵 7.71），没有基准镜像就无法伪造内容 ⇒ **rootfs 层改不了**。
- `update_engine`（install.img 内 ELF32）二进制里 `RSA/CMS/PKCS/X509` 零命中，
  只有 MD5 校验（字符串 `md5 : not support sign image.`）⇒ **包级没签名**，
  这也解释了为什么改过的包能被接受。

## 6. 结论

OTA 是一条**已经被验证的、可控的写盘通道**：

- ✅ 能精确控制写哪些分区（package-file 注释）
- ✅ 能替换 boot 分区内容（写自定义 FIT）
- ✅ 不会因为「包内容与版本号不一致」被拒
- ❌ 不能读回任何分区

所以「先备份再动手」这个顺序，在 OTA 通道上是做不到的 —— 这也是本项目的核心矛盾。
