# Pixel Display Hub — full system explanation

This project is a smart pixel clock built from a laptop, an ESP32, a display, and a mobile web app. The idea is simple:

- The ESP32 is a thin client that only reads the accelerometer and draws frames.
- The laptop does the real work: clock rendering, physics, calibration, image processing, and serving the mobile interface.
- The phone/web app lets you view the clock, upload images, create artwork, and calibrate the sensor.

Everything is designed to feel like a real physical object: when you tilt the clock, the pixels release and fall like marbles or fluid, and when you return it upright, the pixels reform into the current time or artwork.

---

## 1. Big picture

The system has 4 main parts:

1. Mobile/web frontend
   - Runs in the browser on a phone.
   - Shows the live preview.
   - Lets you control tilt, view status, upload images, and save artwork.
   - Talks to the laptop over WebSockets and HTTP.

2. Laptop server (FastAPI backend)
   - Runs `app.py` and `device.py`.
   - Hosts the web app and the API.
   - Runs the physics engine and clock rendering.
   - Sends frames to the ESP32 and to live preview clients.

3. ESP32 microcontroller
   - Runs `firmware/pixel_clock/pixel_clock.ino`.
   - Reads raw MPU6050 accelerometer data.
   - Sends that sensor data to the laptop.
   - Draws the frames the laptop sends back.

4. Physics/rendering engine
   - `sim.py` handles the falling particles and fluid behavior.
   - `faces.py` renders the clock digits and object shapes.
   - `device.py` decides when to release, reform, and show content.

---

## 2. Where the code lives

Project layout:

- `app.py` — FastAPI app, upload route, image processing pipeline, static web files
- `device.py` — engine, state machine, WebSockets, config, calibration logic, API routes
- `sim.py` — particle physics engine (marbles/fluid)
- `faces.py` — clock face drawing logic and artwork scaling
- `static/index.html` — the mobile website UI
- `firmware/pixel_clock/pixel_clock.ino` — ESP32 firmware
- `config.json` — saved runtime settings and calibration data
- `animations/` — saved animation/artwork library
- `uploads/` — uploaded images
- `test_sim.py` — physics tests

---

## 3. System flow

### A. Device side

The ESP32 does only these tasks:

1. Wake the MPU6050 accelerometer over I2C.
2. Read raw acceleration values from the sensor.
3. Send those raw numbers over WebSocket to the laptop.
4. Receive frame packets from the laptop.
5. Draw the 128x128 game-screen image onto the TFT display.

This keeps the hardware cheap and simple. The real intelligence sits on the laptop.

### B. Laptop side

The laptop:

- Accepts sensor input from the ESP32
- Computes gravity from the device orientation
- Detects when the device is tilted enough to release or reform
- Runs particle physics for all lit pixels
- Packs the result into a 128x128 1-bit frame
- Sends that frame to:
  - the ESP32 panel
  - the browser preview

### C. Browser/mobile side

The website:

- shows the live clock output
- lets the user upload photos
- creates pixel art or artwork from images
- allows tilt simulation for testing
- lets the user calibrate the sensor
- can switch between device sensor and phone motion sensor

---

## 4. Device protocol and communication

The ESP32 talks to the laptop through a single WebSocket at `/ws/device`.

### Device -> laptop messages

- `0x01` = accelerometer data
  - raw x/y/z int16 values
  - 50 Hz update
- handshake text message like:
  - `{"type":"hello","w":128,"h":128,"fw":"1.0"}`

### Laptop -> device messages

- `0x10` = frame payload
  - includes background/foreground colors and a packed 128x128 bitmap
- `0x11` = lamp control
  - 0 or 1 byte to turn the lamp on/off
- `0x12` = time sync message
  - gives the laptop time and timezone offset

The frame is a 1-bit image packed into bytes. It is a 128x128 square, centered on the TFT.

The laptop sends keepalive frames even when there are no visual changes, so the display never freezes.

If the laptop stops responding for more than 3 seconds, the ESP32 falls back to a local clock display and keeps the screen alive.

---

## 5. Engine behavior: how the clock works

The main logic lives in `device.py` inside the `Engine` class.

### Engine loop

The engine runs roughly 30 times per second.

Each tick does:

1. Read latest gravity vector from either:
   - device sensor
   - phone tilt input
   - fallback
2. Compute current tilt angle
3. Check if the clock should:
   - remain formed
   - release into free fall
   - reform back to the current picture
4. Step the particle simulation
5. Render the final bitmap
6. Send it to the ESP32 and preview clients

### State machine

The clock is always in one of a few modes:

- `FORMED` — stable display; no falling particles
- `FREE` — picture has released and is falling
- `REFORMING` — particles are settling back into the current content
- `TRANSITION` — changing between clock and artwork or between minute states

### Tilt and release

The engine continuously measures gravity in screen coordinates.

If the tilt is high enough and lasts long enough, it triggers a release:

- `release_deg` default: 35°
- `release_hold` default: 0.12 s

When the picture starts releasing, pixels break away and fall.

When the clock is back near upright for long enough, it reforms:

- `reform_deg` default: 20°
- `reform_hold` default: 0.5 s

This avoids jitter and flicker near the threshold.

---

## 6. Physics engine

The actual particle simulation is in `sim.py`.

### Why it feels like a physical object

A bitmap is treated as a set of particles. Each lit pixel becomes a small piece of matter that can:

- fall under gravity
- collide with walls
- bounce
- settle into a heap
- roll toward lower points
- reform back to target pixels

This gives the effect of a pixel “clock” behaving like marbles or a fluid.

### Two motion styles

The system supports:

- `marbles` mode
- `fluid` mode

`marbles` mode:

- each pixel behaves like a marble
- gravity acts on each particle
- particles roll and settle
- collisions create a natural-looking heap

`fluid` mode:

- uses a velocity field and FLIP-style fluid simulation
- smoother motion and liquid-like behavior

The engine also includes features like:

- random jitter for stronger motion
- damping and rest conditions
- wake/sleep logic to avoid endless CPU use
- settle detection

---

## 7. What gets rendered on the display

The display is usually showing either:

- the current clock time
- the active artwork image
- both, depending on settings

`faces.py` builds the clock bitmap. It can render:

- 12h or 24h clock layouts
- stacked or inline layout
- second bar animation
- tick/transition changes at minute boundaries

The system also supports a saved animation library in `animations/`.

---

## 8. Image processing and segmentation pipeline

This is the part that turns a photograph into a pixel-clock image.

`app.py` handles uploads and processing.

### Upload step

When a user uploads a photo:

- file is checked for image type
- file size is limited to 15 MB
- a unique filename is generated
- the image is stored in `uploads/`
- the browser gets a URL like `/uploads/<file>`

### Processing step

The API endpoint `/process` handles custom image conversion.

It uses:

- `PIL` for resize and image manipulation
- `OpenCV` for segmentation and edge processing
- `rembg` for subject/background removal

### Background removal

The app tries to isolate the main subject from the background using a model called:

- `isnet-general-use`

This creates a mask that identifies the object/person/subject in the photo.

### Segmentation logic

The code in `app.py` does the following:

1. Remove background and keep only the main subject mask.
2. Crop around the subject with a margin.
3. Resize to a working resolution.
4. Convert the result into a square grid.
5. Keep only the regions that matter.
6. Optionally extract edges for line-style output.
7. Downsample to the final pixel grid (e.g. 8x8, 16x16, 32x32, up to 128x128)

### Two main modes

The image pipeline supports different “styles”:

- `object` mode
  - tries to preserve the silhouette/shape of the subject
- `ink` mode
  - better for handwriting, sketches, drawings, and line art

The code also supports extras like:

- animation frames
- rotate
- invert
- line style
- breathing/wobble/float motion

This image data gets saved as a small grid or animation in `device.RECENT` and can later be stored into the artwork library.

---

## 9. Saving artwork and library

The app can save processed images to the device’s animation library.

- saved result goes into `animations/*.json`
- the library is exposed through `/api/animations`
- the browser can show or delete saved entries

This means you can upload a photo, convert it to a pixel-art object, save it, and then have it appear on the clock as a display mode.

---

## 10. Mobile website and browser UI

The browser app is served from `static/index.html`.

It acts like a control panel and preview screen.

### Tabs

The app has 3 main sections:

1. `Clock`
   - live preview of exact output image
   - device status
   - tilt controls
   - settings

2. `Create`
   - upload image and process it into pixel art
   - choose size/style/animation
   - save result to the library

3. `Library`
   - list saved artwork
   - render previews
   - show/delete entries

### Tilt controls

The UI lets you simulate or drive tilt without a device:

- Upright
- Tip right
- Flip over
- Lay flat
- Shake

You can also choose:

- use phone motion sensor
- use device sensor

The most recent input source wins. That means the browser can simulate motion even while the real device is connected.

---

## 11. Calibration system

The accelerometer does not always read in exactly the same axis directions depending on how the board is mounted. Calibration fixes that.

The default assumption is:

- sensor X = right
- sensor Y = down
- sensor Z = normal

But real hardware may be rotated or mirrored.

### Calibration captures

The code supports calibration poses such as:

- upright
- flat
- left
- right
- upside

The device stores a raw sensor sample for each pose.

From those, `Engine._solve_calibration()` computes the correct mapping from sensor axes to screen axes.

### Why it exists

Without calibration, the software may think left is right, or down is up, or the board may be mirrored. That leads to bad tilt behavior and a poor experience.

The app stores calibration in `config.json` under `cal`.

---

## 12. Server API endpoints

The FastAPI app exposes several routes.

### HTTP endpoints

- `GET /api/config` — read config
- `PUT /api/config` — save config changes
- `GET /api/animations` — list saved artworks
- `POST /api/animations` — add or save artwork
- `DELETE /api/animations` — remove artwork
- `POST /api/calibrate?step=...` — capture or apply calibration
- `GET /api/state` — get current engine state and timing
- `POST /upload` — upload image
- `POST /process` — create pixel art from uploaded image

### WebSocket endpoints

- `/ws/device` — connection for the ESP32
- `/ws/preview` — live preview and phone input connection

The browser preview and ESP32 both observe the same engine state.

---

## 13. Server and web segmentation

The phrase “segmentation” in this project can mean two different things:

### 1. System segmentation

The system is segmented into distinct roles:

- hardware sensor layer
- communication layer
- engine/physics layer
- UI layer
- image processing layer

Each layer has a job and communicates through simple interfaces.

### 2. Image segmentation

In the photo conversion pipeline, segmentation means separating the main object from the background. That is the process in `app.py` where the code finds the subject and removes the rest.

This is done with:

- `rembg` background masking
- OpenCV morphology and thresholding
- connected component filtering
- subject bounding box extraction

This is what makes a real-world photo turn into a clean silhouette or drawing instead of a noisy full image.

---

## 14. The most important runtime idea

The architecture is intentionally split:

- hardware side: sensor input + screen output
- server side: logic and rendering
- web side: interaction and control

This separation allows:

- the ESP32 to stay lightweight
- the laptop to handle complex physics without exhausting the microcontroller
- the browser to preview and adjust settings live
- the physics to be tested separately from the device

The whole system behaves as if the clock is a smart physical object, even though the actual computation happens on the laptop.

---

## 15. Important files and meanings

### `app.py`
Responsible for:

- photo upload
- object/background segmentation
- image-to-pixel conversion
- serving the web app
- mounting the `/uploads` folder

### `device.py`
Responsible for:

- engine state machine
- tilt detection
- calibration
- sensor fusion from device/phone
- sending frames to preview and device
- API routes

### `sim.py`
Responsible for:

- particle simulation
- marbles / fluid logic
- release and reform behavior
- occupancy logic and collisions

### `faces.py`
Responsible for:

- drawing digits and clock layouts
- render clock content into a 128x128 grid

### `static/index.html`
Responsible for:

- displaying the live view
- letting the user interact
- triggering calibration
- setting configuration

### `firmware/pixel_clock/pixel_clock.ino`
Responsible for:

- WiFi connection
- MPU6050 reading
- WebSocket handshake
- frame display
- fallback clock when laptop connection is down

---

## 16. Failure handling and resilience

The design includes a few safety mechanisms:

- If the laptop loses connection, the ESP32 displays plain time.
- If no sensor data arrives, the engine falls back to safe defaults.
- If calibration is not valid yet, the app warns or refuses invalid steps.
- If image segmentation fails, the app falls back to a simpler thresholding approach.

This makes it robust even when the network, hardware, or processing step is imperfect.

---

## 17. Practical summary of the full system

The easiest way to understand it is this:

- The phone is the control panel and preview.
- The server is the brain.
- The ESP32 is the display and sensor hardware.
- The physics makes the pixels behave like real falling matter.
- Image segmentation turns photos into clean clock art.
- Calibration makes the tilt math match the real physical mount.

So the full stack is:

Phone browser -> FastAPI server -> Physics engine -> frame output -> ESP32 display
and
ESP32 accelerometer -> FastAPI server -> gravity/tilt logic -> physics updates -> frame output

That is the whole system.

---

## 18. In one sentence

This project is a networked, sensor-driven pixel clock where a browser uploads art, a laptop runs the tilt physics and calibration, an ESP32 reads motion and shows the rendered frames, and the whole system presents itself as a magical physical object that responds to orientation.
