// espnow_compat.h — adapta o callback de envio do ESP-NOW às versões do core ESP32.
//
// Fica em um .h (e não no .ino) porque o gerador automático de protótipos do
// Arduino não respeita #if e quebraria a compilação em uma das versões.
#pragma once
#include <Arduino.h>
#include <esp_now.h>

// Implementada em emissora.ino.
void trataResultadoEnvio(bool ok);

#if defined(ESP_ARDUINO_VERSION) && ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 3, 0)
// Core 3.3+ (ESP-IDF 5.5): o primeiro parâmetro passou a ser wifi_tx_info_t.
static void cbEnvioEspNow(const wifi_tx_info_t *info, esp_now_send_status_t status) {
  (void)info;
  trataResultadoEnvio(status == ESP_NOW_SEND_SUCCESS);
}
#else
static void cbEnvioEspNow(const uint8_t *mac, esp_now_send_status_t status) {
  (void)mac;
  trataResultadoEnvio(status == ESP_NOW_SEND_SUCCESS);
}
#endif
