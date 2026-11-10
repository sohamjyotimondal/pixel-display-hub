#include <Wire.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ST7735.h>
#include <math.h>

// =====================================
// DISPLAY CONFIGURATION
// =====================================

#define TFT_CS   5
#define TFT_DC   2
#define TFT_RST  4
#define TFT_SCLK 18
#define TFT_MOSI 23

#define W 160
#define H 128

Adafruit_ST7735 tft(TFT_CS, TFT_DC, TFT_RST);
GFXcanvas16 canvas(W, H);

// =====================================
// MPU6050
// =====================================

#define MPU_ADDR 0x68
#define SDA_PIN 21
#define SCL_PIN 22

float fx = 0, fy = 0, fz = 1;

// =====================================
// GALAXY COLORS
// =====================================

#define BLACK       0x0000
#define NAVY        0x000D
#define DEEP_PURPLE 0x200F
#define PURPLE      0x781F
#define CYAN        0x07FF
#define LIGHT_BLUE  0x5DFF
#define WHITE       0xFFFF
#define PINK        0xF81F
#define ASTEROID    0x9B6F
#define GREY        0x7BEF
#define YELLOW      0xFFE0

// =====================================
// GAME VARIABLES
// =====================================

enum GameState {
  START,
  PLAYING,
  GAME_OVER
};

GameState gameState = START;

int score = 0;
int lives = 3;

float shipX = 80;
float shipY = 109;

float tiltCenter = 0;
float tiltControl = 0;

unsigned long gameStart = 0;
unsigned long lastFrame = 0;
unsigned long lastSpawn = 0;
unsigned long lastHit = 0;

const int MAX_ASTEROIDS = 6;

struct Asteroid {
  float x;
  float y;
  float speed;
  int size;
  bool active;
};

Asteroid rocks[MAX_ASTEROIDS];

// =====================================
// STAR FIELD
// =====================================

struct Star {
  int x;
  int y;
  int speed;
  uint16_t color;
};

Star stars[22];

// =====================================
// READ MPU6050
// =====================================

void readMPU() {

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);

  if (Wire.requestFrom(MPU_ADDR, 6, true) != 6)
    return;

  int16_t rx = (Wire.read() << 8) | Wire.read();
  int16_t ry = (Wire.read() << 8) | Wire.read();
  int16_t rz = (Wire.read() << 8) | Wire.read();

  float ax = rx / 16384.0;
  float ay = ry / 16384.0;
  float az = rz / 16384.0;

  fx += (ax - fx) * 0.20;
  fy += (ay - fy) * 0.20;
  fz += (az - fz) * 0.20;
}

// =====================================
// INITIALIZE STARS
// =====================================

void initStars() {

  for (int i = 0; i < 22; i++) {

    stars[i].x = random(W);
    stars[i].y = random(H);
    stars[i].speed = random(1, 3);

    stars[i].color =
      (i % 3 == 0) ? CYAN : GREY;
  }
}

// =====================================
// DRAW STARS
// =====================================

void drawStars(bool animate) {

  for (int i = 0; i < 22; i++) {

    if (animate) {

      stars[i].y += stars[i].speed;

      if (stars[i].y >= H) {
        stars[i].y = 0;
        stars[i].x = random(W);
      }
    }

    canvas.drawPixel(
      stars[i].x,
      stars[i].y,
      stars[i].color
    );

    if (i % 6 == 0) {
      canvas.drawPixel(
        stars[i].x,
        stars[i].y + 1,
        WHITE
      );
    }
  }
}

// =====================================
// PIXEL SPACESHIP
// =====================================

void drawShip(int x, int y) {

  // Engine flame
  canvas.fillTriangle(
    x - 3, y + 7,
    x + 3, y + 7,
    x, y + 14,
    PINK
  );

  canvas.drawPixel(x, y + 10, YELLOW);

  // Wings
  canvas.fillTriangle(
    x - 5, y + 4,
    x - 11, y + 10,
    x - 4, y + 9,
    LIGHT_BLUE
  );

  canvas.fillTriangle(
    x + 5, y + 4,
    x + 11, y + 10,
    x + 4, y + 9,
    LIGHT_BLUE
  );

  // Main body
  canvas.fillTriangle(
    x, y - 9,
    x - 6, y + 7,
    x + 6, y + 7,
    WHITE
  );

  // Cockpit
  canvas.fillRect(
    x - 2, y - 1, 4, 5, CYAN
  );

  canvas.drawPixel(x, y - 5, LIGHT_BLUE);
}

// =====================================
// DRAW ASTEROID
// =====================================

void drawAsteroid(int x, int y, int s) {

  canvas.fillCircle(x, y, s, ASTEROID);

  canvas.fillCircle(
    x - s / 3, y - s / 4,
    max(1, s / 4), GREY
  );

  canvas.fillCircle(
    x + s / 3, y + s / 4,
    max(1, s / 5), DEEP_PURPLE
  );

  canvas.drawPixel(x, y - s / 2, WHITE);
}

// =====================================
// CREATE ASTEROID
// =====================================

void spawnAsteroid() {

  for (int i = 0; i < MAX_ASTEROIDS; i++) {

    if (!rocks[i].active) {

      rocks[i].x = random(8, W - 8);
      rocks[i].y = -10;

      rocks[i].speed =
        random(15, 30) / 10.0;

      rocks[i].size = random(4, 8);
      rocks[i].active = true;

      break;
    }
  }
}

// =====================================
// RESET GAME
// =====================================

void resetGame() {

  score = 0;
  lives = 3;

  shipX = 80;
  shipY = 109;

  tiltCenter = fx;

  for (int i = 0; i < MAX_ASTEROIDS; i++) {
    rocks[i].active = false;
  }

  gameStart = millis();
  lastSpawn = millis();
  lastHit = 0;

  initStars();

  gameState = PLAYING;
}

// =====================================
// UPDATE GAME
// =====================================

void updateGame() {

  // Ship movement
  tiltControl = fx - tiltCenter;

  float targetX =
    80 + tiltControl * 115.0;

  targetX = constrain(targetX, 10, 150);

  shipX += (targetX - shipX) * 0.16;

  // Progressive difficulty
  unsigned long elapsed =
    millis() - gameStart;

  int level = elapsed / 12000;

  float speedMultiplier =
    1.0 + level * 0.18;

  unsigned long spawnInterval =
    max(350, 950 - level * 65);

  // Spawn asteroids
  if (millis() - lastSpawn > spawnInterval) {

    spawnAsteroid();
    lastSpawn = millis();
  }

  // Update asteroids
  for (int i = 0; i < MAX_ASTEROIDS; i++) {

    if (!rocks[i].active)
      continue;

    rocks[i].y +=
      rocks[i].speed * speedMultiplier;

    // Collision detection
    float dx = rocks[i].x - shipX;
    float dy = rocks[i].y - shipY;

    float distance =
      sqrt(dx * dx + dy * dy);

    if (distance < rocks[i].size + 7 &&
        millis() - lastHit > 900) {

      lives--;
      lastHit = millis();

      rocks[i].active = false;

      if (lives <= 0) {
        gameState = GAME_OVER;
      }
    }

    if (rocks[i].y > H + 10) {

      rocks[i].active = false;
      score += 10;
    }
  }

  score = (millis() - gameStart) / 1000;
}

// =====================================
// HUD
// =====================================

void drawHUD() {

  canvas.setTextSize(1);
  canvas.setTextColor(WHITE);

  canvas.setCursor(5, 5);
  canvas.print("SCORE ");
  canvas.print(score);

  canvas.setCursor(111, 5);
  canvas.print("HP:");

  for (int i = 0; i < lives; i++) {

    canvas.fillCircle(
      137 + i * 7, 9, 2, PINK
    );
  }

  canvas.drawFastHLine(
    0, 16, W, DEEP_PURPLE
  );
}

// =====================================
// START SCREEN
// =====================================

void drawStartScreen() {

  canvas.fillScreen(NAVY);

  drawStars(false);

  canvas.setTextColor(CYAN);
  canvas.setTextSize(2);
  canvas.setCursor(17, 30);
  canvas.print("SPACE");

  canvas.setCursor(17, 50);
  canvas.print("DODGER");

  drawShip(80, 82);

  canvas.setTextSize(1);
  canvas.setTextColor(WHITE);

  canvas.setCursor(27, 105);
  canvas.print("TILT TO START!");

  canvas.setCursor(38, 117);
  canvas.print("AVOID ASTEROIDS");

  tft.drawRGBBitmap(
    0, 0, canvas.getBuffer(), W, H
  );
}

// =====================================
// GAME OVER SCREEN
// =====================================

void drawGameOver() {

  canvas.fillScreen(NAVY);

  drawStars(false);

  canvas.setTextSize(2);
  canvas.setTextColor(PINK);

  canvas.setCursor(18, 34);
  canvas.print("GAME OVER");

  canvas.setTextSize(1);
  canvas.setTextColor(WHITE);

  canvas.setCursor(52, 65);
  canvas.print("SCORE:");

  canvas.setCursor(74, 79);
  canvas.print(score);

  canvas.setCursor(25, 106);
  canvas.print("TILT TO RESTART");

  tft.drawRGBBitmap(
    0, 0, canvas.getBuffer(), W, H
  );
}

// =====================================
// DRAW GAME FRAME
// =====================================

void renderGame() {

  canvas.fillScreen(NAVY);

  drawStars(true);

  for (int i = 0; i < MAX_ASTEROIDS; i++) {

    if (rocks[i].active) {

      drawAsteroid(
        rocks[i].x,
        rocks[i].y,
        rocks[i].size
      );
    }
  }

  // Blink ship after collision
  if (millis() - lastHit > 900 ||
      (millis() / 120) % 2 == 0) {

    drawShip(shipX, shipY);
  }

  drawHUD();

  // Transfer complete frame
  tft.drawRGBBitmap(
    0, 0, canvas.getBuffer(), W, H
  );
}

// =====================================
// SETUP
// =====================================

void setup() {

  Serial.begin(115200);

  Wire.begin(SDA_PIN, SCL_PIN);

  // Wake MPU6050
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  byte error = Wire.endTransmission();

  delay(100);

  if (error == 0) {
    Serial.println("MPU6050 CONNECTED");
  } else {
    Serial.println("MPU6050 NOT FOUND");
  }

  // SPI
  SPI.begin(TFT_SCLK, -1, TFT_MOSI, TFT_CS);

  // TFT
  tft.initR(INITR_BLACKTAB);
  tft.setRotation(1);
  tft.fillScreen(NAVY);

  randomSeed(micros());

  initStars();

  Serial.println("SPACE DODGER READY!");
}

// =====================================
// MAIN LOOP
// =====================================

void loop() {

  unsigned long now = millis();

  // Approximately 20 FPS
  if (now - lastFrame < 50)
    return;

  lastFrame = now;

  readMPU();

  switch (gameState) {

    case START:

      drawStartScreen();

      // Tilt to start
      if (fabs(fx) > 0.30) {
        resetGame();
      }

      break;

    case PLAYING:

      updateGame();
      renderGame();

      break;

    case GAME_OVER:

      drawGameOver();

      // Tilt to restart
      if (fabs(fx - tiltCenter) > 0.35) {

        delay(250);
        resetGame();
      }

      break;
  }
}