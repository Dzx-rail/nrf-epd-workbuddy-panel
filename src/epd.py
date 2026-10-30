"""EPD-nRF5 BLE host implementation (Windows / bleak).

Protocol reference: https://github.com/tsl0922/EPD-nRF5
Service 62750001-...; data char 62750002-...; version char 62750003-...
Commands: INIT 0x01 / REFRESH 0x05 / WRITE_IMG 0x30 (payload = frame byte + data)
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from bleak import BleakClient, BleakScanner
from PIL import Image

SVC_UUID = "62750001-d828-918d-fb46-b6c11c675aec"
CH_DATA = "62750002-d828-918d-fb46-b6c11c675aec"
CH_VER = "62750003-d828-918d-fb46-b6c11c675aec"

CMD_INIT = 0x01
CMD_REFRESH = 0x05
CMD_SET_TIME = 0x20
CMD_WRITE_IMG = 0x30

# driver id -> (name, color mode, width, height)
DRIVERS = {
    0x01: ("4.2 UC8176", "bw", 400, 300),
    0x02: ("4.2 SSD1619", "bwr", 400, 300),
    0x03: ("4.2 UC8176", "bwr", 400, 300),
    0x04: ("4.2 SSD1619", "bw", 400, 300),
    0x05: ("4.2 JD79668", "4c", 400, 300),
}


def pack_bw(img: Image.Image) -> bytes:
    """Grayscale -> 1bpp MSB-first; bit 1 = white, 0 = black."""
    w, h = img.size
    raw = img.convert("L").tobytes()
    stride = w // 8
    out = bytearray(stride * h)
    for y in range(h):
        row, obase = y * w, y * stride
        for xb in range(stride):
            v = 0
            for b in range(8):
                if raw[row + xb * 8 + b] >= 140:
                    v |= 0x80 >> b
            out[obase + xb] = v
    return bytes(out)


def pack_red(img: Image.Image) -> bytes:
    """1bpp MSB-first; bit 0 = red, 1 = not red."""
    w, h = img.size
    px = img.convert("RGB").load()
    stride = w // 8
    out = bytearray(stride * h)
    for y in range(h):
        obase = y * stride
        for xb in range(stride):
            v = 0
            for b in range(8):
                r, g, bl = px[xb * 8 + b, y]
                # 要求明显的红饱和度，避免抗锯齿的浅粉边缘被判成红色而让字形变胖
                if not (r > 150 and (r - max(g, bl)) > 70):
                    v |= 0x80 >> b
            out[obase + xb] = v
    return bytes(out)


def to_planes(img: Image.Image) -> bytes:
    """Black/white plane followed by red/white plane (3-colour layout)."""
    return pack_bw(img) + pack_red(img)


class EpdTag:
    def __init__(self, name_prefix: str = "NRF_EPD", address: Optional[str] = None,
                 name: Optional[str] = None, log=print):
        self.name_prefix = name_prefix
        self.address = address
        self.name = name
        self.log = log

    async def _scan(self, budget: float = 30.0) -> list[dict]:
        """Collect every matching tag in range: [{dev, addr, name, rssi}]."""
        prefix = self.name_prefix.upper()
        deadline = time.time() + budget
        hits: dict[str, dict] = {}
        async with BleakScanner(return_adv=True) as scanner:
            while time.time() < deadline:
                await asyncio.sleep(0.8)
                for addr, (dev, adv) in scanner.discovered_devices_and_advertisement_data.items():
                    nm = dev.name or adv.local_name or ""
                    if prefix not in nm.upper():
                        continue
                    hits[addr.upper()] = {"dev": dev, "addr": addr, "name": nm, "rssi": adv.rssi or -999}
                    if self.address and addr.upper() == self.address.upper():
                        return [hits[addr.upper()]]          # 命中锁定地址，无需继续扫
                    if self.name and nm.upper() == self.name.upper():
                        return [hits[addr.upper()]]
        return sorted(hits.values(), key=lambda h: h["rssi"], reverse=True)

    async def _find(self, budget: float = 40.0):
        """Pick the target tag. Pinned address/name wins; a single match is accepted;
        multiple matches without a pin is refused rather than guessed."""
        found = await self._scan(budget)

        # 锁定了地址：只认这一个，绝不回退到别的设备（附近可能有他人的屏）
        if self.address:
            for h in found:
                if h["addr"].upper() == self.address.upper():
                    self.log(f"目标设备（地址锁定）{h['name']} {h['addr']} rssi={h['rssi']}")
                    return h["dev"]
            others = "、".join(f"{h['name']}({h['addr']})" for h in found) or "无"
            raise RuntimeError(
                f"锁定的设备 {self.address} 未出现在本次扫描中（其余可见：{others}），"
                f"已跳过以避免推错屏；请检查设备是否上电，或执行 --list-devices 核对地址"
            )

        if not found:
            return None

        if self.name:
            for h in found:
                if h["name"].upper() == self.name.upper():
                    self.log(f"目标设备（名称锁定）{h['name']} {h['addr']} rssi={h['rssi']}")
                    return h["dev"]
            raise RuntimeError(f"锁定的名称 {self.name} 未出现，已跳过（其余可见 {len(found)} 块）")

        if len(found) == 1:
            h = found[0]
            self.log(f"发现设备 {h['name']} {h['addr']} rssi={h['rssi']}（未锁定，按唯一设备处理）")
            return h["dev"]
        detail = "、".join(f"{h['name']}({h['addr']}, {h['rssi']}dBm)" for h in found)
        raise RuntimeError(f"发现 {len(found)} 个墨水屏但未指定目标，拒绝推送：{detail}；"
                           f"请在 config.json 填 device_address")

    async def _session(self, dev, img: Image.Image, settle: float) -> dict:
        target = dev if isinstance(dev, str) else dev
        info: dict = {
            "address": target if isinstance(target, str) else target.address,
            "name": "?" if isinstance(target, str) else target.name,
        }
        dev = target
        notes: list[bytes] = []
        reported_mtu = 20

        def on_notify(_c, data: bytearray):
            nonlocal reported_mtu
            raw = bytes(data)
            notes.append(raw)
            txt = raw.decode("utf-8", "replace")
            if txt.startswith("mtu="):
                try:
                    reported_mtu = int(txt[4:].split()[0])
                except ValueError:
                    pass

        async with BleakClient(dev, timeout=90) as cli:
            ver = await cli.read_gatt_char(CH_VER)
            info["firmware"] = f"0x{ver[0]:02x}"

            await cli.start_notify(CH_DATA, on_notify)
            await cli.write_gatt_char(CH_DATA, bytes([CMD_INIT]), response=True)
            for _ in range(20):
                if any(n.decode("utf-8", "replace").startswith("mtu=") for n in notes):
                    break
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.3)

            for n in notes:
                txt = n.decode("utf-8", "replace")
                if len(n) >= 8 and not txt.startswith(("mtu=", "t=")):
                    driver = n[7]
                    info["driver"] = f"0x{driver:02x}"
                    info["driver_name"] = DRIVERS.get(driver, ("?", "?", 0, 0))[0]
                    info["color_mode"] = DRIVERS.get(driver, ("?", "bwr", 0, 0))[1]
                    break

            try:
                info["os_mtu"] = cli.mtu_size
            except Exception:
                info["os_mtu"] = None

            # firmware reports its ATT payload; otherwise use the OS-negotiated MTU
            os_payload = (info.get("os_mtu") or 23) - 3
            payload_size = min(244, max(20, reported_mtu if reported_mtu > 20 else os_payload))
            chunk = payload_size - 2
            planes = to_planes(img)

            await cli.write_gatt_char(CH_DATA, bytes([CMD_INIT]), response=True)
            t0 = time.time()
            sent = 0
            half = len(planes) // 2
            for plane, data in (("bw", planes[:half]), ("red", planes[half:])):
                total = (len(data) + chunk - 1) // chunk
                for i in range(total):
                    frame = (0x0F if plane == "bw" else 0x00) | (0x00 if i == 0 else 0xF0)
                    pkt = bytes([CMD_WRITE_IMG, frame]) + data[i * chunk:(i + 1) * chunk]
                    with_resp = (i % 50 == 49) or (i == total - 1)
                    await cli.write_gatt_char(CH_DATA, pkt, response=with_resp)
                    sent += 1

            info["packets"] = sent
            info["tx_seconds"] = round(time.time() - t0, 2)
            info["mtu_payload"] = payload_size

            await cli.write_gatt_char(CH_DATA, bytes([CMD_REFRESH]), response=True)
            await asyncio.sleep(settle)
        return info

    async def _calendar(self, dev, mode: int = 1, settle: float = 20.0) -> dict:
        """Switch the device to its built-in calendar (mode=1) / clock (mode=2)."""
        info: dict = {
            "address": dev if isinstance(dev, str) else dev.address,
            "name": "?" if isinstance(dev, str) else dev.name,
            "mode": mode,
        }
        async with BleakClient(dev, timeout=90) as cli:
            info["firmware"] = f"0x{(await cli.read_gatt_char(CH_VER))[0]:02x}"
            await cli.write_gatt_char(CH_DATA, bytes([CMD_INIT]), response=True)
            await asyncio.sleep(0.8)
            ts = int(time.time())
            tz = int(round(-(time.timezone if not time.daylight else time.altzone) / 3600))
            payload = bytes([CMD_SET_TIME,
                             (ts >> 24) & 0xFF, (ts >> 16) & 0xFF, (ts >> 8) & 0xFF, ts & 0xFF,
                             tz & 0xFF, mode & 0xFF])
            await cli.write_gatt_char(CH_DATA, payload, response=True)
            info["sent"] = payload.hex()
            await asyncio.sleep(settle)
        return info

    async def calendar(self, mode: int = 1, settle: float = 20.0, attempts: int = 3,
                       budget: float = 300.0) -> dict:
        deadline = time.time() + budget
        if self.address:
            for i in range(attempts):
                if time.time() > deadline:
                    break
                try:
                    info = await self._calendar(self.address, mode, settle)
                    info["via"] = "direct"
                    await asyncio.sleep(5)
                    return info
                except Exception as e:
                    self.log(f"直连失败 {i + 1}/{attempts}: {type(e).__name__}: {e}")
                    await asyncio.sleep(8)
        dev = await self._find()
        if dev is None:
            raise RuntimeError("EPD tag not found (asleep or out of range?)")
        last: Exception | None = None
        for i in range(attempts):
            try:
                return await self._calendar(dev, mode, settle)
            except Exception as e:
                last = e
                self.log(f"日历模式失败 {i + 1}/{attempts}: {type(e).__name__}: {e}")
                if i + 1 < attempts:
                    await asyncio.sleep(8)
                    dev = await self._find(budget=15) or dev
        raise last  # type: ignore[misc]

    def calendar_sync(self, mode: int = 1, settle: float = 20.0) -> dict:
        return asyncio.run(self.calendar(mode, settle))

    async def push(self, img: Image.Image, settle: float = 18.0, attempts: int = 3,
                   budget: float = 300.0) -> dict:
        deadline = time.time() + budget
        if self.address:
            for i in range(attempts):
                if time.time() > deadline:
                    break
                try:
                    info = await self._session(self.address, img, settle)
                    info["via"] = "direct"
                    await asyncio.sleep(5)          # 留出链路拆除时间，避免下一次连接被拒
                    return info
                except Exception as e:
                    self.log(f"直连失败 {i + 1}/{attempts}: {type(e).__name__}: {e}")
                    await asyncio.sleep(8)
        dev = await self._find()
        if dev is None:
            raise RuntimeError("EPD tag not found (asleep or out of range?)")
        last: Exception | None = None
        for i in range(attempts):
            try:
                return await self._session(dev, img, settle)
            except Exception as e:
                last = e
                self.log(f"传输失败 {i + 1}/{attempts}: {type(e).__name__}: {e}")
                if i + 1 < attempts:
                    await asyncio.sleep(8)
                    dev = await self._find(budget=15) or dev
        raise last  # type: ignore[misc]

    def push_sync(self, img: Image.Image, settle: float = 18.0) -> dict:
        return asyncio.run(self.push(img, settle=settle))


async def _reset_radio() -> None:
    """Toggle the Bluetooth radio: clears half-open links the Windows stack keeps alive."""
    from winrt.windows.devices.radios import Radio, RadioKind, RadioState

    for r in await Radio.get_radios_async():
        if r.kind == RadioKind.BLUETOOTH:
            await r.set_state_async(RadioState.OFF)
            await asyncio.sleep(3)
            await r.set_state_async(RadioState.ON)
            await asyncio.sleep(6)
            return


def reset_radio() -> None:
    asyncio.run(_reset_radio())
