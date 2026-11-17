// espnow_compat.h — adapta o callback de recepção do ESP-NOW às versões do core ESP32.
//
// Fica em um .h (e não no .ino) porque o gerador automático de protótipos do
// Arduino não respeita #if e quebraria a compilação em uma das versões.
#pragma once
#include <Arduino.h>
#include <esp_now.h>

// Implementada em receptora.ino. rssi = 0 quando o core não informa.
void trataPacoteRecebido(const uint8_t *dados, int len, int rssi);

#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
static void cbRecebimentoEspNow(const esp_now_recv_info_t *info, const uint8_t *dados, int len) {
  int rssi = (info && info->rx_ctrl) ? info->rx_ctrl->rssi : 0;
  trataPacoteRecebido(dados, len, rssi);
}
#else
static void cbRecebimentoEspNow(const uint8_t *mac, const uint8_t *dados, int len) {
  (void)mac;
  trataPacoteRecebido(dados, len, 0);
}
#endif
