#include <M5Unified.h>

#define NUM_BANDS 16
#define BAR_WIDTH 16
#define BAR_GAP   3
#define START_X   10
#define SPRITE_Y  68
#define SPRITE_H  150
#define MAX_BAR_H 142
#define TIMEOUT_MS 2500

M5Canvas sprite(&M5.Display);

uint8_t bands[NUM_BANDS];
uint8_t peak[NUM_BANDS];

String currentTitle = "";
String currentArtist = "";
int progressPercent = 0;
bool isConnected = false;
unsigned long lastDataTime = 0;

// Rótulos de frequência para as 16 bandas logarítmicas
const char* freqLabels[NUM_BANDS] = {
  "40", "63", "100", "160", "250", "400", "630", "1k",
  "1.6", "2.5", "4k", "6.3", "8k", "10k", "12k", "15k"
};

void drawFrequencyLabels() {
  // Linha de base do equalizador
  M5.Display.drawFastHLine(10, 220, 300, 0x4208);

  M5.Display.setTextDatum(top_center);
  
  for (int i = 0; i < NUM_BANDS; i++) {
    int centerX = START_X + i * (BAR_WIDTH + BAR_GAP) + (BAR_WIDTH / 2);
    
    // Cores sutis por faixa de áudio (Graves -> Médios -> Agudos)
    uint16_t txtColor;
    if (i < 4)       txtColor = 0x5DDF; // Ciano / Azul (Sub & Bass)
    else if (i < 8)  txtColor = 0x7FE0; // Verde claro (Low Mids)
    else if (i < 12) txtColor = 0xFFE0; // Amarelo (High Mids / Vocais)
    else             txtColor = 0xFBE0; // Coral / Laranja (Agudos)

    M5.Display.setTextColor(txtColor, TFT_BLACK);
    M5.Display.drawString(freqLabels[i], centerX, 224, 1);
  }
}

void showWaitingScreen() {
  isConnected = false;
  memset(bands, 0, sizeof(bands));
  memset(peak, 0, sizeof(peak));
  currentTitle = "";
  currentArtist = "";
  progressPercent = 0;

  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextDatum(top_center);
  M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Display.drawString("Spotify Desk Companion", 160, 40, 4);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.drawString("Waiting for PC connection...", 160, 90, 2);
  M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK);
  M5.Display.drawString("Pioneer DJ VU Meter Mode", 160, 125, 2);
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);

  sprite.setColorDepth(8);
  sprite.createSprite(320, SPRITE_H);

  showWaitingScreen();
}

void drawUI() {
  // Limpa área superior de metadados
  M5.Display.fillRect(0, 0, 320, SPRITE_Y, TFT_BLACK);

  // Título da música
  M5.Display.setTextDatum(top_center);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  String titleDisplay = currentTitle.length() > 24 ? currentTitle.substring(0, 24) + ".." : currentTitle;
  M5.Display.drawString(titleDisplay, 160, 8, 4);

  // Artista
  M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK);
  String artistDisplay = currentArtist.length() > 32 ? currentArtist.substring(0, 32) + ".." : currentArtist;
  M5.Display.drawString(artistDisplay, 160, 34, 2);

  // Barra de progresso verde Spotify
  M5.Display.drawRoundRect(20, 53, 280, 6, 3, 0x2945);
  int fillWidth = (276 * progressPercent) / 100;
  if (fillWidth > 0) {
    M5.Display.fillRoundRect(22, 54, fillWidth, 4, 2, 0x1DB954); // Verde oficial Spotify
  }

  // Divisória superior
  M5.Display.drawFastHLine(10, 65, 300, 0x3186);

  // Rótulos de frequência na base
  drawFrequencyLabels();
}

void drawSpectrum() {
  sprite.fillScreen(TFT_BLACK);
  int baseY = SPRITE_H - 2;

  for (int i = 0; i < NUM_BANDS; i++) {
    int x = START_X + i * (BAR_WIDTH + BAR_GAP);
    int h = (bands[i] * MAX_BAR_H) / 100;
    if (h > MAX_BAR_H) h = MAX_BAR_H;

    // Queda suave dos picos
    if (h > peak[i]) {
      peak[i] = h;
    } else if (peak[i] > 2) {
      peak[i] -= 2;
    } else {
      peak[i] = 0;
    }

    // Estilo Pioneer DJ: Barras segmentadas em blocos de LED (3px altura + 1px espaço)
    for (int segY = 0; segY < h; segY += 4) {
      int pct = (segY * 100) / MAX_BAR_H;
      uint16_t segColor;

      if (pct < 55) {
        segColor = 0x07E0; // Verde Pioneer DJ (área normal)
      } else if (pct < 78) {
        segColor = 0xFFE0; // Amarelo Amber (nível alto)
      } else {
        segColor = 0xF800; // Vermelho Peak (batidas fortes / pico)
      }

      sprite.fillRect(x, baseY - segY - 3, BAR_WIDTH, 3, segColor);
    }

    // Marcador branco de pico no topo da barra
    if (peak[i] > 0) {
      sprite.drawFastHLine(x, baseY - peak[i], BAR_WIDTH, TFT_WHITE);
      sprite.drawFastHLine(x, baseY - peak[i] - 1, BAR_WIDTH, TFT_WHITE);
    }
  }

  sprite.pushSprite(0, SPRITE_Y);
}

void loop() {
  M5.update();

  if (M5.BtnA.wasPressed()) Serial.println("CMD:PREV");
  if (M5.BtnB.wasPressed()) Serial.println("CMD:PLAYPAUSE");
  if (M5.BtnC.wasPressed()) Serial.println("CMD:NEXT");

  // Watchdog de desconexão
  if (isConnected && (millis() - lastDataTime > TIMEOUT_MS)) {
    showWaitingScreen();
  }

  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    if (line == "DISCONNECT") {
      showWaitingScreen();
      return;
    }

    lastDataTime = millis();

    if (!isConnected) {
      isConnected = true;
      M5.Display.fillScreen(TFT_BLACK);
      drawUI();
    }

    if (line.startsWith("META|")) {
      int p1 = line.indexOf('|', 5);
      int p2 = line.indexOf('|', p1 + 1);
      if (p1 > 0 && p2 > 0) {
        currentTitle = line.substring(5, p1);
        currentArtist = line.substring(p1 + 1, p2);
        progressPercent = line.substring(p2 + 1).toInt();
        drawUI();
      }
    } else if (line.startsWith("FFT|")) {
      int idx = 4;
      for (int i = 0; i < NUM_BANDS; i++) {
        int nextComma = line.indexOf(',', idx);
        if (nextComma == -1) nextComma = line.length();
        bands[i] = line.substring(idx, nextComma).toInt();
        idx = nextComma + 1;
      }
      drawSpectrum();
    }
  }
}
