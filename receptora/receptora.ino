// GCP-Esteira — Unidade de painel (visor) — Entrega V1
//
// Recebe os pacotes da braçadeira por ESP-NOW, mostra BPM e cadência no TFT,
// publica a telemetria no ThingsBoard por MQTT a 1 Hz e imprime cada pacote
// recebido em linhas "CSV,R,..." para o tools/serial_logger.py.
//
// Antes de compilar: copie secrets.example.h para secrets.h e preencha.

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <PubSubClient.h>
#include <TFT_eSPI.h>
#include <SPI.h>
#include <ArduinoJson.h>
#include "pacote.h"
#include "espnow_compat.h"
#include "secrets.h"
#include <esp_mac.h>

// MAC do rádio Wi-Fi lido direto da eFuse (ver macDesta() em emissora.ino).
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
const uint8_t  PINO_BACKLIGHT        = 4;
const uint32_t PERIODO_DISPLAY_MS    = 250;   // 4 Hz
const uint32_t PERIODO_MQTT_MS       = 1000;  // 1 Hz
const uint32_t TIMEOUT_ENLACE_MS     = 1000;  // sem pacote -> braçadeira fora do ar
const uint32_t DURACAO_TELA_CONCLUIDO = 5000; // tela de "PARABENS!"
const uint32_t TEMPO_TENTATIVA_WIFI_MS = 15000;
const uint32_t ESPERA_WIFI_MS        = 60000;
const uint32_t INTERVALO_MQTT_MS     = 5000;  // entre tentativas de conexão

const char *TOPICO_TELEMETRIA  = "v1/devices/me/telemetry";
const char *TOPICO_RPC_PEDIDOS = "v1/devices/me/rpc/request/+";
const char *PREFIXO_RPC_PEDIDO = "v1/devices/me/rpc/request/";
const char *PREFIXO_RPC_RESPOSTA = "v1/devices/me/rpc/response/";

// -------------------------------------------------------------------
// ESTADO
// -------------------------------------------------------------------
TFT_eSPI tft = TFT_eSPI();
WiFiClient espClient;
PubSubClient client(espClient);

// Pacotes passam do callback do ESP-NOW (tarefa do Wi-Fi) para o loop por esta fila.
struct Recebido {
  pacote_t pacote;
  uint32_t tRxMs;
  int8_t   rssi;
};
QueueHandle_t filaPacotes = NULL;
volatile uint32_t descartadosFila = 0;

pacote_t ultimo;
bool     temPacote = false;
uint32_t ultimoRxMs = 0;
int      ultimoRssi = 0;
uint32_t pacotesRx = 0;
uint32_t pacotesPerdidos = 0;

// Treino
bool treinoConcluido = false;   // mostrando "PARABENS!" por 5 s
bool treinoFinalizado = false;  // layout pós-treino (fundo verde)
uint32_t tempoInicioConcluido = 0;
uint32_t passosBase = 0;        // passos da braçadeira quando o treino (re)começou
uint32_t passosFinais = 0;      // congelado ao concluir

// Etapa do guia de cardio (V2), recebida pelo RPC definirEtapa.
bool     etapaAtiva = false;
char     etapaNome[12] = "";
float    etapaVel = 0.0f;       // km/h recomendada
uint32_t etapaFimMs = 0;        // millis() em que a etapa termina
uint16_t etapaZmin = 0, etapaZmax = 0; // faixa de BPM da zona-alvo
uint8_t  etapaIndice = 0, etapaTotal = 0;
bool     etapaAlerta = false;   // etapa de segurança: barra de título vermelha

// -------------------------------------------------------------------
// ALERTA LOCAL DE SEGURANÇA (A6)
// -------------------------------------------------------------------
// Exceção deliberada ao "sem processamento local": a comparação com o limite
// de segurança roda no painel, para o alerta funcionar mesmo sem internet.
// Não decide o treino: só sinaliza. O limite (95% da FC máx.) vem do guia no
// RPC definirEtapa ("limite_alerta"); 0 desliga o alerta.
const uint8_t  TAM_AMOSTRAS_BPM    = 64;
const uint8_t  MIN_AMOSTRAS_ALERTA = 5;
const uint32_t JANELA_ALERTA_MS    = 10000;  // mediana dos últimos 10 s, como no guia
struct AmostraBpm { uint32_t tMs; float bpm; };
AmostraBpm amostrasBpm[TAM_AMOSTRAS_BPM];
uint8_t  posAmostra = 0;
uint8_t  nAmostras = 0;
float    ultimoBpmRegistrado = -1.0f;
uint16_t limiteAlertaBpm = 0;
bool     alertaLocal = false;
float    medianaAlertaLocal = 0.0f;

// Guarda uma leitura de BPM. O BPM muda devagar, então só registra quando o valor muda.
void registraAmostraBpm(uint32_t tMs, float bpm) {
  if (bpm == ultimoBpmRegistrado) return;
  ultimoBpmRegistrado = bpm;
  amostrasBpm[posAmostra] = {tMs, bpm};
  posAmostra = (posAmostra + 1) % TAM_AMOSTRAS_BPM;
  if (nAmostras < TAM_AMOSTRAS_BPM) nAmostras++;
}

// Calcula a mediana das amostras da janela e liga/desliga o alerta.
void atualizaAlertaLocal(uint32_t agora) {
  float valores[TAM_AMOSTRAS_BPM];
  uint8_t n = 0;
  for (uint8_t i = 0; i < nAmostras; i++) {
    if ((agora - amostrasBpm[i].tMs) <= JANELA_ALERTA_MS) valores[n++] = amostrasBpm[i].bpm;
  }

  bool novo = false;
  if (limiteAlertaBpm > 0 && n >= MIN_AMOSTRAS_ALERTA) {
    // Ordenação por inserção: n é pequeno (no máximo 64).
    for (uint8_t i = 1; i < n; i++) {
      float v = valores[i];
      int j = (int)i - 1;
      while (j >= 0 && valores[j] > v) { valores[j + 1] = valores[j]; j--; }
      valores[j + 1] = v;
    }
    medianaAlertaLocal = (n % 2) ? valores[n / 2] : (valores[n / 2 - 1] + valores[n / 2]) / 2.0f;
    novo = medianaAlertaLocal > limiteAlertaBpm;
  }

  if (novo != alertaLocal) {
    alertaLocal = novo;
    Serial.printf("[ALERTA] %s: mediana %.0f BPM, limite %u\n",
                  novo ? "ATIVO" : "encerrado", medianaAlertaLocal, (unsigned)limiteAlertaBpm);
  }
}

// Rede
enum EstadoRede { REDE_TENTANDO, REDE_CONECTADA, REDE_ESPERA };
EstadoRede estadoRede = REDE_TENTANDO;
uint32_t tEstadoRede = 0;
uint32_t publicacoesOk = 0;
uint32_t publicacoesFalha = 0;

// -------------------------------------------------------------------
// ESP-NOW
// -------------------------------------------------------------------
// Chamada pelo callback de recepção (espnow_compat.h), na tarefa do Wi-Fi.
void trataPacoteRecebido(const uint8_t *dados, int len, int rssi) {
  if (len != (int)sizeof(pacote_t) || filaPacotes == NULL) return;
  Recebido r;
  memcpy(&r.pacote, dados, sizeof(pacote_t));
  if (r.pacote.versao != PACOTE_VERSAO) return;
  r.tRxMs = millis();
  r.rssi = (int8_t)rssi;
  if (xQueueSend(filaPacotes, &r, 0) != pdTRUE) descartadosFila++;
}

bool enlaceAtivo(uint32_t agora) {
  return temPacote && (agora - ultimoRxMs) < TIMEOUT_ENLACE_MS;
}

uint32_t passosDoTreino() {
  if (treinoConcluido || treinoFinalizado) return passosFinais;
  return temPacote ? (ultimo.passos - passosBase) : 0;
}

void processaFila() {
  Recebido r;
  while (xQueueReceive(filaPacotes, &r, 0) == pdTRUE) {
    uint32_t intervaloChegada = 0;
    if (temPacote) {
      intervaloChegada = r.tRxMs - ultimoRxMs;
      if (r.pacote.seq > ultimo.seq + 1) {
        pacotesPerdidos += r.pacote.seq - ultimo.seq - 1;
      } else if (r.pacote.seq <= ultimo.seq) {
        // seq voltou: a braçadeira reiniciou. Recomeça as contagens.
        pacotesRx = 0;
        pacotesPerdidos = 0;
        passosBase = 0;
      }
    }
    pacotesRx++;
    ultimo = r.pacote;
    ultimoRxMs = r.tRxMs;
    ultimoRssi = r.rssi;
    temPacote = true;
    if (r.pacote.contato && r.pacote.bpm >= 1.0f) registraAmostraBpm(r.tRxMs, r.pacote.bpm);

    Serial.printf("CSV,R,%lu,%lu,%lu,%lu,%d,%lu,%.1f,%.1f,%lu,%ld,%u,%ld,%lu,%.3f,%.3f,%.3f\n",
                  (unsigned long)r.tRxMs, (unsigned long)r.pacote.seq,
                  (unsigned long)r.pacote.t_ms, (unsigned long)intervaloChegada,
                  r.rssi, (unsigned long)pacotesPerdidos,
                  r.pacote.bpm, r.pacote.spm, (unsigned long)r.pacote.passos,
                  (long)r.pacote.irValue, r.pacote.contato,
                  r.pacote.latencia_us == LATENCIA_INVALIDA ? -1L : (long)r.pacote.latencia_us,
                  (unsigned long)r.pacote.falhas_envio,
                  r.pacote.ax, r.pacote.ay, r.pacote.az);
  }
}

// -------------------------------------------------------------------
// DISPLAY
// -------------------------------------------------------------------
void exibirTelaConcluido() {
  tft.fillScreen(TFT_BLACK);
  tft.fillRect(0, 0, 240, 40, TFT_GREEN);
  tft.setTextColor(TFT_BLACK, TFT_GREEN);
  tft.setTextDatum(MC_DATUM);
  tft.drawString("PARABENS!", 120, 20, 4);

  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.drawString("VOCE CONCLUIU", 120, 90, 4);
  tft.drawString("SEU TREINO!", 120, 130, 4);

  tft.drawRoundRect(10, 170, 220, 55, 8, TFT_YELLOW);
  tft.setTextColor(TFT_YELLOW, TFT_BLACK);
  char texto[30];
  snprintf(texto, sizeof(texto), "Total: %lu passos", (unsigned long)passosFinais);
  tft.drawString(texto, 120, 197, 2);
}

void desenharInterfaceEstatica() {
  uint16_t corFundo = treinoFinalizado ? TFT_GREEN : TFT_BLACK;
  uint16_t corTexto = treinoFinalizado ? TFT_BLACK : TFT_WHITE;
  tft.fillScreen(corFundo);

  uint16_t corBarra = (etapaAtiva && etapaAlerta) ? TFT_RED : TFT_NAVY;
  tft.fillRect(0, 0, 240, 32, corBarra);
  tft.setTextColor(TFT_WHITE, corBarra);
  tft.setTextDatum(MC_DATUM);
  if (etapaAtiva) {
    char titulo[24];
    snprintf(titulo, sizeof(titulo), "%s %u/%u", etapaNome, etapaIndice, etapaTotal);
    tft.drawString(titulo, 120, 16, 4);
  } else {
    tft.drawString("GCP-esteira IoT", 120, 16, 4);
  }

  tft.drawRoundRect(5, 38, 230, 92, 5, TFT_RED);
  tft.setTextColor(corTexto, corFundo);
  tft.setTextDatum(TC_DATUM);
  tft.drawString("BATIMENTOS (BPM)", 120, 46, 2);

  tft.drawRoundRect(5, 136, 230, 96, 5, treinoFinalizado ? TFT_BLUE : TFT_GREEN);
  tft.setTextColor(corTexto, corFundo);
  tft.drawString(etapaAtiva ? "VELOCIDADE E TEMPO" : "CADENCIA (SPM)", 120, 144, 2);
}

void atualizarValoresDisplay(uint32_t agora) {
  if (treinoConcluido) return;

  uint16_t corFundo = treinoFinalizado ? TFT_GREEN : TFT_BLACK;
  bool enlace = enlaceAtivo(agora);
  tft.setTextDatum(MC_DATUM);

  // Indicadores na barra de título: braçadeira (esquerda) e MQTT (direita).
  tft.fillCircle(10, 16, 5, enlace ? TFT_GREEN : TFT_RED);
  tft.fillCircle(230, 16, 5, client.connected() ? TFT_GREEN : TFT_RED);

  // Rótulo do BPM; pisca em vermelho enquanto o alerta local estiver ativo.
  uint16_t corTextoRotulo = treinoFinalizado ? TFT_BLACK : TFT_WHITE;
  tft.fillRect(10, 40, 220, 18, corFundo);
  tft.setTextDatum(TC_DATUM);
  if (alertaLocal && ((agora / 500) % 2 == 0)) {
    tft.fillRect(10, 40, 220, 18, TFT_RED);
    tft.setTextColor(TFT_WHITE, TFT_RED);
    tft.drawString("FC ALTA: REDUZA O RITMO", 120, 46, 2);
  } else {
    tft.setTextColor(corTextoRotulo, corFundo);
    tft.drawString("BATIMENTOS (BPM)", 120, 46, 2);
  }
  tft.setTextDatum(MC_DATUM);

  // BPM
  tft.fillRect(10, 62, 220, 60, corFundo);
  if (!enlace) {
    tft.setTextColor(TFT_DARKGREY, corFundo);
    tft.drawString("SEM SINAL", 120, 90, 4);
  } else if (!ultimo.contato) {
    tft.setTextColor(TFT_DARKGREY, corFundo);
    tft.drawString("SEM CONTATO", 120, 90, 4);
  } else if (ultimo.bpm < 1.0f) {
    tft.setTextColor(treinoFinalizado ? TFT_NAVY : TFT_YELLOW, corFundo);
    tft.drawString("ANALISANDO...", 120, 90, 4);
  } else {
    char texto[10];
    snprintf(texto, sizeof(texto), "%3u", (unsigned)(ultimo.bpm + 0.5f));
    tft.setTextColor(TFT_RED, corFundo);
    tft.drawString(texto, 120, 90, 4);
  }

  char texto[32];
  if (etapaAtiva) {
    // Faixa-alvo e moldura colorida: verde na zona, amarelo abaixo, vermelho acima.
    bool temBpm = enlace && ultimo.contato && ultimo.bpm >= 1.0f;
    uint16_t corZona = TFT_DARKGREY;
    if (temBpm) {
      corZona = ultimo.bpm < etapaZmin ? TFT_YELLOW : (ultimo.bpm > etapaZmax ? TFT_RED : TFT_GREEN);
    }
    tft.drawRoundRect(5, 38, 230, 92, 5, corZona);
    tft.drawRoundRect(6, 39, 228, 90, 5, corZona);
    snprintf(texto, sizeof(texto), "alvo %u-%u", etapaZmin, etapaZmax);
    tft.setTextColor(TFT_WHITE, corFundo);
    tft.drawString(texto, 120, 113, 2);

    // Velocidade recomendada e tempo restante da etapa.
    uint32_t restante = (agora < etapaFimMs) ? (etapaFimMs - agora + 999) / 1000 : 0;
    tft.fillRect(10, 162, 220, 65, corFundo);
    snprintf(texto, sizeof(texto), "%.1fkm/h %lu:%02lu", etapaVel,
             (unsigned long)(restante / 60), (unsigned long)(restante % 60));
    tft.setTextColor(etapaAlerta ? TFT_RED : TFT_GREEN, corFundo);
    tft.drawString(texto, 120, 182, 4);
    if (enlace) {
      snprintf(texto, sizeof(texto), "%u spm  %lu passos", (unsigned)(ultimo.spm + 0.5f),
               (unsigned long)passosDoTreino());
    } else {
      snprintf(texto, sizeof(texto), "-- spm  %lu passos", (unsigned long)passosDoTreino());
    }
    tft.setTextColor(TFT_WHITE, corFundo);
    tft.drawString(texto, 120, 213, 2);
    return;
  }

  // Cadência e passos
  tft.fillRect(10, 162, 220, 65, corFundo);
  tft.setTextColor(treinoFinalizado ? TFT_BLUE : TFT_GREEN, corFundo);
  if (enlace) {
    snprintf(texto, sizeof(texto), "%u", (unsigned)(ultimo.spm + 0.5f));
  } else {
    snprintf(texto, sizeof(texto), "--");
  }
  tft.drawString(texto, 120, 182, 4);
  snprintf(texto, sizeof(texto), "%lu passos", (unsigned long)passosDoTreino());
  tft.drawString(texto, 120, 213, 2);
}

// -------------------------------------------------------------------
// MQTT / THINGSBOARD
// -------------------------------------------------------------------
void mqttCallback(char *topic, byte *payload, unsigned int length) {
  size_t tamanhoPrefixo = strlen(PREFIXO_RPC_PEDIDO);
  if (strncmp(topic, PREFIXO_RPC_PEDIDO, tamanhoPrefixo) != 0) return;

  // O buffer do PubSubClient é reutilizado no publish: copia o que precisa antes.
  char topicoResposta[64];
  snprintf(topicoResposta, sizeof(topicoResposta), "%s%s", PREFIXO_RPC_RESPOSTA,
           topic + tamanhoPrefixo);

#if ARDUINOJSON_VERSION_MAJOR >= 7
  JsonDocument doc;
#else
  StaticJsonDocument<256> doc;
#endif
  if (deserializeJson(doc, payload, length)) {
    Serial.println("[RPC] JSON invalido");
    return;
  }
  const char *metodo = doc["method"] | "";
  Serial.printf("[RPC] metodo: %s\n", metodo);

  bool reconhecido = true;
  if (strcmp(metodo, "definirEtapa") == 0) {
    // Enviado pelo serviço guia/ a cada troca de etapa.
    JsonVariant p = doc["params"];
    strlcpy(etapaNome, p["nome"] | "ETAPA", sizeof(etapaNome));
    etapaVel    = p["vel"] | 0.0f;
    etapaFimMs  = millis() + (uint32_t)(p["dur"] | 0) * 1000UL;
    etapaZmin   = p["zmin"] | 0;
    etapaZmax   = p["zmax"] | 0;
    etapaIndice = p["i"] | 0;
    etapaTotal  = p["n"] | 0;
    etapaAlerta = p["alerta"] | false;
    limiteAlertaBpm = p["limite_alerta"] | 0;
    if (!etapaAtiva || etapaIndice <= 1) {
      passosBase = temPacote ? ultimo.passos : 0; // primeira etapa: zera a contagem
    }
    etapaAtiva = true;
    treinoConcluido = false;
    treinoFinalizado = false;
    desenharInterfaceEstatica();
  } else if (strcmp(metodo, "concluirTreino") == 0 || strcmp(metodo, "encerrarTreino") == 0) {
    passosFinais = passosDoTreino();
    etapaAtiva = false;
    treinoConcluido = true;
    treinoFinalizado = false;
    tempoInicioConcluido = millis();
    exibirTelaConcluido();
  } else if (strcmp(metodo, "reiniciarTreino") == 0) {
    etapaAtiva = false;
    treinoConcluido = false;
    treinoFinalizado = false;
    passosBase = temPacote ? ultimo.passos : 0;
    desenharInterfaceEstatica();
  } else {
    reconhecido = false;
  }
  client.publish(topicoResposta, reconhecido ? "{\"ok\":true}" : "{\"ok\":false}");
}

void reconectaMQTT(uint32_t agora) {
  static uint32_t ultimaTentativa = 0;
  if (ultimaTentativa != 0 && (agora - ultimaTentativa) < INTERVALO_MQTT_MS) return;
  ultimaTentativa = agora;

  // ClientID derivado do MAC: fixo para esta placa e diferente entre placas.
  String clientId = "gcp-painel-" + macDesta();
  clientId.replace(":", "");
  Serial.printf("[MQTT] Conectando a %s:%d ... ", TB_SERVER, TB_PORT);
  if (client.connect(clientId.c_str(), TB_TOKEN, NULL)) {
    client.subscribe(TOPICO_RPC_PEDIDOS);
    Serial.println("conectado");
  } else {
    Serial.printf("falha (rc=%d)\n", client.state());
  }
}

float arredonda1(float valor) {
  return roundf(valor * 10.0f) / 10.0f;
}

void publicaTelemetria(uint32_t agora) {
#if ARDUINOJSON_VERSION_MAJOR >= 7
  JsonDocument doc;
#else
  StaticJsonDocument<512> doc;
#endif
  bool enlace = enlaceAtivo(agora);
  uint32_t esperados = pacotesRx + pacotesPerdidos;

  doc["enlace"] = enlace;
  if (enlace) {
    // Sem enlace não publica valores de sensor, para não repetir leitura velha.
    doc["bpm"]     = arredonda1(ultimo.bpm);
    doc["spm"]     = arredonda1(ultimo.spm);
    doc["ir"]      = ultimo.irValue;
    doc["contato"] = ultimo.contato != 0;
    doc["rssi"]    = ultimoRssi;
    if (ultimo.latencia_us != LATENCIA_INVALIDA) {
      doc["latencia_ms"] = roundf(ultimo.latencia_us / 10.0f) / 100.0f;
    }
    doc["falhas_envio"] = ultimo.falhas_envio;
  }
  doc["passos"]           = passosDoTreino();
  doc["pacotes_rx"]       = pacotesRx;
  doc["pacotes_perdidos"] = pacotesPerdidos;
  doc["perda_pct"]        = esperados ? arredonda1(100.0f * pacotesPerdidos / esperados) : 0.0f;
  doc["concluido"]        = (treinoConcluido || treinoFinalizado);
  doc["alerta_local"]     = alertaLocal;
  if (etapaAtiva) {
    doc["etapa"]    = etapaIndice;
    doc["vel_alvo"] = etapaVel;
  }

  char payload[512];
  size_t tamanho = serializeJson(doc, payload, sizeof(payload));
  bool ok = client.publish(TOPICO_TELEMETRIA, (const uint8_t *)payload, tamanho);
  if (ok) publicacoesOk++; else publicacoesFalha++;

  Serial.printf("CSV,M,%lu,%u,%d,%lu,%lu\n", (unsigned long)agora, ok ? 1 : 0,
                (int)WiFi.RSSI(), (unsigned long)publicacoesOk, (unsigned long)publicacoesFalha);
}

// -------------------------------------------------------------------
// WI-FI SEM BLOQUEIO
// -------------------------------------------------------------------
// Enquanto procura a rede, o rádio varre os canais e o ESP-NOW perde pacotes.
// Por isso cada tentativa é limitada e, se falhar, há uma pausa longa em que
// o painel funciona só com a braçadeira.
void iniciaTentativaWiFi(uint32_t agora) {
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  estadoRede = REDE_TENTANDO;
  tEstadoRede = agora;
  Serial.printf("[WiFi] Conectando a \"%s\" ...\n", WIFI_SSID);
}

void gerenciaWiFi(uint32_t agora) {
  bool conectado = (WiFi.status() == WL_CONNECTED);
  switch (estadoRede) {
    case REDE_TENTANDO:
      if (conectado) {
        estadoRede = REDE_CONECTADA;
        Serial.printf("[WiFi] Conectado. IP %s, canal %d\n",
                      WiFi.localIP().toString().c_str(), (int)WiFi.channel());
      } else if ((agora - tEstadoRede) >= TEMPO_TENTATIVA_WIFI_MS) {
        WiFi.disconnect();
        estadoRede = REDE_ESPERA;
        tEstadoRede = agora;
        Serial.println("[WiFi] Sem rede. Operando so com ESP-NOW; nova tentativa em 60 s.");
      }
      break;
    case REDE_CONECTADA:
      if (!conectado) {
        Serial.println("[WiFi] Conexao perdida.");
        iniciaTentativaWiFi(agora);
      }
      break;
    case REDE_ESPERA:
      if ((agora - tEstadoRede) >= ESPERA_WIFI_MS) iniciaTentativaWiFi(agora);
      break;
  }
}

// -------------------------------------------------------------------
// SETUP
// -------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== GCP-Esteira | Painel (receptora) V1 ===");

  pinMode(PINO_BACKLIGHT, OUTPUT);
  digitalWrite(PINO_BACKLIGHT, HIGH);

  tft.init();
  tft.setRotation(2);
  tft.invertDisplay(true);
  desenharInterfaceEstatica();

  filaPacotes = xQueueCreate(32, sizeof(Recebido));

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false); // a reconexão é feita por gerenciaWiFi()
  WiFi.setSleep(false);         // com economia de energia o ESP-NOW perde pacotes
  Serial.print("MAC desta placa (receptora): ");
  Serial.println(macDesta());
  Serial.println("  -> confira com MAC_RECEPTORA em emissora.ino");

  if (esp_now_init() != ESP_OK) {
    Serial.println("[ERRO] Falha ao iniciar o ESP-NOW");
  } else {
    esp_now_register_recv_cb(cbRecebimentoEspNow);
    Serial.println("[OK] ESP-NOW pronto");
  }

  client.setServer(TB_SERVER, TB_PORT);
  client.setCallback(mqttCallback);
  client.setBufferSize(768);

  Serial.println("CSVH,R,t_rx_ms,seq,t_tx_ms,dt_chegada_ms,rssi,perdidos,bpm,spm,passos,ir,"
                 "contato,lat_us,falhas_envio,ax,ay,az");
  Serial.println("CSVH,M,t_ms,ok,wifi_rssi,pub_ok,pub_falha");

  iniciaTentativaWiFi(millis());
}

// -------------------------------------------------------------------
// LOOP
// -------------------------------------------------------------------
void loop() {
  static uint32_t ultimoDisplay = 0;
  static uint32_t ultimoMqtt = 0;
  uint32_t agora = millis();

  processaFila();
  gerenciaWiFi(agora);

  if (estadoRede == REDE_CONECTADA) {
    if (!client.connected()) {
      reconectaMQTT(agora);
    } else {
      client.loop();
    }
  }

  // 5 s depois de concluir, troca para o layout pós-treino.
  if (treinoConcluido && (agora - tempoInicioConcluido) >= DURACAO_TELA_CONCLUIDO) {
    treinoConcluido = false;
    treinoFinalizado = true;
    desenharInterfaceEstatica();
  }

  atualizaAlertaLocal(agora);

  if (!treinoConcluido && (agora - ultimoDisplay) >= PERIODO_DISPLAY_MS) {
    ultimoDisplay = agora;
    atualizarValoresDisplay(agora);
  }

  if ((agora - ultimoMqtt) >= PERIODO_MQTT_MS) {
    ultimoMqtt = agora;
    if (client.connected()) publicaTelemetria(agora);
  }

  delay(1);
}
