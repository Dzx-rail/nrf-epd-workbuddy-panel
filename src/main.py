"""Entry point: collect data -> render 400x300 frame -> push to the EPD tag.

Usage:
    python main.py            # collect, render, push (skips identical frames)
    python main.py --dry-run  # render only, write preview.png
    python main.py --force    # push even when the frame is unchanged
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGDIR = ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)
STATE = ROOT / "state.json"
LOCK = ROOT / ".run.lock"


def load_config() -> dict:
    cfg = {"device_name_prefix": "NRF_EPD", "device_address": None, "monthly_quota": 0,
           "settle_seconds": 18, "skip_if_unchanged": True, "max_tasks": 3,
           "location": {"lat": None, "lon": None, "city": None}}
    p = ROOT / "config.json"
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text("utf-8")))
        except Exception:
            pass
    return cfg


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        if sys.stdout:  # pythonw has no console
            print(line)
    except Exception:
        pass
    try:
        with open(LOGDIR / f"{dt.date.today():%Y-%m}.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def wb_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WorkBuddy.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return "WorkBuddy.exe" in out
    except Exception:
        return True


def acquire_lock() -> bool:
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime > 300:
            LOCK.unlink()
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock() -> None:
    try:
        LOCK.unlink()
    except Exception:
        pass


def _hhmm(text: str) -> dt.time:
    h, m = text.split(":")
    return dt.time(int(h), int(m))


def schedule_cfg(cfg: dict) -> dict:
    sch = cfg.get("schedule") or {}
    return {
        "start": _hhmm(sch.get("start", "09:00")),
        "end": _hhmm(sch.get("end", "18:00")),
        "step": max(1, int(sch.get("step_minutes", 15))),
        "restore": bool(sch.get("restore_calendar", True)),
    }


def next_event(cfg: dict, now: dt.datetime) -> tuple:
    """Return (when, action) for the next fixed slot; action is 'push' or 'calendar'."""
    sc = schedule_cfg(cfg)
    for offset in (0, 1):
        day = (now + dt.timedelta(days=offset)).date()
        cur = dt.datetime.combine(day, sc["start"])
        last = dt.datetime.combine(day, sc["end"])
        while cur <= last:
            if cur > now + dt.timedelta(seconds=5):
                is_end = cur.time() == sc["end"]
                return cur, ("calendar" if (is_end and sc["restore"]) else "push")
            cur += dt.timedelta(minutes=sc["step"])
    # schedule disabled / empty: fall back to a daily 09:00 push
    nxt = dt.datetime.combine(now.date() + dt.timedelta(days=1), sc["start"])
    return nxt, "push"


def daemon() -> None:
    """Resident scheduler: fixed 15-minute slots inside the working window."""
    import socket

    guard = socket.socket()
    for attempt in (1, 2):
        try:  # simple process mutex: only one daemon may hold the port
            guard.bind(("127.0.0.1", 47913))
            guard.listen(1)
            break
        except OSError:
            if attempt == 2:
                log("daemon: 端口被占用且无法接管，退出")
                return
            old = 0
            try:
                old = int((ROOT / ".daemon.pid").read_text().strip())
            except Exception:
                pass
            if not old:
                log("daemon: 已有实例在运行，退出")
                return
            log(f"daemon: 接管旧实例 PID {old}")
            subprocess.run(["taskkill", "/PID", str(old), "/F"],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            time.sleep(2)

    (ROOT / ".daemon.pid").write_text(str(os.getpid()), "utf-8")

    cfg = load_config()
    sc = schedule_cfg(cfg)
    log(f"daemon: 排程 {sc['start']:%H:%M}-{sc['end']:%H:%M} 每 {sc['step']} 分钟"
        f"{'，收工后切回原版日历' if sc['restore'] else ''}")

    # 启动时补一次：窗口内立即刷新，收工后立即切回原版日历，开工前不动
    boot = dt.datetime.now().time()
    if sc["restore"] and boot >= sc["end"]:
        log("daemon: 当前已过收工时间，立即切回原版日历")
        try:
            subprocess.run([sys.executable, str(ROOT / "main.py"), "--calendar-mode"],
                           cwd=str(ROOT), timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            log(f"daemon: 启动补刷异常 {type(e).__name__}: {e}")
    elif sc["start"] <= boot < sc["end"]:
        log("daemon: 处于工作时段，立即刷新一次")
        try:
            subprocess.run([sys.executable, str(ROOT / "main.py")],
                           cwd=str(ROOT), timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            log(f"daemon: 启动补刷异常 {type(e).__name__}: {e}")

    while True:
        now = dt.datetime.now()
        when, action = next_event(load_config(), now)
        log(f"daemon: 下次 {when:%m-%d %H:%M} → {'切回原版日历' if action == 'calendar' else '推送'}")
        while True:  # sleep in chunks so clock jumps / config edits are picked up
            remain = (when - dt.datetime.now()).total_seconds()
            if remain <= 0:
                break
            time.sleep(min(remain, 60))
        cmd = [sys.executable, str(ROOT / "main.py")]
        if action == "calendar":
            cmd.append("--calendar-mode")
        try:
            subprocess.run(cmd, cwd=str(ROOT), timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            log(f"daemon: 本轮异常 {type(e).__name__}: {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--config", default=None)
    ap.add_argument("--calibrate", type=float, default=None,
                    help="以当前余额重设基准，例如 --calibrate 2723.23")
    ap.add_argument("--calendar-mode", action="store_true",
                    help="切回固件原版日历（18:00 由排程自动调用）")
    ap.add_argument("--device", default=None, help="临时指定目标设备地址，如 12:34:56:78:9A:BC")
    ap.add_argument("--list-devices", action="store_true", help="扫描并列出所有墨水屏")
    args = ap.parse_args(argv)

    if args.daemon:
        try:
            daemon()
        except KeyboardInterrupt:
            log("daemon: 退出")
        return 0

    cfg = load_config()
    for i in range(3):
        try:
            import collect
            import render
            import epd
            break
        except ImportError as e:
            if i == 2:
                log(f"依赖加载失败: {e}")
                return 3
            log(f"依赖加载被拦截（{type(e).__name__}），4 秒后重试 {i + 2}/3")
            time.sleep(4)

    if args.list_devices:
        import asyncio
        import epd
        tag = epd.EpdTag(name_prefix=cfg.get("device_name_prefix", "NRF_EPD"), log=log)
        hits = asyncio.run(tag._scan(10))
        if not hits:
            log("未发现任何墨水屏")
        for h in hits:
            mark = "*" if (cfg.get("device_address") or "").upper() == h["addr"].upper() else " "
            log(f"{mark} {h['name']:<18} {h['addr']}  rssi={h['rssi']}")
        return 0

    target_addr = args.device or cfg.get("device_address")

    if args.calendar_mode:
        if not acquire_lock():
            log("另一实例正在运行，本次跳过")
            return 0
        try:
            info = epd.EpdTag(
                name_prefix=cfg.get("device_name_prefix", "NRF_EPD"),
                address=target_addr,
                name=cfg.get("device_name"),
                log=log,
            ).calendar_sync(1, settle=float(cfg.get("settle_seconds", 18)))
            log(f"已切回原版日历模式 fw={info.get('firmware')} cmd={info.get('sent')}")
            return 0
        except Exception as e:
            log(f"切换日历模式失败: {type(e).__name__}: {e}")
            return 2
        finally:
            release_lock()

    loc = None
    lc = cfg.get("location") or {}
    if lc.get("lat") and lc.get("lon"):
        loc = (float(lc["lat"]), float(lc["lon"]), lc.get("city") or "")

    running = wb_running()
    data = collect.build(float(cfg.get("monthly_quota") or 0), loc)

    # remaining credits = baseline balance - credits consumed since calibration
    st = {}
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text("utf-8"))
        except Exception:
            st = {}
    cr = data["credits"]
    total_tracked = max(float(st.get("total_max") or 0.0), float(cr.get("total") or 0.0))

    baseline = cfg.get("credit_baseline")
    if args.calibrate is not None:
        baseline = {"remaining": round(args.calibrate, 2), "total_used": total_tracked,
                    "at": dt.datetime.now().isoformat(timespec="seconds")}
        cfg["credit_baseline"] = baseline
        (ROOT / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
        log(f"已校准：余额 {args.calibrate}，累计消耗基准 {total_tracked}")
    if baseline and baseline.get("remaining") is not None:
        used_since = max(0.0, total_tracked - float(baseline.get("total_used") or 0.0))
        cr["remaining"] = round(max(0.0, float(baseline["remaining"]) - used_since), 2)

    # 今日消耗改用「总量差值」，避免跨零点会话被整体算到后一天
    today_str = dt.date.today().isoformat()
    daily = st.get("daily") or {}
    if daily.get("date") != today_str:
        # 当天首次采样即为当日起点（本地库按会话归集不可靠，不做补算）
        start = total_tracked
        daily = {"date": today_str, "start_total": start}
        log(f"今日消耗起点已重置：{start:.2f}（当日累计 {total_tracked:.2f}）")
    st["daily"] = daily
    cr["today"] = round(max(0.0, total_tracked - float(daily["start_total"])), 2)
    # 立刻落盘，避免重叠运行时重复推导当日起点
    st["total_max"] = total_tracked
    try:
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass

    if not running:
        data["tasks"] = []
        data["wb_running"] = False
        log("WorkBuddy 未运行，任务区置空")
    else:
        data["wb_running"] = True

    data["weather_icon"] = bool(cfg.get("weather_icon", False))
    cfg_tasks = int(cfg.get("max_tasks") or 3)
    data["tasks"] = data["tasks"][:cfg_tasks]

    img = render.render(data)
    img.save(ROOT / "last_frame.png")

    if args.dry_run:
        img.resize((800, 600), 0).save(ROOT / "preview_2x.png")
        log(f"dry-run: 已生成 last_frame.png  cities={data['city']} "
            f"credits={data['credits']} tasks={len(data['tasks'])}")
        return 0

    # content hash ignores the footer clock so identical content is not re-flashed
    signature = json.dumps(
        {k: v for k, v in data.items() if k not in ("generated_at",)},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    digest = hashlib.sha256(signature.encode()).hexdigest()[:16]
    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text("utf-8"))
        except Exception:
            prev = {}
    if cfg.get("skip_if_unchanged", True) and not args.force and prev.get("digest") == digest:
        log(f"内容未变（{digest}），跳过刷新")
        prev["total_max"] = total_tracked
        prev["daily"] = daily
        STATE.write_text(json.dumps(prev, ensure_ascii=False, indent=2), "utf-8")
        return 0

    if not acquire_lock():
        log("另一实例正在运行，本次跳过")
        return 0

    try:
        last_err = None
        for attempt in (1,):
            try:
                info = epd.EpdTag(
                    name_prefix=cfg.get("device_name_prefix", "NRF_EPD"),
                    address=target_addr,
                    name=cfg.get("device_name"),
                    log=log,
                ).push_sync(img, settle=float(cfg.get("settle_seconds", 18)))
                STATE.write_text(json.dumps({
                    "digest": digest,
                    "total_max": total_tracked,
                    "daily": daily,
                    "pushed_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "info": info,
                }, ensure_ascii=False, indent=2), "utf-8")
                log(f"推送成功 {info.get('packets')} 包 / {info.get('tx_seconds')}s "
                    f"driver={info.get('driver_name')} fw={info.get('firmware')}")
                return 0
            except Exception as e:
                last_err = e
                log(f"第 {attempt} 次推送失败: {type(e).__name__}: {e}")
                # 兜底：重置蓝牙适配器后再试一次（会短暂中断其它蓝牙外设）
                try:
                    log("重置蓝牙适配器后重试一次")
                    epd.reset_radio()
                    info = epd.EpdTag(
                        name_prefix=cfg.get("device_name_prefix", "NRF_EPD"),
                        address=target_addr,
                        name=cfg.get("device_name"),
                        log=log,
                    ).push_sync(img, settle=float(cfg.get("settle_seconds", 18)))
                    STATE.write_text(json.dumps({
                        "digest": digest, "total_max": total_tracked, "daily": daily,
                        "pushed_at": dt.datetime.now().isoformat(timespec="seconds"), "info": info,
                    }, ensure_ascii=False, indent=2), "utf-8")
                    log(f"重置后推送成功 {info.get('packets')} 包 / {info.get('tx_seconds')}s")
                    return 0
                except Exception as e2:
                    log(f"重置后仍失败: {type(e2).__name__}: {e2}")
                time.sleep(6)
        log(f"推送最终失败: {last_err}")
        return 2
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
