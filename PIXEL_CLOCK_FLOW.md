# Pixel Clock: full flow

A tilt-aware pixel clock on an ESP32 with a colour TFT. It shows the time and artwork made from phone
photos. Turn it over and the pixels let go and fall like marbles, or slosh like liquid. Turn it back and
they fly home into the current time. A lamp output switches off when it is flipped.

Internal build notes. They describe how the laptop does the work behind the device.

## 1. The trick

The ESP32 is a thin client. It does two things only:

1. Reads the MPU6050 and streams raw acceleration to the laptop, 50 times a second.
2. Draws the finished 128×128 frames the laptop sends back, 30 times a second.

The laptop holds the clock, the artwork library, the physics and the rendering. Because tilt goes up and
frames come down continuously, the device reacts at once and looks self-contained.

```
 Phone (browser)                 Laptop (FastAPI, port 8010)                 ESP32
 ────────────────                ───────────────────────────                 ─────────────────
 take photo  ───── upload ─────▶ pixel-art pipeline       (app.py)
 save        ───── token ──────▶ artwork library          (animations/*.json)
 settings    ───── PUT ────────▶ config                   (config.json)
                                        │
                                        ▼
 live preview ◀── /ws/preview ── engine, 30 fps (device.py) ── /ws/device ─▶ draw frame on TFT
 tilt buttons ───────────────▶    clock face   (faces.py)
 or phone motion                  particles    (sim.py)   ◀── accel 50 Hz ── MPU6050
```

The phone reaches the laptop through ngrok. The ESP32 must use the laptop's LAN address, never ngrok:
a round trip through the internet would add visible lag.

## 2. Hardware, as wired in `asteroid_game.cpp`

| Part | Detail |
|---|---|
| Display | ST7735 TFT 160×128, `INITR_BLACKTAB`, rotation 1. CS 5, DC 2, RST 4, SCLK 18, MOSI 23 |
| Sensor | MPU6050 at I2C `0x68`. SDA 21, SCL 22. Woken via `0x6B`, accel at `0x3B`, 16384 counts per g |
| Lamp | GPIO 26, HIGH means on. Proposed pin, nothing needs to be connected yet |

The picture is a 128×128 square, one TFT pixel per cell, centred with a 16 px margin each side.

## 3. Run it

**Laptop**

```
cd C:\nwork\pixel-display-hub
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8010
ngrok http 8010
```

The first start compiles the physics once, about 20 seconds. Later starts are instant.

**ESP32**

1. Arduino IDE, Library Manager: install "WebSockets" by Markus Sattler. The Adafruit GFX and ST7735
   libraries are the ones the game already uses.
2. Open `firmware/pixel_clock/pixel_clock.ino`. Set `WIFI_PASS`. Check `WIFI_SSID` and `SERVER_HOST`.
   `SERVER_HOST` is the laptop's IPv4 address from `ipconfig`, currently `192.168.0.10`.
3. Flash. The screen shows "joining WiFi", then "finding the hub", then the clock.
4. In the phone app, Clock tab: the dot turns green. Run the two-step calibration once (section 6).

**No board to hand?** `python fake_device.py` speaks the same protocol as the ESP32 and performs a flip.
The Clock tab's tilt buttons, or "Tilt with this phone", drive the physics the same way.

## 4. Device protocol: one WebSocket, `/ws/device`

Binary messages, first byte is the type, integers little-endian.

| Direction | Type | Payload | Rate |
|---|---|---|---|
| device to laptop | `0x01` accel | ax, ay, az raw int16. 7 bytes in all | 50 Hz |
| device to laptop | text | `{"type":"hello","w":128,"h":128,"fw":"1.0"}` | on connect |
| laptop to device | `0x10` frame | seq u8, fg RGB565 u16, bg RGB565 u16, then 2048 bytes: 1 bit per pixel, row by row, MSB first | up to 30 fps |
| laptop to device | `0x11` lamp | 1 byte, 0 or 1 | on change |
| laptop to device | `0x12` time | unix seconds u32, UTC offset in minutes i16 | on connect and each minute |

A frame is 2054 bytes, about 60 kB/s at full rate. When the picture is not changing the laptop sends
only two keepalive frames a second.

The device pushes each frame as one 128×128 block over SPI, about 10 ms at 27 MHz.

**If the laptop drops.** With no frame for 3 seconds the ESP32 draws a plain `HH:MM` itself from the last
time message and its own `millis()`. The screen never freezes on a dead link.

## 5. The engine: what happens each 1/30 s

```
latest tilt  → gravity in screen axes
             → orientation state
content      → clock digits and seconds bar, or the saved artwork
particles    → stepped by the physics when anything is in motion
frame        → packed to 2048 bytes, sent to the device and every phone preview
```

While nothing moves, the engine simply draws the content. The moment something must move, every lit
pixel becomes a particle. Falling, reforming and changing picture are the same particles under
different rules, which is why every transition is seamless.

### Orientation states

`tilt` is the angle between gravity and upright.

```
            tilt > 35° for 0.12 s
   FORMED ───────────────────────▶ FREE ◀──────────── tilt > 35° again ───┐
     ▲  ▲                           │                                     │
     │  └── picture changed ──▶ TRANSITION            tilt < 20° for 0.5 s│
     │                              │                   ▼                 │
     └────── all particles home ────┴────────────── REFORMING ────────────┘
```

Different thresholds out and back stop it chattering at the edge. Every state can be interrupted: tip
it mid-reform and the particles in flight simply fall again.

**Lamp.** Tilt above 120° for 0.8 s turns the lamp off. Back under 90° turns it on. The laptop sends the
lamp message and the ESP32 sets GPIO 26. Until a lamp is wired, the display dims to 30 percent so the
state is visible.

### Release

- Pixels let go in a wave over 0.28 s, the edge that is heading down first, so the clock crumbles.
- Each gets a small random push and its own bounciness, so no two move in step.
- Lying flat there is no downhill, so the picture bursts gently outward from the centre.

### Marbles

Grain physics on an occupancy grid, one marble per pixel, so the picture never smears.

| Rule | Value |
|---|---|
| Gravity | 1 g = 360 px/s². A full-height fall takes about 0.85 s |
| Step | 8 substeps per frame, speed capped under 1 px per substep so nothing tunnels |
| Update order | lowest along gravity first, so columns fall without gaps |
| Blocked | roll off diagonally, never uphill. Otherwise bounce |
| Bounce | lively off walls, damped marble on marble so a falling clump stays together |
| Levelling | a resting marble rolls toward any drop within 5 cells, so heaps flatten like marbles, not sand |
| Sleep | 12 blocked substeps and a marble stops dead. A resting heap is perfectly still |
| Wake | gravity changes by 7 percent, or the cell beneath empties |
| Shake | a reading above 1.7 g kicks every free marble |

### Fluid

Same particles, FLIP solver after Matthias Müller's Ten Minute Physics. 64×64 velocity grid over the
128 px square, 90 percent FLIP and 10 percent PIC, 30 pressure passes, particle separation and density
drift correction. A calm pool is frozen, so it neither shimmers nor uses CPU.

### Reform

1. Targets are the pixels of the content as of now. The time may have changed while it lay flipped.
2. Particles are matched to targets strip by strip: same left-to-right order, then top-to-bottom inside
   each strip. Paths come out short and uncrossed, and it costs a sort, not an optimisation.
3. Unequal counts: surplus particles merge into an already-claimed pixel, missing ones split off.
4. Each rides a spring home, 15 rad/s at 0.72 damping, for a slight overshoot. The heap unzips from the top.

### Picture changes while upright

- **Minute tick.** Only pixels that differ move. Old ones drain out the bottom, new ones drop in from the
  top and land with one small bounce.
- **Seconds.** A row of 60 marbles along the bottom. Each second one falls in from the top and lands as
  the second completes. At the minute they all drain away.
- **Clock to artwork and back.** A particle morph using the reform matching.

## 6. Phone app

One page, three tabs.

**Clock.** Live preview of the exact frames the device gets, device status, tilt controls, and settings:
what to show, layout, 12 or 24 hour, seconds marbles, colours, marbles or fluid, gravity, bounce, lamp
behaviour.

**Create.** Photo to pixel art as before, now up to 128 px. "Save and show on clock" stores it in the
library and puts it on the display.

**Library.** Saved artwork with Show and Delete.

**Tilt controls.** Upright, Tip right, Flip over, Lay flat and Shake stand in for the sensor. "Tilt with
this phone" feeds the phone's own motion sensor in, so turning the phone over flips the clock. "Use
device sensor" hands control back to the ESP32. Whichever source sent most recently wins.

**Calibration, once per build.** The sensor's axes depend on how the board is mounted.

1. Stand the clock upright, tap "1. Standing upright".
2. Lay it flat, screen up, tap "2. Lying flat, screen up".

The two readings define screen-down and the screen normal and are saved in `config.json`. If left and
right come out swapped, tick "Mirror left and right". Until calibrated the engine assumes sensor X is
screen-right and sensor Y is screen-down.

| Endpoint | Purpose |
|---|---|
| `GET / PUT /api/config` | every setting, including the tilt thresholds |
| `GET / POST / DELETE /api/animations` | artwork library. Saving takes the token returned by `/process` |
| `POST /api/calibrate?step=upright\|left\|right\|upside\|flat\|apply\|reset` | capture/apply/reset calibration |
| `GET /api/state` | state, tilt, device online, lamp, frame timing peaks |
| `WS /ws/preview` | frames and status out, tilt and shake in |
| `WS /ws/device` | the ESP32 |

## 7. Files

```
app.py                 photo upload and pixel-art pipeline, mounts everything
device.py              engine, state machine, both WebSockets, config and library API
sim.py                 particles: marbles, fluid, release, reform, morph, minute tick
faces.py               digits, seconds bar, artwork scaling
test_sim.py            physics checks:  python test_sim.py
fake_device.py         simulated ESP32 for testing without hardware
static/index.html      the phone app
firmware/pixel_clock/pixel_clock.ino
animations/, config.json   saved state
```

## 8. Tuning knobs

All in `config.json`, editable through `PUT /api/config`.

| Key | Default | Effect |
|---|---|---|
| `release_deg`, `release_hold` | 35, 0.12 | how far and how long to tilt before the picture lets go |
| `reform_deg`, `reform_hold` | 20, 0.5 | how upright and how steady before it reforms |
| `flip_deg`, `flip_hold` | 120, 0.8 | what counts as flipped for the lamp |
| `gravity`, `bounce` | 1.0, 0.3 | feel of the fall |

In the firmware: `TFT_SPI_HZ` if the picture shows noise, `SENSOR_PERIOD_MS`, `LINK_TIMEOUT_MS`.

## 9. What has and has not been tested

**Tested on the laptop**

- Physics checks pass: marbles and fluid flip and reform into the exact target, a resting heap does not
  shimmer, no two marbles share a pixel, an interrupted reform recovers, minute tick and morphs land exactly.
- Full flow through the real device socket using the simulated ESP32: clock, flip, fall, lamp off, turn
  back, reform, lamp on. Steady 30 fps.
- Frame cost: under 1 ms idle, about 3 ms while falling, about 6 ms worst with 4,300 particles.
- Phone-side tilt through the preview socket, and photo to 128 px artwork to clock and back.

**Not tested**

- The firmware has not been compiled or flashed. There is no Arduino toolchain on this laptop.
- The ESP32 reaching the laptop over WiFi. The Windows firewall already allows inbound Python on the
  current network profile, which is a good sign, not proof.
- The page in a real phone browser, including the motion-sensor permission prompt on iOS.
- Sensor axis directions on the real mount. That is what calibration is for.
