// Pixel Clock - ESP32 thin client
//
// Streams raw MPU6050 acceleration to the laptop and draws the 128x128 frames it sends back.
// All clock logic, artwork and physics run on the laptop (see PIXEL_CLOCK_FLOW.md).
//
// Libraries (Arduino Library Manager):
//   - "WebSockets" by Markus Sattler
//   - "Adafruit ST7735 and ST7789 Library" + "Adafruit GFX Library"   (same as asteroid_game)
//
// Wiring is identical to asteroid_game.cpp.

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>

// ============ EDIT THESE ============
const char* WIFI_SSID   = "ACT-ai_102809304192";
const char* WIFI_PASS   = "YOUR_WIFI_PASSWORD";
const char* SERVER_HOST = "192.168.0.10";   // the laptop's address on the same WiFi (ipconfig). Never the ngrok URL.
const uint16_t SERVER_PORT = 8010;
// ====================================

// Display (ST7735 160x128), same pins as the game
#define TFT_CS   5
#define TFT_DC   2
#define TFT_RST  4
#define TFT_SCLK 18
#define TFT_MOSI 23
#define SCREEN_W 160
#define SCREEN_H 128
#define GRID     128                         // the picture is a 128x128 square
#define X_OFF    ((SCREEN_W - GRID) / 2)     // centred: 16 px margin each side
#define TFT_SPI_HZ 27000000                  // drop to 16000000 if the picture shows noise

// MPU6050, same pins as the game
#define MPU_ADDR 0x68
#define SDA_PIN  21
#define SCL_PIN  22

// Lamp output for later (relay / MOSFET). HIGH = lamp on. Nothing needs to be connected yet.
#define LAMP_PIN 26

// Protocol
#define MSG_ACCEL 0x01
#define MSG_FRAME 0x10
#define MSG_LAMP  0x11
#define MSG_TIME  0x12
#define FRAME_BYTES (6 + GRID * GRID / 8)    // header + 1 bit per pixel = 2054

#define SENSOR_PERIOD_MS 20                  // 50 Hz
#define LINK_TIMEOUT_MS  3000                // no frame for this long -> show the local fallback clock

Adafruit_ST7735 tft(TFT_CS, TFT_DC, TFT_RST);
WebSocketsClient ws;

uint16_t lineBuf[GRID];
uint16_t curFg = 0xFFFF, curBg = 0x0000;
bool wsUp = false, marginsDrawn = false, mpuOk = false;
unsigned long lastFrameMs = 0, lastSensorMs = 0, lastFallbackMs = 0;

// time sync for the fallback clock
bool haveTime = false;
uint32_t syncEpoch = 0;
int16_t tzMinutes = 0;
unsigned long syncMs = 0;
int fallbackMinute = -1;

// ---------------------------------------------------------------- display

void drawFrame(const uint8_t* p, size_t len) {
  if (len < FRAME_BYTES) return;
  uint16_t fg = p[2] | (p[3] << 8);
  uint16_t bg = p[4] | (p[5] << 8);
  if (!marginsDrawn || bg != curBg) {
    tft.fillRect(0, 0, X_OFF, SCREEN_H, bg);
    tft.fillRect(X_OFF + GRID, 0, SCREEN_W - X_OFF - GRID, SCREEN_H, bg);
    marginsDrawn = true;
  }
  curFg = fg;
  curBg = bg;
  const uint8_t* bits = p + 6;
  tft.startWrite();
  tft.setAddrWindow(X_OFF, 0, GRID, GRID);
  for (int y = 0; y < GRID; y++) {
    const uint8_t* row = bits + y * (GRID / 8);
    for (int x = 0; x < GRID; x++) {
      lineBuf[x] = (row[x >> 3] & (0x80 >> (x & 7))) ? fg : bg;   // MSB first
    }
    tft.writePixels(lineBuf, GRID);
  }
  tft.endWrite();
  lastFrameMs = millis();
  fallbackMinute = -1;
}

void showMessage(const char* line1, const char* line2) {
  tft.fillScreen(ST77XX_BLACK);
  marginsDrawn = false;
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(1);
  tft.setCursor(8, 54);
  tft.print(line1);
  tft.setCursor(8, 68);
  tft.print(line2);
}

// Plain clock drawn by the ESP32 itself when the laptop link is down, so the screen never freezes.
void drawFallback() {
  if (!haveTime) {
    if (fallbackMinute != -2) {
      showMessage("Pixel Clock", wsUp ? "waiting for frames..." : "connecting...");
      fallbackMinute = -2;
    }
    return;
  }
  uint32_t local = syncEpoch + (millis() - syncMs) / 1000 + (int32_t)tzMinutes * 60;
  int minuteOfDay = (local / 60) % 1440;
  if (minuteOfDay == fallbackMinute) return;
  fallbackMinute = minuteOfDay;
  char buf[6];
  snprintf(buf, sizeof(buf), "%02d:%02d", minuteOfDay / 60, minuteOfDay % 60);
  tft.fillScreen(curBg);
  marginsDrawn = false;
  tft.setTextColor(curFg);
  tft.setTextSize(4);                        // 24x32 px per character, 5 characters = 120 px
  tft.setCursor((SCREEN_W - 120) / 2, 44);
  tft.print(buf);
}

// ---------------------------------------------------------------- sensor

bool readAccelRaw(int16_t* out) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, 6, true) != 6) return false;
  for (int i = 0; i < 3; i++) {
    out[i] = (int16_t)((Wire.read() << 8) | Wire.read());
  }
  return true;
}

void sendAccel() {
  int16_t a[3];
  if (!readAccelRaw(a)) return;
  // raw counts, little-endian. No smoothing here: the laptop filters.
  uint8_t msg[7] = { MSG_ACCEL,
                     (uint8_t)(a[0] & 0xFF), (uint8_t)(a[0] >> 8),
                     (uint8_t)(a[1] & 0xFF), (uint8_t)(a[1] >> 8),
                     (uint8_t)(a[2] & 0xFF), (uint8_t)(a[2] >> 8) };
  ws.sendBIN(msg, sizeof(msg));
}

// ---------------------------------------------------------------- link

void onWsEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      wsUp = true;
      Serial.println("WS connected");
      ws.sendTXT("{\"type\":\"hello\",\"w\":128,\"h\":128,\"fw\":\"1.0\"}");
      break;

    case WStype_DISCONNECTED:
      if (wsUp) Serial.println("WS disconnected");
      wsUp = false;
      break;

    case WStype_BIN:
      if (length == 0) break;
      if (payload[0] == MSG_FRAME) {
        drawFrame(payload, length);
      } else if (payload[0] == MSG_LAMP && length >= 2) {
        digitalWrite(LAMP_PIN, payload[1] ? HIGH : LOW);
      } else if (payload[0] == MSG_TIME && length >= 7) {
        syncEpoch = (uint32_t)payload[1] | ((uint32_t)payload[2] << 8) |
                    ((uint32_t)payload[3] << 16) | ((uint32_t)payload[4] << 24);
        tzMinutes = (int16_t)(payload[5] | (payload[6] << 8));
        syncMs = millis();
        haveTime = true;
      }
      break;

    default:
      break;
  }
}

// ---------------------------------------------------------------- setup / loop

void setup() {
  Serial.begin(115200);
  pinMode(LAMP_PIN, OUTPUT);
  digitalWrite(LAMP_PIN, HIGH);

  // MPU6050: wake it, default +-2 g range (16384 counts per g)
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(400000);
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  mpuOk = (Wire.endTransmission() == 0);
  Serial.println(mpuOk ? "MPU6050 CONNECTED" : "MPU6050 NOT FOUND");

  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);
  tft.initR(INITR_BLACKTAB);
  tft.setSPISpeed(TFT_SPI_HZ);
  tft.setRotation(1);
  showMessage("Pixel Clock", "joining WiFi...");

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                      // WiFi power save adds tens of ms of lag
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.print("\nWiFi OK, IP ");
  Serial.println(WiFi.localIP());
  showMessage("WiFi OK", mpuOk ? "finding the hub..." : "MPU6050 NOT FOUND");
  fallbackMinute = -2;

  ws.begin(SERVER_HOST, SERVER_PORT, "/ws/device");
  ws.onEvent(onWsEvent);
  ws.setReconnectInterval(1500);
  ws.enableHeartbeat(5000, 3000, 2);         // notice a dead laptop within ~10 s
}

void loop() {
  ws.loop();

  unsigned long now = millis();
  if (wsUp && mpuOk && now - lastSensorMs >= SENSOR_PERIOD_MS) {
    lastSensorMs = now;
    sendAccel();
  }

  if (now - lastFrameMs > LINK_TIMEOUT_MS && now - lastFallbackMs > 500) {
    lastFallbackMs = now;
    drawFallback();
  }
}
