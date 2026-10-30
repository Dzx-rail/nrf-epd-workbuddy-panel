# epaper-panel

把桌面工作状态推到一块 4.2 寸黑白红三色墨水屏上：**本周日历 + 积分余额 + 任务进度**，
按工作时段定时刷新，收工后自动切回固件自带的日历。

Push your daily dashboard onto a 4.2" tri-colour e-paper tag: weekly calendar, credit balance
and task progress — refreshed on a schedule, switching back to the firmware's own calendar
after hours.

## 界面

```
┌─────────────────────────┬──────────────┐
│ 本周 10/26 — 11/01 第44周│ 积分          │
│ 周一 10/26 九月十七 ☀ 17/9│            │
│ 周二 10/27 九月十八 ☁ 14/7│   2568.0     │
│ 周三 10/28 九月十九 ☀ 14/3│ 剩余          │
│ ██周四 10/29 九月二十 ☁ 18/6██│ 今日 -105.4  │
│ 周五 10/30 九月廿一 ☀ 20/6│ ───────────  │
│ 周六 10/31 九月廿二 ☁ 11/4│ 进行中      1 │
│ 周日 11/01 九月廿三 ☁ 17/5│ 为墨水屏显示… │
│                         │ 本地 · 0 分钟 │
│                         │ ───────────  │
│                         │ 今日完成    5 │
│                         │ 美团自动领券  │
│                         │ 10:15 · 本地 │
│                         │ ───────────  │
│                         │ 更新 16:44 北京│
└─────────────────────────┴──────────────┘
```

今天整行反白；休息日（周末与法定节假日）整行红字；调休补班的周末按工作日处理。
天气用几何图标而非汉字——原因见 `docs/readability.md`。

## 支持的硬件

| 项目 | 说明 |
| --- | --- |
| 固件 | 开源 [EPD-nRF5](https://github.com/tsl0922/EPD-nRF5)，出厂多为 0x17 |
| 广播名 | `NRF_EPD_XXXX`（`XXXX` 为 MAC 末两字节） |
| 屏幕 | 4.2" 400×300，黑白红三色，驱动 `0x02` SSD1619 |
| 传输 | BLE GATT，MTU 244，整帧 124 包，约 20 秒 |

同类标签出厂即带此固件，**不需要拆机、不需要 SWD 烧录**。

## 快速开始

```bash
pip install -r src/requirements.txt

# 1. 找到你的标签，记下地址
python src/main.py --list-devices

# 2. 写配置
cp examples/config.example.json src/config.json   # 填入上一步的地址

# 3. 先看渲染效果，不连蓝牙
python src/main.py --dry-run                      # 生成 src/last_frame.png

# 4. 推送
python src/main.py --force

# 5. 锚定积分余额（可选，右半屏需要）
python src/main.py --calibrate 2568.0

# 6. 常驻定时刷新
python src/main.py --daemon
```

## 命令行

| 参数 | 作用 |
| --- | --- |
| `--dry-run` | 只渲染，生成 `last_frame.png`，不连蓝牙 |
| `--force` | 强制刷新，忽略内容摘要 |
| `--calibrate N` | 以当前余额 N 重建锚点，之后按消耗递减 |
| `--calendar-mode` | 切回固件原版日历（18:00 自动调用） |
| `--list-devices` | 扫描范围内所有同类标签，`*` 为已锁定 |
| `--device ADDR` | 临时指定目标，不改配置 |
| `--daemon` | 常驻，按 `schedule` 在固定时刻触发 |

## 配置

```json
{
  "device_address": "12:34:56:78:9A:BC",
  "weather_icon": true,
  "max_tasks": 3,
  "skip_if_unchanged": true,
  "settle_seconds": 18,
  "schedule": { "start": "09:00", "end": "18:00", "step_minutes": 15, "restore_calendar": true }
}
```

`schedule` 决定工作时段：09:00–17:45 每 15 分钟推一次，18:00 改发 `SET_TIME` 切回固件日历。

## 数据来源

| 内容 | 来源 |
| --- | --- |
| 积分与任务 | 本地 `~/.workbuddy/workbuddy.db`（WorkBuddy 桌面端写入） |
| 天气 | [Open-Meteo](https://open-meteo.com/)，免密钥，含过去 7 天 |
| 节假日 | [NateScarlet/holiday-cn](https://github.com/NateScarlet/holiday-cn) |
| 农历 | [lunardate](https://pypi.org/project/lunardate/) |
| 定位 | ip-api 自动识别，结果缓存到 `cache/geo.json` |

积分/任务部分依赖 WorkBuddy 桌面端；若仅需日历，其余功能不受影响。

## 设计取舍

**剩余积分是推算值。** 本地库只记录消耗，没有账户余额字段，服务端也无法匿名读取
（Cookie 为 Chromium v20 应用绑定加密）。因此以用户看到的余额为锚点，按新增累计消耗递减；
充值或结算后需重新校准。

**内容未变就不刷。** 三色屏全刷约 15~20 秒且会闪。摘要排除时间戳，数据静止时屏幕会停在
上一次成功刷新的时刻，这是刻意设计，不是故障。

**多设备必须锁定地址。** 按名称前缀取"第一个搜到的"会推错屏。锁定地址后未命中即拒绝，
不回退到其他设备——推错到别人的屏上是不可撤销的操作。

## 已知限制

- 电量/电压读不到：固件不通过 BLE 上报，只在它自绘的日历界面显示
- 小字号中文最小 12px，9~10px 必然残缺，调整阈值无法补救（实测数据见 `docs/readability.md`）
- 定时与自启动依赖平台能力，本仓库在 Windows 上验证；其他平台需换 systemd timer / launchd
- 三色屏刷新有寿命，不建议把间隔压到 15 分钟以下

## 文档

- [`docs/protocol.md`](docs/protocol.md) — 帧格式、位平面打包、MTU 与分包、日历模式指令
- [`docs/ble-troubleshooting.md`](docs/ble-troubleshooting.md) — 六类典型 BLE 故障的判定与处置
- [`docs/readability.md`](docs/readability.md) — 字号下限、彩色文字阈值、图标替代的量化结论

## 致谢

协议实现参考 [tsl0922/EPD-nRF5](https://github.com/tsl0922/EPD-nRF5) 的固件源码。
该项目与本仓库无隶属关系。

## License

MIT
