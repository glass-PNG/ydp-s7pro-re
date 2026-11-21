# Roadmap：待解问题与建议的验证顺序

下面每一条都写清楚「已知什么」「还缺什么」「怎么验证」。欢迎直接开 issue 认领。

---

## Q1（最高优先）怎么让 ROM 真正跳到 u-boot？

**已知**
- BL2 分派器在 VA `0x8003fb7c`；token `0x03` → `0x8003fbac`：
  打印 `NOTICE:  CVI_USB_JUMP to %llx` → `blr x21` → 返回后打印 `JUMP back`
- 厂商 CLI（`usb_dl.exe`，32 位 PE）对 cv182x 走的流程是：
  magic(128B, token 3) → fip 前 64KiB 到 `0xC0120000`(token 0) → `"1NGM"` 到 `0x0E000004`(token 1)
  → BREAK(token 2) → 重新枚举 → 再 magic → 整份 fip 到 `0x80800000`(token 1)
  → `"1000"` 到 `0x0C021FFC`(token 0) → BREAK
- 实机：token `0x03` 无论地址、无论桩是否返回，**一发就掉 USB 并重启**
- token `0x02` 在跳表里**未实现**

**还缺**：这台笔的 ROM 到底用什么方式结束下载状态并跳到 RAM 里的 fip。

**建议验证**
1. 只发 token `0x01` + `"1NGM"@0x0E000004`，看 ACK 是否正常（不跳转，安全）
2. 再只发 token `0x02`（BREAK，16B 头 / 8B 头各试一次），观察是否重枚举为 `3346:1001`
3. 试 `token 0x03` + addr 指向 **`0x80800000`（已上传的 fip）**，但把桩换成
   「写一个标记到 `0x93202000` 然后 `b .`」，看设备是「跳过去执行了」还是「直接复位」
   —— 区分「JUMP 实现了但目标不对」和「token 3 根本不是 JUMP」
4. 用 USBPcap/Wireshark 抓一次**厂商 GUI 工具**（`CviUsbDownload`）在本机上的真实报文
   （本机缺 USBPcap，需要装）

## Q2 `0x83` + `0x0a` 为什么读不到用户区？

**已知**
- 结构体 `{u32 0, u32 size, u64 offset}` 放在 `0x93200000`
- 设备侧等价：`snprintf("mmc read %p 0x%llx 0x%x", 0x93200040, offset>>9, size>>9)` + `run_command`
- 返回值存 `[0x800b3c30]`；`0x0a` 发现它为 0 时打印错误，但**仍然发旧缓冲**
- 实测：无论偏移多少，拿到的都是全零/全 0xFF（说明 `mmc read` 失败了）

**还缺**：`mmc read` 失败的具体原因（设备号不对？`mmc dev 0 0` 没执行？地址越界？）

**建议验证**
1. 先用 `0x83` 读 `offset=0`（boot0 的第 0 扇区），与 `0x0c` 的结果比对
   —— 如果这里也失败，说明问题不在「用户区」而在命令上下文
2. 试 `offset` 用**扇区**而不是字节（有些实现参数单位不同）
3. 试 `size` 用 512 的整数倍且 `offset` 也在 512 边界
4. 观察 ACK 的 `aux` 字段（`[8:16]`）是否返回扇区数，可反推成功/失败

## Q3 怎么进 `3346:1001`？

**已知**：u-boot 有 `cvi_utask` 任务，枚举为 `3346:1001`；u-boot 里有
`CVI_USB_READ_BACK` / `mmc read` 的完整实现。

**还缺**：从 ROM 到 u-boot 的那一步（与 Q1 同一个问题）。

## Q4 `ENV` 分区怎么改？

**已知**：真实 bootargs 来自 u-boot env（`p4 ENV` 128 KiB + 备份）。
FIT 里的 bootargs 是 SDK 模板，改了无效。

**建议**：先把 ENV 读出来（如果能进 u-boot，`printenv` 即可；
ROM 阶段可试 `0x83` 读 `p4` 的偏移）。ENV 大概率是标准 `crc32 + 0x01 + "key=value"` 格式。

## Q5 rootfs 有没有别的入口？

**已知**：`rootfs.img` 是 `HDIFF19&&crc32` 差分容器（熵 7.71），
没有基准就无法伪造；设备侧是 EROFS 只读。

**建议**
1. 找 `ydupdate_ab_ota` 二进制（在 rootfs 里），看它怎么解析差分容器
2. 看 `HDIFF19` 是不是公开格式（有同名工具的话就能自制补丁）
3. userdata 独立脚本位置 `data_local.sh`（见交接记录）

## Q6 MTP 能不能当文件通道？

**已知**：正常态 MTP 根只有 `Music` 与 `Favorite` 两级目录，
ADB 被登录墙挡住，但不能排除 MTP 写文件到 `userdisk` 的可能。

**建议**：用 libmtp / Windows MTP API 往 `Music` 下推一个小文件，
再从设备侧确认落点，确认 MTP 是「只读视图」还是「可写共享」。

## Q7 有没有其它调试口？

**建议**：网络侧扫 `<PEN_IP>` 的 1–65535 端口（本仓库 `scripts/scan_pen_ports.py`），
找 ssh/telnet/adb-tcp/自定义服务。已知第三方笔记提到同系列设备出现过
`5555` 端口与 `cherry` 类密码，值得一试。

---

## 已排除的路线（别再踩）

| 路线 | 结论 |
|---|---|
| `install.img` 里注入 `sleep 90` 脚本 | 无效，10 个脚本注入后仍在 17 秒内装完 |
| 改 FIT 里的 bootargs | 无效，被 u-boot env 覆盖 |
| 改 DRAM 里的 `mmc dev 0 1` 字符串 | 无效，协议代码不在可读 DRAM（在片内 SRAM） |
| 用内核 USB gadget 做 U 盘 | 内核无 `dwc2`/`g_mass_storage`/`configfs` |
| 用 libusb-1.0 走 CDC bulk | 本机驱动下 bulk 全废（`LIBUSB_ERROR_IO/PIPE`），`usbser.sys` 反而正常 |
| 用 8 字节头发 `0x82` / 用 16 字节头发 `0x00` | 都失败，两种帧头不能混 |
| 用 512/1024/2048 长度调 `0x82` | 无响应，必须 4096 |
