---
name: epaper-dashboard
description: 把 WorkBuddy 的积分余额、进行中/已完成任务，连同本周日历（星期、农历、天气、节假日）推送到 4.2 寸黑白红三色电子纸标签（EPD-nRF5 固件），并按工作时段定时刷新、收工后切回固件自带日历。当用户提到「墨水屏」「电子纸」「EPD」「NRF_EPD」「积分上屏」「桌面状态屏」，或要求把桌面信息/日历显示到低功耗小屏时使用。
agent_created: true
---

# 电子纸状态屏

## 概述

在 400×300 三色墨水屏上呈现一屏桌面状态：左半是本周日历（星期、日期、农历、天气、节假日标记），
右半是积分余额与今日消耗、进行中任务、今日已完成任务。通过 BLE 直连标签推送 1bpp 双平面位图，
不走厂商 App，也不需要拆机刷机——市面上多数 `NRF_EPD_*` 标签出厂即带开源固件 EPD-nRF5。

## 适用前提

1. 标签广播名形如 `NRF_EPD_XXXX`，其中 `XXXX` 是 MAC 末两字节（如 `NRF_EPD_9ABC` → `…:9A:BC`）。
   这是 EPD-nRF5 固件的命名规则，可用本技能的协议层驱动。
2. 主机具备 BLE（Windows 走 WinRT，macOS/Linux 走 BlueZ/CoreBluetooth），Python 3.10+。
3. 可选：本机运行 WorkBuddy 桌面端，用于读取积分与任务；缺失时右半屏留空，日历部分仍可用。

## 工作流程

### 第 1 步：安装依赖并探测设备

```bash
pip install -r scripts/requirements.txt
python scripts/main.py --list-devices
```

输出形如 `* NRF_EPD_9ABC   12:34:56:78:9A:BC  rssi=-62`。若有多块标签，
`*` 标记的是 config 中已锁定的那块——**多设备场景必须锁定地址**，否则技能会拒绝推送而不是猜。

把探测到的地址写入 `config.json`（由 `assets/config.example.json` 复制而来）：

```bash
cp assets/config.example.json scripts/config.json
```

### 第 2 步：渲染预览（不连蓝牙）

```bash
python scripts/main.py --dry-run
```

生成 `scripts/last_frame.png`。先用图片确认版面，再上屏——三色屏每次全刷约 15~20 秒且会闪，
不做预览直接刷会浪费很多次刷新寿命。

### 第 3 步：首次推送

```bash
python scripts/main.py --force
```

约 20~40 秒完成（连接 + 124 个数据包 + 刷新等待）。失败会按 3 次重试，间隔 8 秒。

### 第 4 步：校准积分基准（右半屏需要）

本地数据库只记录**消耗**，没有账户余额，因此余额需人工锚定一次：

```bash
python scripts/main.py --calibrate 2804.06   # 填入用户端看到的当前余额
```

之后屏幕上的剩余积分 = 锚点 − 新增累计消耗。用户充值或结算后重新校准即可。
若不做校准，右半屏显示「本月已用」而非「剩余」。

### 第 5 步：常驻定时刷新

```bash
python scripts/main.py --daemon
```

按 `schedule` 配置在固定时刻触发：工作时段每 N 分钟推送一次，收工时刻改发
`SET_TIME(0x20, mode=1)` 让固件自己画回原版日历（含农历、电压），次日开工时刻恢复。

## 关键约束

**定时任务与自启动是平台相关的。** 本技能在 Windows 上验证过：计划任务常被组策略拒绝
（`0x80070005`），可行方案是「启动文件夹放一个 `.cmd` + 常驻进程」，并用端口互斥防重复。
其他平台需换用 systemd timer / launchd，`--daemon` 参数本身是跨平台的。

**三色屏寿命有限。** `skip_if_unchanged` 默认开启：内容摘要（排除时间戳）未变则跳过刷新。
这会让屏幕在数据静止时看起来「没更新」，属于预期行为，不要误判为故障。

**连接失败多半不是代码问题。** 详见 `references/ble-troubleshooting.md`，
顺序是：先看广告间隔（正常 1~2s，6s 说明设备进了省电广播）、再看是否残留下链路、最后才怀疑代码。

## 参考文档

- `references/protocol.md` — EPD-nRF5 帧格式、位平面打包、MTU 与分包、日历模式指令
- `references/ble-troubleshooting.md` — Windows 蓝牙栈的六类典型故障与判定方法
- `references/readability.md` — 小屏可读性的量化结论（字号下限、彩色文字阈值、图标替代）

修改版式前务必先读 `readability.md`，那里的结论都是实测得出的，重走一遍成本很高。
