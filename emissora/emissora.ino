#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <Wire.h>
#include <MAX30105.h>
#include <heartRate.h>

// Endereço MAC da ESP32 Receptora
uint8_t receiverAddress[] = {0xE0, 0x5A, 0x1B, 0x4A, 0xA6, 0x18};

// Estrutura de dados transmitida via ESP-NOW
typedef struct struct_message {
  float heartBPM;
  int32_t irValue;
  float ax;
  float ay;
  float az;
} struct_message;

struct_message sensorData;
esp_now_peer_info_t peerInfo;

MAX30105 particleSensor;
uint8_t MMA_ADDR = 0x1C; // Será detectado no setup() (0x1C ou 0x1D)

long lastBeat = 0;
float beatsPerMinute = 0.0;

// -------------------------------------------------------------------
// FUNÇÕES DE LEITURA DIRETA REGISTRADORES MMA8452Q (SEM BIBLIOTECA)
// -------------------------------------------------------------------
void writeRegisterMMA(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MMA_ADDR);
  Wire.write(reg);
  Wire.write(value);
  Wire.endTransmission();
}

void readRegistersMMA(uint8_t reg, uint8_t count, uint8_t *dest) {
  Wire.beginTransmission(MMA_ADDR);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)MMA_ADDR, count);
  for (uint8_t i = 0; i < count; i++) {
    if (Wire.available()) {
      dest[i] = Wire.read();
    }
  }
}

bool inicializarMMA_Direto() {
  uint8_t whoAmI = 0;

  // Tenta endereço 0x1C primeiro
  MMA_ADDR = 0x1C;
  readRegistersMMA(0x0D, 1, &whoAmI);

  // Se falhar em 0x1C, tenta 0x1D
  if (whoAmI == 0x00 || whoAmI == 0xFF) {
    MMA_ADDR = 0x1D;
    readRegistersMMA(0x0D, 1, &whoAmI);
  }

  // WHO_AM_I válido do MMA8452Q costuma ser 0x2A
  if (whoAmI != 0x00 && whoAmI != 0xFF) {
    writeRegisterMMA(0x2A, 0x00); // Entra em Standby para configurar
    writeRegisterMMA(0x0E, 0x00); // Define escala +/- 2g
    writeRegisterMMA(0x2A, 0x01); // Coloca em modo Active
    Serial.printf("✅ MMA8452Q encontrado no endereco 0x%02X! (WHO_AM_I: 0x%02X)\n", MMA_ADDR, whoAmI);
    return true;
  }

  Serial.println("❌ MMA8452Q nao respondeu em 0x1C nem 0x1D via I2C Direto!");
  return false;
}

void lerAcelerometroMMA(float &ax, float &ay, float &az) {
  uint8_t rawData[6];
  readRegistersMMA(0x01, 6, rawData);

  int16_t x = ((int16_t)(rawData[0] << 8 | rawData[1])) >> 2;
  int16_t y = ((int16_t)(rawData[2] << 8 | rawData[3])) >> 2;
  int16_t z = ((int16_t)(rawData[4] << 8 | rawData[5])) >> 2;

  // Converte para g (Gravidade)
  ax = (float)x / 1024.0;
  ay = (float)y / 1024.0;
  az = (float)z / 1024.0;
}

// -------------------------------------------------------------------
// CALLBACK ESP-NOW
// -------------------------------------------------------------------
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
void OnDataSent(const wifi_tx_info_t *info, esp_now_send_status_t status) {
  Serial.print("Status ESP-NOW: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Sucesso" : "Falha");
}
#else
void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  Serial.print("Status ESP-NOW: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Sucesso" : "Falha");
}
#endif

void setup() {
  Serial.begin(115200);

  // Inicializa I2C nos pinos padrão (SDA = 21, SCL = 22)
  Wire.begin(21, 22);

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != ESP_OK) {
    Serial.println("Erro ESP-NOW");
    return;
  }

  esp_now_register_send_cb(OnDataSent);

  memset(&peerInfo, 0, sizeof(peerInfo));
  memcpy(peerInfo.peer_addr, receiverAddress, 6);
  peerInfo.channel = 1;
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Falha Peer ESP-NOW");
    return;
  }

  // Inicializa MAX30102
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("❌ MAX30102 nao encontrado!");
  } else {
    particleSensor.setup();
    particleSensor.setPulseAmplitudeRed(0x0A);
    particleSensor.setPulseAmplitudeGreen(0x00);
    Serial.println("✅ MAX30102 inicializado!");
  }

  // Inicializa MMA8452Q Direto
  inicializarMMA_Direto();
}

void loop() {
  // 1. MAX30102
  long irValue = particleSensor.getIR();

  if (checkForBeat(irValue)) {
    long delta = millis() - lastBeat;
    lastBeat = millis();
    beatsPerMinute = 60.0 / (delta / 1000.0);
  }

  sensorData.irValue = irValue;
  sensorData.heartBPM = beatsPerMinute;

  // 2. Leitura direta dos eixos
  lerAcelerometroMMA(sensorData.ax, sensorData.ay, sensorData.az);

  // 3. Envio ESP-NOW a cada 100 ms
  static unsigned long lastSendTime = 0;
  if (millis() - lastSendTime >= 100) { 
    lastSendTime = millis();
    Serial.printf("EMISSORA -> AX: %.2f | AY: %.2f | AZ: %.2f | BPM: %.1f\n", 
                  sensorData.ax, sensorData.ay, sensorData.az, sensorData.heartBPM);
    esp_now_send(receiverAddress, (uint8_t *) &sensorData, sizeof(sensorData));
  }

  delay(20);
}