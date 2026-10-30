# EPD-nRF5 传输协议

来源：开源项目 `tsl0922/EPD-nRF5`（设备侧固件），本技能在固件 `0x17` 上实测通过。

## GATT 结构

| 用途 | UUID |
| --- | --- |
| 主服务 | `62750001-d828-918d-fb46-b6c11c675aec` |
| 数据特征（notify + write + write-without-response） | `62750002-d828-918d-fb46-b6c11c675aec` |
| 版本特征（read，1 字节固件版本） | `62750003-d828-918d-fb46-b6c11c675aec` |
| DFU 服务（存在，本技能不涉及） | `0000fe59-…` |

**没有电池服务（`0x180F`），也没有任何特征会返回电压。** 电压只在设备内部由
`EPD_ReadVoltage()` 采集，喂给固件自己的日历界面；广播包不含 manufacturer/service data。
若用户要求在自绘界面上显示电量，只能改固件并 OTA 刷入——先把这一点讲清楚，避免反复排查。

## 指令

| 指令 | 值 | 说明 |
| --- | --- | --- |
| INIT | `0x01` | 初始化，无参数；返回配置通知 |
| WRITE_IMG | `0x30` | 格式 `[0x30, frame_flag, ...data]` |
| REFRESH | `0x05` | 触发刷新，随后等待 15~20 秒 |
| SET_TIME | `0x20` | `[0x20, ts_be32, tz_int8, mode]`，mode=1 日历模式、2 时钟模式 |
| SET_PINS / SET_CONFIG 等 | `0x10`… | 本技能不使用 |

## 数据帧标志位

`frame_flag = 平面标志 | 续包标志`

- 黑白平面：首包 `0x0F`，续包 `0xFF`
- 红色平面：首包 `0x00`，续包 `0xF0`

## 位图格式

1bpp、MSB first、按行打包，每行 `width/8` 字节。

- 黑白平面：bit=1 表示白，bit=0 表示黑。灰度 ≥140 判白（实测取值，见 readability.md）
- 红色平面：bit=0 表示红，bit=1 表示非红
- 3 色屏依次发送「黑白平面(15000B) + 红色平面(15000B)」，共 30000 字节

## 分包与 MTU

Windows 侧协商 MTU 247 → ATT 载荷 244 → 每包数据 242 字节 → 整帧 124 包。

固件在 INIT 后会以字符串通知形式回报自身支持的载荷，格式 `mtu=244`。
优先采用该值，缺失时退回 `cli.mtu_size - 3`，上限 244。

分片策略：每包 `write-without-response`，每 50 包和最后一包改用 `write-with-response`，
作为流控与确认点——纯无响应写在高吞吐下容易被底层丢弃。

## 配置通知

INIT 之后设备返回 13 字节配置，与固件 `epd_config_t` 逐字段对应：

```
mosi, sclk, cs, dc, rst, busy, bs, model_id, wakeup_pin, led_pin, en_pin, display_mode, week_start
```

- 第 8 字节（索引 7）是 `model_id`：`0x02` = 4.2" SSD1619 三色
- 最后两字节为 `display_mode`（1=日历，2=时钟）与 `week_start`
- 长度恰好 13，**不含电压**，可据此向用户解释为什么读不到电量

## 切回固件日历

```
SET_TIME: [0x20, ts>>24, ts>>16, ts>>8, ts, tz, 1]
tz = -(time.timezone)/3600    # 东八区为 8
```

发送后设备自行绘制原版日历（含农历、温度、电压、电量图标），并在每天 00:00 重新绘制。
次日再推图时，`INIT` 会重置显示上下文，无需额外唤醒指令。
