// pacote.h — formato do pacote ESP-NOW do GCP-Esteira (braçadeira -> painel).
//
// ATENÇÃO: este arquivo existe em duas cópias idênticas, emissora/pacote.h e
// receptora/pacote.h, porque o Arduino só enxerga arquivos dentro da pasta do
// sketch. Ao alterar um, copie para o outro e incremente PACOTE_VERSAO.
#pragma once
#include <stdint.h>

#define PACOTE_VERSAO 2

// Valor de latencia_us quando ainda não há medida (primeiro pacote ou falha de envio).
#define LATENCIA_INVALIDA 0xFFFFFFFFu

typedef struct __attribute__((packed)) {
  uint8_t  versao;       // PACOTE_VERSAO
  uint32_t seq;          // contador de pacotes desde o boot da braçadeira
  uint32_t t_ms;         // millis() da braçadeira no momento do envio
  float    bpm;          // média dos últimos batimentos; 0 = sem leitura válida
  int32_t  irValue;      // última amostra bruta do canal IR do MAX30102
  float    ax;           // aceleração em g
  float    ay;
  float    az;
  float    spm;          // passadas por minuto; 0 = parado
  uint32_t passos;       // passadas acumuladas desde o boot
  uint32_t latencia_us;  // tempo envio->ACK do pacote anterior, em microssegundos
  uint32_t falhas_envio; // envios sem ACK acumulados desde o boot
  uint8_t  contato;      // 1 = sensor óptico em contato com a pele
} pacote_t;
