"""Stand-in for the ESP32: speaks the same /ws/device protocol so the whole flow can be tested without hardware.

    python fake_device.py                 # upright 2 s, flip over, hold, turn back; saves fake_device.gif
    python fake_device.py --host 192.168.0.10 --hold 6
"""
import argparse
import asyncio
import math
import struct
import time

import numpy as np
import websockets
from PIL import Image


def pose(t, flip_at, hold, dur=0.45):
    """Rotation of the clock in its own plane, degrees: 0 upright, 180 upside down."""
    if t < flip_at:
        return 0.0
    if t < flip_at + dur:
        return (t - flip_at) / dur * 180
    if t < flip_at + dur + hold:
        return 180.0
    return max(180 - (t - flip_at - dur - hold) / dur * 180, 0.0)


async def main(a):
    frames, msgs, t0 = [], {"lamp": [], "time": 0}, time.perf_counter()
    async with websockets.connect(f"ws://{a.host}:{a.port}/ws/device", max_size=None) as ws:
        await ws.send('{"type":"hello","w":128,"h":128,"fw":"fake"}')

        async def sensor():
            while True:
                deg = pose(time.perf_counter() - t0, a.flip_at, a.hold)
                r = math.radians(deg)
                # default calibration: sensor x = screen right, sensor y = screen down
                ax, ay, az = math.sin(r), math.cos(r), 0.0
                await ws.send(struct.pack("<Bhhh", 0x01, *(int(v * 16384) for v in (ax, ay, az))))
                await asyncio.sleep(0.02)

        task = asyncio.create_task(sensor())
        try:
            while time.perf_counter() - t0 < a.total:
                m = await asyncio.wait_for(ws.recv(), 3)
                if isinstance(m, bytes) and m[0] == 0x10:
                    _, seq, fg, bg = struct.unpack("<BBHH", m[:6])
                    bits = np.unpackbits(np.frombuffer(m[6:], np.uint8)).reshape(128, 128)
                    frames.append((time.perf_counter() - t0, bits, fg))
                elif isinstance(m, bytes) and m[0] == 0x11:
                    msgs["lamp"].append((round(time.perf_counter() - t0, 2), m[1]))
                elif isinstance(m, bytes) and m[0] == 0x12:
                    msgs["time"] += 1
        finally:
            task.cancel()
    ts = np.array([f[0] for f in frames])
    moving = ts[(ts > a.flip_at) & (ts < a.flip_at + 1.5)]
    print(f"frames {len(frames)}, frame bytes {6 + 2048}, time msgs {msgs['time']}, lamp msgs {msgs['lamp']}")
    if len(moving) > 2:
        gaps = np.diff(moving) * 1000
        print(f"during the fall: {len(moving) / 1.5:.1f} fps, gap mean {gaps.mean():.1f} ms, max {gaps.max():.1f} ms")
    print("fg colour values seen:", sorted({hex(f[2]) for f in frames}))
    gif = [Image.fromarray((f[1] * 255).astype(np.uint8)).resize((256, 256), Image.NEAREST) for f in frames]
    gif[0].save(a.out, save_all=True, append_images=gif[1:], duration=33, loop=0)
    return frames


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8010)
    p.add_argument("--flip-at", type=float, default=2.0)
    p.add_argument("--hold", type=float, default=3.5)
    p.add_argument("--total", type=float, default=9.0)
    p.add_argument("--out", default="fake_device.gif")
    asyncio.run(main(p.parse_args()))
