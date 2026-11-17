// GCP-Esteira — Unidade vestível (braçadeira) — Entrega V1
//
// Lê o MAX30102 (PPG -> BPM) e o acelerômetro MMA8452Q (passadas -> SPM),
// envia os valores ao painel por ESP-NOW a 10 Hz e, com o USB conectado,
// imprime o sinal bruto a 50 Hz em linhas "CSV,E,..." para o
// tools/serial_logger.py.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <Wire.h>
#include <MAX30105.h>
#include <heartRate.h>
#include "pacote.h"
#include "espnow_compat.h"
#include <esp_mac.h>

// MAC do rádio Wi-Fi lido direto da eFuse. WiFi.macAddress() pode devolver zeros
// antes do Wi-Fi iniciar por completo.
String macDesta() {
  uint8_t mac[6];
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char texto[18];
  snprintf(texto, sizeof(texto), "%02X:%02X:%02X:%02X:%02X:%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(texto);
}

// -------------------------------------------------------------------
// CONFIGURAÇÃO
// -------------------------------------------------------------------
// MAC da ESP32 do painel. A receptora imprime o próprio MAC na serial ao ligar:
// confira e ajuste aqui se for diferente.
uint8_t MAC_RECEPTORA[6] = {0xE0, 0x5A, 0x1B, 0x4A, 0xA6, 0x18};

const uint8_t  PINO_SDA = 21;
const uint8_t  PINO_SCL = 22;

const uint32_t PERIODO_ACEL_MS  = 20;   // amostragem do acelerômetro: 50 Hz
const uint32_t PERIODO_ENVIO_MS = 100;  // envio ESP-NOW: 10 Hz
const bool     SAIDA_CSV        = true; // sinal bruto na serial (modo bancada)

// Detecção de passada. Valores iniciais: calibrar com os dados do teste T2.
const float    LIMIAR_PASSO_G        = 0.15f; // pico acima da gravidade, em g
const uint32_t REFRATARIO_PASSO_MS   = 250;   // limita a 240 passadas/min
const uint32_t INTERVALO_MAX_PASSO_MS = 2000; // acima disso não é passada seguida
const uint32_t TIMEOUT_PASSO_MS      = 3000;  // sem passada -> SPM = 0
// Se o teste T2 mostrar um pico por ciclo do braço (metade da cadência real),
// troque para 2.
const uint8_t  PASSOS_POR_PICO       = 1;

// Frequência cardíaca.
const int32_t  LIMIAR_CONTATO_IR    = 50000; // IR abaixo disso = sem contato com a pele
const float    BPM_MIN              = 40.0f;
const float    BPM_MAX              = 220.0f;
const uint32_t TIMEOUT_BATIMENTO_MS = 3000;

// Após este número de envios seguidos sem ACK, passa a testar os outros canais.
const uint16_t FALHAS_PARA_BUSCA = 20;

// -------------------------------------------------------------------
// ESTADO
// -------------------------------------------------------------------
MAX30105 sensorPPG;
bool ppgOk  = false;
bool acelOk = false;
uint8_t enderecoAcel = 0x1C; // detectado no setup (0x1C ou 0x1D)

// Acelerômetro / passadas
float ax = 0, ay = 0, az = 0;
float gravidade = 1.0f;       // estimativa lenta da magnitude (componente DC)
float filtrado = 0.0f;        // magnitude sem gravidade, suavizada
bool  filtroIniciado = false;
bool  detectorArmado = true;
uint32_t ultimoPassoMs = 0;
uint32_t passos = 0;
float spm = 0.0f;
const uint8_t N_INTERVALOS = 6;
uint32_t intervalosPasso[N_INTERVALOS];
uint8_t nIntervalos = 0, idxIntervalo = 0;

// PPG / batimentos
int32_t ultimoIR = 0;
bool contato = false;
bool batimentoDesdeUltimaLinha = false;
uint32_t ultimoBatimentoMs = 0;
float bpm = 0.0f;
const uint8_t N_BATIMENTOS = 4;
float bpmRecentes[N_BATIMENTOS];
uint8_t nBatimentos = 0, idxBatimento = 0;

// ESP-NOW
esp_now_peer_info_t peer;
bool espNowOk = false;
uint8_t canalAtual = 1;
uint32_t seq = 0;
volatile uint32_t tEnvioUs = 0;
volatile uint32_t ultimaLatenciaUs = LATENCIA_INVALIDA;
volatile uint32_t falhasEnvio = 0;
volatile uint16_t falhasSeguidas = FALHAS_PARA_BUSCA - 3; // busca o canal logo após o boot

// -------------------------------------------------------------------
// MMA8452Q — acesso direto aos registradores
// -------------------------------------------------------------------
void escreveRegAcel(uint8_t reg, uint8_t valor) {
  Wire.beginTransmission(enderecoAcel);
  Wire.write(reg);
  Wire.write(valor);
  Wire.endTransmission();
}

bool leRegsAcel(uint8_t reg, uint8_t n, uint8_t *destino) {
  Wire.beginTransmission(enderecoAcel);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(enderecoAcel, n) != n) return false;
  for (uint8_t i = 0; i < n; i++) destino[i] = Wire.read();
  return true;
}

bool iniciaAcelerometro() {
  const uint8_t enderecos[2] = {0x1C, 0x1D};
  for (uint8_t i = 0; i < 2; i++) {
    enderecoAcel = enderecos[i];
    uint8_t whoAmI = 0;
    // WHO_AM_I (0x0D): 0x2A = MMA8452Q, 0x1A = MMA8451Q, 0x3A = MMA8453Q
    if (leRegsAcel(0x0D, 1, &whoAmI) && (whoAmI == 0x2A || whoAmI == 0x1A || whoAmI == 0x3A)) {
      escreveRegAcel(0x2A, 0x00); // CTRL_REG1: standby para configurar
      escreveRegAcel(0x0E, 0x02); // XYZ_DATA_CFG: escala +/- 8 g (corrida passa de 2 g)
      escreveRegAcel(0x2A, 0x19); // CTRL_REG1: ODR 100 Hz + ACTIVE
      Serial.printf("[OK] Acelerometro em 0x%02X (WHO_AM_I 0x%02X), +/-8 g, 100 Hz\n",
                    enderecoAcel, whoAmI);
      return true;
    }
  }
  Serial.println("[ERRO] Acelerometro MMA845x nao respondeu em 0x1C nem 0x1D");
  return false;
}

bool leAcelerometro() {
  uint8_t bruto[6];
  if (!leRegsAcel(0x01, 6, bruto)) return false;
  // Os dados vêm alinhados à esquerda em 16 bits. Em +/-8 g isso equivale a
  // 4096 contagens por g, qualquer que seja a resolução do chip (10/12/14 bits).
  ax = (int16_t)((bruto[0] << 8) | bruto[1]) / 4096.0f;
  ay = (int16_t)((bruto[2] << 8) | bruto[3]) / 4096.0f;
  az = (int16_t)((bruto[4] << 8) | bruto[5]) / 4096.0f;
  return true;
}

// -------------------------------------------------------------------
// PASSADAS E SPM
// -------------------------------------------------------------------
// Retorna true se esta amostra fechou uma passada.
bool processaAceleracao(float magnitude, uint32_t agora) {
  if (!filtroIniciado) {
    gravidade = magnitude;
    filtroIniciado = true;
  }
  gravidade += 0.02f * (magnitude - gravidade);         // passa-baixa lento (~1 s)
  filtrado  += 0.40f * ((magnitude - gravidade) - filtrado); // suaviza (~5 Hz)

  bool passo = false;
  if (detectorArmado && filtrado > LIMIAR_PASSO_G &&
      (agora - ultimoPassoMs) >= REFRATARIO_PASSO_MS) {
    passo = true;
    detectorArmado = false;
    uint32_t intervalo = agora - ultimoPassoMs;
    if (ultimoPassoMs != 0 && intervalo <= INTERVALO_MAX_PASSO_MS) {
      intervalosPasso[idxIntervalo] = intervalo;
      idxIntervalo = (idxIntervalo + 1) % N_INTERVALOS;
      if (nIntervalos < N_INTERVALOS) nIntervalos++;
    } else {
      nIntervalos = 0;
      idxIntervalo = 0;
    }
    ultimoPassoMs = agora;
    passos += PASSOS_POR_PICO;
  } else if (!detectorArmado && filtrado < LIMIAR_PASSO_G * 0.5f) {
    detectorArmado = true; // histerese: rearma só depois de o sinal cair
  }

  if (ultimoPassoMs != 0 && (agora - ultimoPassoMs) > TIMEOUT_PASSO_MS) {
    nIntervalos = 0;
    idxIntervalo = 0;
  }
  if (nIntervalos == 0) {
    spm = 0.0f;
  } else {
    uint32_t soma = 0;
    for (uint8_t i = 0; i < nIntervalos; i++) soma += intervalosPasso[i];
    spm = PASSOS_POR_PICO * 60000.0f * nIntervalos / (float)soma;
  }
  return passo;
}

// -------------------------------------------------------------------
// PPG E BPM
// -------------------------------------------------------------------
void zeraBpm() {
  bpm = 0.0f;
  nBatimentos = 0;
  idxBatimento = 0;
}

void processaPPG(int32_t ir, uint32_t agora) {
  ultimoIR = ir;
  bool batimento = checkForBeat(ir); // chamado sempre, para manter o filtro interno

  contato = ir > LIMIAR_CONTATO_IR;
  if (!contato) {
    zeraBpm();
    ultimoBatimentoMs = 0;
    return;
  }

  if (batimento) {
    batimentoDesdeUltimaLinha = true;
    if (ultimoBatimentoMs != 0) {
      float instantaneo = 60000.0f / (float)(agora - ultimoBatimentoMs);
      if (instantaneo >= BPM_MIN && instantaneo <= BPM_MAX) {
        bpmRecentes[idxBatimento] = instantaneo;
        idxBatimento = (idxBatimento + 1) % N_BATIMENTOS;
        if (nBatimentos < N_BATIMENTOS) nBatimentos++;
        float soma = 0;
        for (uint8_t i = 0; i < nBatimentos; i++) soma += bpmRecentes[i];
        bpm = soma / nBatimentos;
      }
    }
    ultimoBatimentoMs = agora;
  } else if (ultimoBatimentoMs != 0 && (agora - ultimoBatimentoMs) > TIMEOUT_BATIMENTO_MS) {
    zeraBpm();
  }
}

// -------------------------------------------------------------------
// ESP-NOW
// -------------------------------------------------------------------
// Chamada pelo callback de envio (espnow_compat.h), na tarefa do Wi-Fi.
void trataResultadoEnvio(bool ok) {
  uint32_t decorrido = micros() - tEnvioUs;
  if (ok) {
    ultimaLatenciaUs = decorrido;
    falhasSeguidas = 0;
  } else {
    ultimaLatenciaUs = LATENCIA_INVALIDA;
    falhasEnvio++;
    if (falhasSeguidas < 0xFFFF) falhasSeguidas++;
  }
}

void defineCanal(uint8_t canal) {
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(canal, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
  canalAtual = canal;
}

void enviaPacote(uint32_t agora) {
  // A receptora fica no canal do roteador Wi-Fi, que não é conhecido aqui.
  // Com o enlace caído, cada envio testa o canal seguinte até voltar o ACK.
  if (falhasSeguidas >= FALHAS_PARA_BUSCA) {
    defineCanal((canalAtual % 13) + 1);
  }

  pacote_t pacote;
  pacote.versao       = PACOTE_VERSAO;
  pacote.seq          = seq++;
  pacote.t_ms         = agora;
  pacote.bpm          = bpm;
  pacote.irValue      = ultimoIR;
  pacote.ax           = ax;
  pacote.ay           = ay;
  pacote.az           = az;
  pacote.spm          = spm;
  pacote.passos       = passos;
  pacote.latencia_us  = ultimaLatenciaUs;
  pacote.falhas_envio = falhasEnvio;
  pacote.contato      = contato ? 1 : 0;

  tEnvioUs = micros();
  if (esp_now_send(MAC_RECEPTORA, (const uint8_t *)&pacote, sizeof(pacote)) != ESP_OK) {
    trataResultadoEnvio(false); // nem chegou a ser enfileirado
  }
}

// -------------------------------------------------------------------
// SETUP
// -------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== GCP-Esteira | Bracadeira (emissora) V1 ===");

  Wire.begin(PINO_SDA, PINO_SCL);

  // Varredura I2C: esperado 0x57 (MAX30102) e 0x1C ou 0x1D (MMA8452Q).
  Serial.print("Dispositivos I2C:");
  for (uint8_t endereco = 1; endereco < 127; endereco++) {
    Wire.beginTransmission(endereco);
    if (Wire.endTransmission() == 0) Serial.printf(" 0x%02X", endereco);
  }
  Serial.println();

  if (sensorPPG.begin(Wire, I2C_SPEED_FAST)) {
    sensorPPG.setup(); // padrão da biblioteca: 400 amostras/s com média de 4 = 100 Hz
    sensorPPG.setPulseAmplitudeRed(0x0A);
    sensorPPG.setPulseAmplitudeGreen(0x00);
    ppgOk = true;
    Serial.println("[OK] MAX30102 inicializado");
  } else {
    Serial.println("[ERRO] MAX30102 nao encontrado");
  }

  acelOk = iniciaAcelerometro();
  if (acelOk) {
    delay(50);
    if (leAcelerometro()) {
      // Parado, a magnitude deve ficar perto de 1,00 g.
      Serial.printf("Magnitude em repouso: %.2f g\n", sqrtf(ax * ax + ay * ay + az * az));
    }
  }

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  Serial.print("MAC desta placa (emissora): ");
  Serial.println(macDesta());

  if (esp_now_init() != ESP_OK) {
    Serial.println("[ERRO] Falha ao iniciar o ESP-NOW");
  } else {
    esp_now_register_send_cb(cbEnvioEspNow);
    memset(&peer, 0, sizeof(peer));
    memcpy(peer.peer_addr, MAC_RECEPTORA, 6);
    peer.channel = 0; // 0 = usa o canal em que o rádio estiver
    peer.ifidx   = WIFI_IF_STA;
    peer.encrypt = false;
    if (esp_now_add_peer(&peer) != ESP_OK) {
      Serial.println("[ERRO] Falha ao registrar a receptora como peer");
    } else {
      espNowOk = true;
      defineCanal(1);
      Serial.println("[OK] ESP-NOW pronto");
    }
  }

  if (SAIDA_CSV) {
    Serial.println("CSVH,E,t_ms,ax,ay,az,mag,filtrado,passo,passos,spm,ir,batimento,bpm,"
                   "contato,lat_us,falhas,canal");
  }
}

// -------------------------------------------------------------------
// LOOP
// -------------------------------------------------------------------
void loop() {
  static uint32_t proximaAmostraMs = 0;
  static uint32_t proximoEnvioMs = 0;
  uint32_t agora = millis();

  // 1. PPG: consome todas as amostras novas da FIFO do sensor (100 Hz).
  if (ppgOk) {
    sensorPPG.check();
    while (sensorPPG.available()) {
      processaPPG((int32_t)sensorPPG.getFIFOIR(), millis());
      sensorPPG.nextSample();
    }
  }

  // 2. Acelerômetro a 50 Hz.
  if ((int32_t)(agora - proximaAmostraMs) >= 0) {
    proximaAmostraMs += PERIODO_ACEL_MS;
    if ((int32_t)(agora - proximaAmostraMs) > 0) proximaAmostraMs = agora + PERIODO_ACEL_MS;

    bool passo = false;
    float magnitude = 0.0f;
    if (acelOk && leAcelerometro()) {
      magnitude = sqrtf(ax * ax + ay * ay + az * az);
      passo = processaAceleracao(magnitude, agora);
    }

    if (SAIDA_CSV) {
      uint32_t latencia = ultimaLatenciaUs;
      Serial.printf("CSV,E,%lu,%.3f,%.3f,%.3f,%.3f,%.3f,%u,%lu,%.1f,%ld,%u,%.1f,%u,%ld,%lu,%u\n",
                    (unsigned long)agora, ax, ay, az, magnitude, filtrado,
                    passo ? 1 : 0, (unsigned long)passos, spm,
                    (long)ultimoIR, batimentoDesdeUltimaLinha ? 1 : 0, bpm,
                    contato ? 1 : 0,
                    latencia == LATENCIA_INVALIDA ? -1L : (long)latencia,
                    (unsigned long)falhasEnvio, canalAtual);
      batimentoDesdeUltimaLinha = false;
    }
  }

  // 3. Envio ESP-NOW a 10 Hz.
  if (espNowOk && (int32_t)(agora - proximoEnvioMs) >= 0) {
    proximoEnvioMs = agora + PERIODO_ENVIO_MS;
    enviaPacote(agora);
  }

  delay(1);
}
