// Modelo de configuração do TFT_eSPI para o painel do GCP-Esteira.
//
// Fica em docs/ (e não na pasta do sketch) para não ser incluído por engano na
// compilação. O TFT_eSPI não lê este arquivo daqui: copie-o por cima de
//   Documentos/Arduino/libraries/TFT_eSPI/User_Setup.h
// depois de preencher a pinagem.
//
// O que já se sabe (pelo código e pela descrição do projeto):
//   - controlador ST7789;
//   - backlight no GPIO 4 (receptora.ino liga esse pino no setup);
//   - a interface é desenhada para 240 px de largura e usa até y = 232,
//     o que indica um módulo 240x240. CONFIRMAR no display.
// Pinagem conferida na montagem (fios do display -> ESP32):
//   BLK -> D4 (GPIO 4), DC -> RX2 (GPIO 16), RES -> TX2 (GPIO 17),
//   SDA -> D23 (GPIO 23), SCK -> D18 (GPIO 18), VCC -> 3V3, GND -> GND.
// Não há fio de CS na montagem: TFT_CS fica em -1.

#define USER_SETUP_INFO "GCP-Esteira painel"

#define ST7789_DRIVER
#define TFT_WIDTH  240
#define TFT_HEIGHT 240   // trocar para 320 se o módulo for 240x320

// Pinos (GPIO da ESP32). Use -1 para um pino que o módulo não tem.
#define TFT_MOSI  23  // SDA do display -> D23
#define TFT_SCLK  18  // SCK do display -> D18
#define TFT_CS    -1  // sem fio de CS na montagem
#define TFT_DC    16  // DC do display -> RX2
#define TFT_RST   17  // RES do display -> TX2
#define TFT_BL    4
#define TFT_BACKLIGHT_ON HIGH

// Fontes usadas por receptora.ino (2 e 4).
#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4

#define SPI_FREQUENCY 27000000
