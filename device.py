"""Clock engine and device link.

Every 1/30 s: read the latest tilt, decide what the clock should show, step the particles,
and push one 128x128 1-bit frame to the ESP32 (/ws/device) and to every phone preview (/ws/preview).
The ESP32 only sends raw accelerometer counts and draws the frames it is given.
"""

import asyncio
import base64
import json
import math
import struct
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

import faces
from sim import Sim

N, FPS = 128, 30
G = 360.0  # px/s^2 for 1 g: a full-height fall takes ~0.85 s
ROOT = Path(__file__).parent
ANIMS = ROOT / "animations"
ANIMS.mkdir(exist_ok=True)
CONFIG = ROOT / "config.json"
RECENT: dict[str, dict] = (
    {}
)  # last few /process results, so the phone can save one by token

DEFAULTS = {
    "layout": "inline",  # inline (HH:MM) | stacked (HH over MM)
    "hour24": True,
    "seconds": True,
    "fg": "#ffffff",
    "bg": "#000000",
    "physics": "marbles",  # marbles | fluid
    "gravity": 1.0,  # multiplier on G
    "bounce": 0.3,
    "show": "clock",  # clock | art | both
    "art_every_min": 5,  # in "both": artwork appears every N minutes ...
    "art_for_sec": 20,  # ... for this many seconds
    "active_anim": None,
    "lamp": "auto",  # auto | on | off
    # tuning knobs: a real sensor on a real desk needs these adjusted
    "release_deg": 35,
    "reform_deg": 20,
    "flip_deg": 120,
    "release_hold": 0.12,
    "reform_hold": 0.5,
    "flip_hold": 0.8,
    # sensor -> screen axes (set by the two-pose calibration). reading . right = gx, . down = gy, . normal = gz
    "cal": {"right": [1, 0, 0], "down": [0, 1, 0], "normal": [0, 0, 1]},
    "flip_x": False,
}


def rgb565(hex_colour: str, scale: float = 1.0) -> int:
    h = hex_colour.lstrip("#")
    r, g, b = (int(int(h[i : i + 2], 16) * scale) for i in (0, 2, 4))
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def pack(frames) -> list[str]:
    return [
        base64.b64encode(np.packbits(np.asarray(f, bool)).tobytes()).decode()
        for f in frames
    ]


def unpack(b64: str, size: int) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(base64.b64decode(b64), np.uint8))[: size * size]
    return bits.reshape(size, size).astype(bool)


class Engine:
    def __init__(self):
        self.cfg = dict(DEFAULTS)
        if CONFIG.exists():
            self.cfg.update(json.loads(CONFIG.read_text()))
        self.sim = Sim(
            N, fluid=self.cfg["physics"] == "fluid", bounce=self.cfg["bounce"]
        )
        self.state = "FORMED"
        self.key = None
        self.shown = np.zeros((N, N), bool)
        self.anim_t0 = time.monotonic()
        self.g = np.array([0.0, 1.0, 0.0])  # filtered gravity, screen axes, in g
        self.tilt = 0.0
        self.dev_raw = None  # filtered raw sensor reading (for calibration)
        self.dev_t = self.virt_t = -1e9
        self.dev_g = self.virt_g = None
        self.source = "none"
        self.lamp = True
        self._over = self._under = self._flip = self._unflip = 0.0
        self._shake = False
        self._art = (None, [], 8)  # (id, frames at display size, fps)
        self._cal_samples: dict[str, np.ndarray] = {}
        self.ms = self.ms_max = 0.0
        self.peak = {
            "tick_ms": 0.0,
            "beat_ms": 0.0,
        }  # worst since the last GET /api/state

    # ---- config
    def save(self):
        CONFIG.write_text(json.dumps(self.cfg, indent=1))

    def update(self, patch: dict):
        for k, v in patch.items():
            if k in DEFAULTS:
                self.cfg[k] = v
        self.sim.set_fluid(self.cfg["physics"] == "fluid")
        self.sim.bounce = float(self.cfg["bounce"])
        self.sim.rest = self.sim.bounce * self.sim.rng.uniform(0.7, 1.3, len(self.sim))
        self.save()

    # ---- inputs
    def on_accel(self, ax: float, ay: float, az: float):
        """Raw MPU6050 reading in g, sensor axes."""
        r = np.array([ax, ay, az])
        self.dev_raw = (
            r if self.dev_raw is None else self.dev_raw + (r - self.dev_raw) * 0.2
        )
        c = self.cfg["cal"]
        gx = float(r @ np.array(c["right"])) * (-1 if self.cfg["flip_x"] else 1)
        self.dev_g = np.array(
            [gx, float(r @ np.array(c["down"])), float(r @ np.array(c["normal"]))]
        )
        self.dev_t = time.monotonic()

    def on_virtual(self, g):
        """Gravity already in screen axes (phone tilt or the preview buttons)."""
        self.virt_g = np.array(g, float)
        self.virt_t = time.monotonic()

    def calibrate(self, step: str):
        if self.dev_raw is None or time.monotonic() - self.dev_t > 1.5:
            raise HTTPException(409, "Device is not sending sensor data")
        if step in ("upright", "flat", "left", "right", "upside"):
            self._cal_samples[step] = self.dev_raw.copy()
            if step == "upright":
                return {
                    "captured": "upright",
                    "next": "capture left/right and upside, then apply calibration",
                }
            if step == "flat":
                return {
                    "captured": "flat",
                    "next": "capture upright and apply calibration for best result",
                }
            if step in ("left", "right"):
                return {
                    "captured": step,
                    "next": "capture the opposite side and upside, then apply",
                }
            return {
                "captured": "upside",
                "next": "capture upright and left/right, then apply",
            }
        if step == "apply":
            self._solve_calibration()
            return {
                "captured": "apply",
                "cal": self.cfg["cal"],
                "samples": sorted(self._cal_samples.keys()),
            }
        if step == "reset":
            self._cal_samples = {}
            return {"captured": "reset", "samples": []}
        raise HTTPException(400, "unknown calibration step")

    # ---- content
    def _load_art(self):
        aid = self.cfg["active_anim"]
        if self._art[0] != aid:
            self._art = (aid, [], 8)
            f = ANIMS / f"{aid}.json"
            if aid and f.exists():
                a = json.loads(f.read_text())
                self._art = (
                    aid,
                    [faces.scale_art(unpack(b, a["size"]), N) for b in a["frames"]],
                    a["fps"],
                )
        return self._art

    def _content(self, now: datetime):
        """(key, base bitmap). The key changes when the picture needs a transition."""
        aid, frames, _ = self._load_art()
        c = self.cfg
        art = bool(frames) and (
            c["show"] == "art"
            or (
                c["show"] == "both"
                and now.minute % max(int(c["art_every_min"]), 1) == 0
                and now.second < int(c["art_for_sec"])
            )
        )
        if art:
            return ("art", aid), frames[0]
        style = (c["layout"], c["hour24"], c["seconds"])
        return ("clock", style, now.hour, now.minute), faces.clock_bitmap(
            now.hour, now.minute, now.second, N, c["layout"], c["hour24"], c["seconds"]
        )

    def _formed_frame(self, now: datetime, base):
        """What to blit while nothing is in motion: animated artwork, or the clock plus the falling seconds marble."""
        if self.key[0] == "art":
            _, frames, fps = self._art
            return (
                frames[int((time.monotonic() - self.anim_t0) * fps) % len(frames)]
                if frames
                else base
            )
        if not self.cfg["seconds"]:
            return base
        f = now.microsecond / 1e6
        if f > 0.5 and now.second < 60:
            p = (f - 0.5) / 0.5
            bx, by = faces.bar_slot(now.second, N)
            y = int(
                -faces.BAR_H + (by + faces.BAR_H) * p * p
            )  # accelerates down, lands as the second completes
            base = base.copy()
            base[max(y, 0) : max(y + faces.BAR_H, 0), bx : bx + faces.BAR_SEG] = True
        return base

    # ---- one frame
    def tick(self, dt: float):
        t0 = time.perf_counter()
        c = self.cfg
        mono = time.monotonic()
        if mono - self.virt_t < 1.5:
            raw, self.source = self.virt_g, "phone"
        elif mono - self.dev_t < 1.5:
            raw, self.source = self.dev_g, "device"
        else:
            raw, self.source = np.array([0.0, 1.0, 0.0]), "none"
        mag = float(np.linalg.norm(raw))
        if mag > 1.7:
            self._shake = True
        base_alpha = 0.32 if self.source == "device" else 0.5
        gm_raw = float(np.linalg.norm(raw))
        gm_cur = float(np.linalg.norm(self.g))
        if gm_raw > 0.2 and gm_cur > 0.2:
            dot = max(-1.0, min(1.0, float((raw @ self.g) / (gm_raw * gm_cur))))
            delta = math.degrees(math.acos(dot))
            alpha = min(0.9, base_alpha + min(0.4, delta / 70.0 * 0.4))
        else:
            alpha = base_alpha
        self.g += (raw - self.g) * alpha
        gm = float(np.linalg.norm(self.g))
        if gm > 0.2:
            self.tilt = math.degrees(math.acos(max(-1.0, min(1.0, self.g[1] / gm))))
        gx, gy = self.g[0] * G * c["gravity"], self.g[1] * G * c["gravity"]

        self._over = self._over + dt if self.tilt > c["release_deg"] else 0.0
        self._under = self._under + dt if self.tilt < c["reform_deg"] else 0.0
        self._flip = self._flip + dt if self.tilt > c["flip_deg"] else 0.0
        self._unflip = self._unflip + dt if self.tilt < 90 else 0.0
        if self._flip > c["flip_hold"]:
            self.lamp = False
        elif self._unflip > 0.3:
            self.lamp = True
        release = self._over > c["release_hold"]

        now = datetime.now()
        key, base = self._content(now)
        sim = self.sim
        if self.state == "FORMED":
            if release:
                sim.load(self.shown)
                sim.release(gx, gy)
                self.state = "FREE"
            elif key != self.key:
                if self.key is None:
                    self.shown = base
                else:
                    sim.load(self.shown)
                    same_face = key[0] == "clock" and self.key[:2] == key[:2]
                    (sim.diff_to if same_face else sim.morph_to)(base)
                    self.state = "TRANSITION"
                self.key = key
            else:
                self.shown = self._formed_frame(now, base)
        elif self.state in ("TRANSITION", "REFORMING"):
            if release:
                sim.release(gx, gy)
                self.state = "FREE"
            elif sim.settled():
                self.state = "FORMED"
                self.anim_t0 = mono
        elif self.state == "FREE":
            if self._shake:
                sim.kick()
            if self._under > c["reform_hold"]:
                sim.reform(base, gx, gy)
                self.key = key
                self.state = "REFORMING"
        self._shake = False
        if self.state != "FORMED":
            sim.step(dt, gx, gy)
            self.shown = sim.render().astype(bool)
        took = (time.perf_counter() - t0) * 1000
        self.ms += (took - self.ms) * 0.1
        self.ms_max = max(
            self.ms_max * 0.99, took
        )  # recent worst frame, decays over a few seconds
        self.peak["tick_ms"] = max(self.peak["tick_ms"], took)
        return self.shown

    def lamp_on(self) -> bool:
        return {"on": True, "off": False}.get(self.cfg["lamp"], self.lamp)

    def frame_bytes(self, seq: int) -> bytes:
        lit = self.lamp_on()
        fg = rgb565(
            self.cfg["fg"], 1.0 if lit else 0.3
        )  # no lamp wired yet: a dimmed screen stands in for "lamp off"
        return (
            struct.pack("<BBHH", 0x10, seq & 0xFF, fg, rgb565(self.cfg["bg"]))
            + np.packbits(self.shown).tobytes()
        )

    def status(self) -> dict:
        return {
            "type": "status",
            "state": self.state,
            "tilt": round(self.tilt, 1),
            "source": self.source,
            "device": hub.device is not None,
            "lamp": self.lamp_on(),
            "ms": round(self.ms, 2),
            "ms_max": round(self.ms_max, 1),
            "particles": (
                len(self.sim) if self.state != "FORMED" else int(self.shown.sum())
            ),
            "fg": self.cfg["fg"],
            "bg": self.cfg["bg"],
        }

    def warmup(self):
        """Compile the numba kernels before the first real flip (cached on disk after the first run)."""
        bm = faces.clock_bitmap(12, 34, 30, N)
        for fluid in (False, True):
            s = Sim(N, fluid=fluid)
            s.load(bm)
            s.release(0.0, G)
            for _ in range(8):
                s.step(1 / FPS, 0.0, -G)
            s.kick()
            s.reform(bm, 0.0, G)
            s.step(1 / FPS, 0.0, G)
            s.render()
            s.set_fluid(not fluid)
            s.step(1 / FPS, 0.0, G)
        s = Sim(N)
        s.load(bm)
        s.diff_to(faces.clock_bitmap(12, 35, 0, N))
        s.step(1 / FPS, 0.0, G)

    def _solve_calibration(self):
        samples = self._cal_samples
        up = samples.get("upright")
        if up is None:
            raise HTTPException(409, "Capture upright first")

        down = up.astype(float)
        upside = samples.get("upside")
        if upside is not None:
            down = down - upside
        if np.linalg.norm(down) < 0.35:
            raise HTTPException(
                409, "Upright/upside readings are too close; recapture those poses"
            )
        down /= np.linalg.norm(down)

        right_candidates = []
        if "right" in samples:
            right_candidates.append(samples["right"].astype(float))
        if "left" in samples:
            right_candidates.append(-samples["left"].astype(float))

        if right_candidates:
            right = np.mean(np.vstack(right_candidates), axis=0)
            right = right - (right @ down) * down
        else:
            flat = samples.get("flat")
            if flat is None:
                raise HTTPException(
                    409, "Capture right or left (or flat) before applying calibration"
                )
            flat = flat.astype(float) - (flat @ down) * down
            right = np.cross(flat, down)

        if np.linalg.norm(right) < 0.25:
            raise HTTPException(
                409,
                "Left/right pose is not distinct enough from upright; recapture and retry",
            )
        right /= np.linalg.norm(right)

        normal = np.cross(right, down)
        normal /= np.linalg.norm(normal)

        self.update(
            {
                "cal": {
                    "right": right.tolist(),
                    "down": down.tolist(),
                    "normal": normal.tolist(),
                }
            }
        )


class Conn:
    """One socket. Its pump always sends the newest frame and drops any it was too slow for."""

    def __init__(self, ws: WebSocket):
        self.ws, self.wake, self.extra = ws, asyncio.Event(), []

    def send(self, msg):
        self.extra.append(msg)
        self.wake.set()

    async def pump(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            extra, self.extra = self.extra, []
            for m in extra:
                await (
                    self.ws.send_text(m)
                    if isinstance(m, str)
                    else self.ws.send_bytes(m)
                )
            if hub.frame:
                await self.ws.send_bytes(hub.frame)


class Hub:
    def __init__(self):
        self.device: Conn | None = None
        self.previews: set[Conn] = set()
        self.frame = b""
        self.hello = {}


engine = Engine()
hub = Hub()
router = APIRouter()


def time_msg() -> bytes:
    now = datetime.now().astimezone()
    return struct.pack(
        "<BIh", 0x12, int(now.timestamp()), int(now.utcoffset().total_seconds() // 60)
    )


def _pacer(loop, beat: asyncio.Event):
    """30 Hz metronome on a thread: asyncio's own timer on Windows is only good to ~16 ms."""
    nxt = time.perf_counter()
    while True:
        nxt += 1 / FPS
        d = nxt - time.perf_counter()
        if d < -0.2:
            nxt = time.perf_counter()
        elif d > 0:
            time.sleep(d)
        try:
            loop.call_soon_threadsafe(beat.set)
        except RuntimeError:  # loop closed: server is shutting down
            return


async def run_loop():
    await asyncio.to_thread(engine.warmup)
    seq, last_sent, last_bits, last_status, last_lamp, last_minute = (
        0,
        0.0,
        None,
        0.0,
        None,
        -1,
    )
    beat = asyncio.Event()
    threading.Thread(
        target=_pacer, args=(asyncio.get_running_loop(), beat), daemon=True
    ).start()
    prev = time.perf_counter()
    while True:
        await beat.wait()
        beat.clear()
        t = time.perf_counter()
        engine.peak["beat_ms"] = max(engine.peak["beat_ms"], (t - prev) * 1000)
        prev = t
        try:
            bits = engine.tick(1 / FPS)
        except Exception as e:  # never let one bad frame kill the clock
            print("tick error:", repr(e))
            engine.state, engine.key = "FORMED", None
            bits = engine.shown
        now = time.perf_counter()
        lamp = engine.lamp_on()
        changed = (
            last_bits is None
            or lamp != last_lamp
            or not np.array_equal(bits, last_bits)
        )
        if (
            changed or now - last_sent > 0.5
        ):  # static picture: 2 keepalive frames a second is enough
            seq += 1
            hub.frame = engine.frame_bytes(seq)
            last_bits, last_sent = bits.copy(), now
            for c in ([hub.device] if hub.device else []) + list(hub.previews):
                c.wake.set()
        if hub.device and (lamp != last_lamp or datetime.now().minute != last_minute):
            hub.device.send(struct.pack("<BB", 0x11, int(lamp)))
            hub.device.send(time_msg())
        last_lamp, last_minute = lamp, datetime.now().minute
        if now - last_status > 0.25:
            last_status = now
            st = json.dumps(engine.status())
            for c in hub.previews:
                c.send(st)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(run_loop())
    yield
    task.cancel()


async def _serve(ws: WebSocket, conn: Conn, on_message):
    pump = asyncio.create_task(conn.pump())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            on_message(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        pump.cancel()


@router.websocket("/ws/device")
async def ws_device(ws: WebSocket):
    await ws.accept()
    conn = Conn(ws)
    hub.device = conn
    conn.send(time_msg())
    conn.send(struct.pack("<BB", 0x11, int(engine.lamp_on())))

    def on_message(msg):
        b = msg.get("bytes")
        if b and b[0] == 0x01 and len(b) >= 7:
            ax, ay, az = struct.unpack("<hhh", b[1:7])
            engine.on_accel(
                ax / 16384.0, ay / 16384.0, az / 16384.0
            )  # MPU6050 at +-2 g
        elif msg.get("text"):
            try:
                hub.hello = json.loads(msg["text"])
            except ValueError:
                pass

    try:
        await _serve(ws, conn, on_message)
    finally:
        if hub.device is conn:
            hub.device = None


@router.websocket("/ws/preview")
async def ws_preview(ws: WebSocket):
    await ws.accept()
    conn = Conn(ws)
    hub.previews.add(conn)
    conn.send(json.dumps(engine.status()))

    def on_message(msg):
        try:
            m = json.loads(msg.get("text") or "{}")
        except ValueError:
            return
        if isinstance(m.get("g"), list) and len(m["g"]) == 3:
            engine.on_virtual(m["g"])
        if m.get("shake"):
            engine._shake = True

    try:
        await _serve(ws, conn, on_message)
    finally:
        hub.previews.discard(conn)


@router.get("/api/config")
async def get_config():
    return engine.cfg


@router.put("/api/config")
async def put_config(patch: dict = Body(...)):
    engine.update(patch)
    return engine.cfg


@router.get("/api/state")
async def get_state():
    peak, engine.peak = engine.peak, {"tick_ms": 0.0, "beat_ms": 0.0}
    return engine.status() | {
        "hello": hub.hello,
        "previews": len(hub.previews),
        "peak": {k: round(v, 1) for k, v in peak.items()},
    }


@router.post("/api/calibrate")
async def calibrate(step: str):
    if step not in ("upright", "flat", "left", "right", "upside", "apply", "reset"):
        raise HTTPException(
            400, "step must be upright|flat|left|right|upside|apply|reset"
        )
    return engine.calibrate(step)


@router.get("/api/animations")
async def list_animations():
    out = []
    for f in sorted(
        ANIMS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        a = json.loads(f.read_text())
        out.append(
            {
                "id": a["id"],
                "name": a["name"],
                "size": a["size"],
                "fps": a["fps"],
                "count": len(a["frames"]),
                "thumb": a["frames"][0],
            }
        )
    return out


@router.post("/api/animations")
async def save_animation(token: str, name: str = "Untitled"):
    r = RECENT.get(token)
    if not r:
        raise HTTPException(404, "Process the photo again, then save")
    aid = uuid.uuid4().hex[:10]
    (ANIMS / f"{aid}.json").write_text(
        json.dumps(
            {
                "id": aid,
                "name": name[:40] or "Untitled",
                "size": r["size"],
                "fps": r["fps"],
                "frames": pack(r["frames"]),
            }
        )
    )
    return {"id": aid}


@router.delete("/api/animations/{aid}")
async def delete_animation(aid: str):
    f = ANIMS / f"{Path(aid).name}.json"
    if not f.exists():
        raise HTTPException(404, "Not found")
    f.unlink()
    if engine.cfg["active_anim"] == aid:
        engine.update({"active_anim": None, "show": "clock"})
    return {"deleted": aid}
