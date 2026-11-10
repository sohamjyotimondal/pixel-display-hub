# Pixel Display Hub — 5 Minute Overview

This project is a smart pixel clock that reacts to tilt like a real physical object.

The screen is a 128x128 display, and instead of showing a normal clock, it shows pixels that can fall, roll, reform, and turn into artwork. When you tilt the device, the pixels break apart and behave like marbles or liquid. When you turn it back upright, they come back together into the current time or an image.

## 1. Main idea

The system has 3 main parts:

- ESP32 device
  - Reads the motion sensor
  - Sends acceleration data to the laptop
  - Draws the final frames it receives

- Laptop/server
  - Runs the main logic
  - Detects tilt and orientation
  - Runs the physics engine
  - Sends updated frames back to the display

- Phone/web app
  - Shows live preview
  - Lets users upload photos
  - Converts images into pixel art
  - Lets the user calibrate the sensor and control tilt

## 2. How it works

The ESP32 is a thin client. It does not do the smart stuff.

It only:
- reads the MPU6050 sensor,
- sends raw motion data,
- receives frames from the laptop,
- displays them on the screen.

The real intelligence is on the laptop. It takes the accelerometer readings, figures out the tilt and gravity direction, decides when the pixels should release or reform, and renders the final display image.

## 3. Why it feels alive

The clock is not just a flat image. The system treats each lit pixel as a particle.

When the clock is tilted:
- the pixels detach and fall,
- they roll, bounce, and settle,
- they can behave like marbles or fluid.

When the clock is upright again:
- the particles return to the target image,
- the time or artwork reforms into place.

So the display feels like a physical object reacting to motion.

## 4. Phone app and image workflow

The mobile app lets the user:
- view the live clock,
- upload a photo,
- convert it to pixel art,
- save it into the library,
- show artwork on the clock.

The app uses image segmentation to detect the main subject in the photo and remove the background, then converts it into a low-resolution pixel-style representation.

This means a normal photo can be turned into a custom clock image or animation.

## 5. Calibration

The sensor must be calibrated so the software knows how the board is mounted.

The user captures the sensor orientation in different poses, such as:
- upright,
- flat,
- left/right,
- upside down.

This helps the system correctly match real-world tilt to the screen orientation, so the motion feels correct.

## 6. Communication flow

The flow is:

1. ESP32 reads motion data
2. Sends it to the laptop over WebSocket
3. Laptop computes tilt and physics
4. Laptop sends frame data back
5. ESP32 displays it
6. Browser preview shows the same output live

This gives both the physical clock and the phone app a synchronized visual result.

## 7. Why this project is interesting

This project combines:
- embedded hardware,
- real-time motion sensing,
- physics simulation,
- image processing,
- web/mobile interaction,
- and display rendering.

It is a great example of a system where the microcontroller does the hardware work, while the laptop handles the “brain” of the experience.

## 8. One-line summary

This is a smart tilt-reactive pixel clock that uses an ESP32 as a display/sensor device, a laptop as the intelligence engine, and a phone app to upload images, control motion, and calibrate the system.
