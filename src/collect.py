"""Data collection: WorkBuddy credits/tasks, weather, lunar calendar, holidays."""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import requests
from lunardate import LunarDate

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
CACHE.mkdir(exist_ok=True)
WB_DB = Path(os.environ.get("WORKBUDDY_HOME", Path.home() / ".workbuddy")) / "workbuddy.db"

CN_WEEK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
LUNAR_MONTH = ["正月", "二月", "三月", "四月", "五月", "六月",
               "七月", "八月", "九月", "十月", "冬月", "腊月"]
LUNAR_DAY = ["初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
             "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
             "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十"]

# WMO weather code -> short Chinese label
WMO = {
    0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾",
    51: "小雨", 53: "小雨", 55: "中雨", 56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪",
    80: "阵雨", 81: "阵雨", 82: "暴雨", 85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨", 99: "雷阵雨",
}


def _db() -> sqlite3.Connection:
    """Open the app database read-only via a temp copy (WAL-safe)."""
    tmp = Path(tempfile.gettempdir()) / "wb_epd_snapshot.db"
    try:
        src = sqlite3.connect(f"file:{WB_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    except Exception:
        shutil.copy(WB_DB, tmp)
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row
    return conn


def credits(quota: float = 0.0) -> dict:
    """Credits consumed today / this month / in total (all-time)."""
    out = {"today": 0.0, "month": 0.0, "total": 0.0, "remaining": None, "quota": quota}
    try:
        conn = _db()
        now = dt.datetime.now()
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        for row in conn.execute("select credit_json, updated_at from session_usage"):
            if not row["credit_json"]:
                continue
            try:
                vals = json.loads(row["credit_json"])
            except Exception:
                continue
            total = sum(v for v in vals.values() if isinstance(v, (int, float)))
            day = dt.datetime.fromtimestamp(row["updated_at"] / 1000).strftime("%Y-%m-%d")
            out["total"] += total
            if day == today:
                out["today"] += total
            if day[:7] == month:
                out["month"] += total
        conn.close()
    except Exception as e:  # db locked / missing
        out["error"] = f"{type(e).__name__}: {e}"
    out["today"] = round(out["today"], 2)
    out["month"] = round(out["month"], 2)
    out["total"] = round(out["total"], 2)
    return out


def running_tasks(limit: int = 3) -> list[dict]:
    """Sessions currently in 'working' state, local and cloud."""
    items = []
    try:
        conn = _db()
        rows = conn.execute(
            "select title, custom_title, transport, cwd, updated_at, is_background_automation "
            "from sessions where status='working' and deleted_at is null "
            "order by ifnull(last_activity_at, updated_at) desc limit ?",
            (limit + 4,),
        ).fetchall()
        now_ms = time.time() * 1000
        seen = set()
        for r in rows:
            title = (r["custom_title"] or r["title"] or "未命名任务").strip()
            if title in seen:
                continue
            seen.add(title)
            mins = max(0, int((now_ms - (r["updated_at"] or now_ms)) / 60000))
            items.append({
                "title": title,
                "scope": "云" if (r["transport"] == "cloud") else "本地",
                "minutes": mins,
                "auto": bool(r["is_background_automation"]),
            })
            if len(items) >= limit:
                break
        conn.close()
    except Exception:
        pass
    return items


def completed_today() -> dict:
    """Sessions that finished today, plus the most recent one."""
    out = {"count": 0, "latest": None, "items": []}
    try:
        conn = _db()
        today = dt.date.today().isoformat()
        rows = conn.execute(
            "select title, custom_title, transport, updated_at from sessions "
            "where deleted_at is null and status in ('completed', 'archived') "
            "and date(updated_at / 1000, 'unixepoch', 'localtime') = ? "
            "order by updated_at desc",
            (today,),
        ).fetchall()
        out["count"] = len(rows)
        out["items"] = [
            {
                "title": (r["custom_title"] or r["title"] or "未命名任务").strip(),
                "scope": "云" if r["transport"] == "cloud" else "本地",
                "time": dt.datetime.fromtimestamp(r["updated_at"] / 1000).strftime("%H:%M"),
            }
            for r in rows[:5]
        ]
        if out["items"]:
            out["latest"] = out["items"][0]
        conn.close()
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def _geo() -> tuple[float, float, str]:
    cached = CACHE / "geo.json"
    if cached.exists():
        try:
            d = json.loads(cached.read_text("utf-8"))
            return d["lat"], d["lon"], d["city"]
        except Exception:
            pass
    try:
        r = requests.get("http://ip-api.com/json/?lang=zh-CN", timeout=10)
        d = r.json()
        if d.get("status") == "success":
            data = {"lat": d["lat"], "lon": d["lon"], "city": d.get("city") or d.get("regionName")}
            cached.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
            return data["lat"], data["lon"], data["city"]
    except Exception:
        pass
    return 39.911, 116.395, "北京市"


def weather(days: list[dt.date], loc: tuple | None = None) -> dict:
    """Daily weather keyed by ISO date, covering past and future days of this week."""
    lat, lon, city = loc if loc else _geo()
    out = {"city": city, "days": {}}
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone": "Asia/Shanghai",
                "past_days": 7, "forecast_days": 8,
            },
            timeout=12,
        )
        d = r.json()["daily"]
        for i, day in enumerate(d["time"]):
            out["days"][day] = {
                "text": WMO.get(d["weather_code"][i], "—"),
                "code": d["weather_code"][i],
                "tmax": round(d["temperature_2m_max"][i]),
                "tmin": round(d["temperature_2m_min"][i]),
            }
    except Exception as e:
        out["error"] = type(e).__name__
    return out


def holidays(year: int) -> dict:
    """China holiday / make-up workday schedule, cached per year."""
    path = CACHE / f"holiday-{year}.json"
    if not path.exists():
        url = (f"https://ghproxy.net/https://raw.githubusercontent.com/"
               f"NateScarlet/holiday-cn/master/{year}.json")
        try:
            r = requests.get(url, timeout=20)
            if r.status_code == 200 and r.text.strip().startswith("{"):
                path.write_text(r.text, "utf-8")
        except Exception:
            pass
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
        return {d["date"]: d for d in data.get("days", [])}
    except Exception:
        return {}


def lunar_label(d: dt.date) -> str:
    try:
        l = LunarDate.from_solar_date(d.year, d.month, d.day)
        name = ("闰" if l.isLeapMonth else "") + LUNAR_MONTH[l.month - 1]
        day = "初一" if l.day == 1 else LUNAR_DAY[l.day - 1]
        return name if day == "初一" else name + day
    except Exception:
        return ""


def week_of(today: dt.date) -> list[dt.date]:
    start = today - dt.timedelta(days=today.weekday())
    return [start + dt.timedelta(days=i) for i in range(7)]


def build(quota: float = 0.0, loc: tuple | None = None) -> dict:
    today = dt.date.today()
    week = week_of(today)
    hol = {}
    for y in {today.year, (week[-1]).year}:
        hol.update(holidays(y))
    wx = weather(week, loc)
    days = []
    for d in week:
        h = hol.get(d.isoformat())
        w = wx["days"].get(d.isoformat())
        days.append({
            "date": d.isoformat(),
            "week": CN_WEEK[d.weekday()],
            "md": f"{d.month}/{d.day}",
            "month": d.month,
            "day": d.day,
            "lunar": lunar_label(d),
            "today": d == today,
            "weekend": d.weekday() >= 5,
            "holiday": h["name"] if h and h.get("isOffDay") else None,
            "workday": bool(h and not h.get("isOffDay")),
            "weather": w["text"] if w else "—",
            "code": w["code"] if w else -1,
            "tmax": w["tmax"] if w else None,
            "tmin": w["tmin"] if w else None,
        })
    return {
        "generated_at": dt.datetime.now().strftime("%m-%d %H:%M"),
        "today": today.isoformat(),
        "week_no": today.isocalendar()[1],
        "range": f"{week[0].month}月{week[0].day}日 — {week[-1].month}月{week[-1].day}日",
        "days": days,
        "city": wx.get("city", ""),
        "credits": credits(quota),
        "tasks": running_tasks(),
        "done": completed_today(),
        "errors": {k: v for k, v in (("weather", wx.get("error")),) if v},
    }


if __name__ == "__main__":
    print(json.dumps(build(0), ensure_ascii=False, indent=2))
