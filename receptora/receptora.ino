#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <PubSubClient.h>
#include <TFT_eSPI.h>
#include <SPI.h>
#include <ArduinoJson.h>

// -------------------------------------------------------------------
// CONFIGURAÇÕES DE REDE E THINGSBOARD
// -------------------------------------------------------------------
const char* WIFI_SSID = "A22";
const char* WIFI_PASS = "leonardo1";

const char* TB_SERVER   = "10.68.103.45"; 
const int   TB_PORT     = 1883;
const char* TB_TOKEN = "novo123";

// Constante do limiar de presença do dedo no sensor MAX30102
const int32_t LIMIAR_DEDO_PRESENTE = 50000;

// -------------------------------------------------------------------
// ESTRUTURA DE DADOS RECEBIDA VIA ESP-NOW
// -------------------------------------------------------------------
typedef struct struct_message {
  float heartBPM;
  int32_t irValue;
  float ax;
  float ay;
  float az;
} struct_message;

struct_message incomingData;

// Instâncias
TFT_eSPI tft = TFT_eSPI();
WiFiClient espClient;
PubSubClient client(espClient);

// -------------------------------------------------------------------
// VARIÁVEIS GLOBAIS DE CONTROLE E ESTADO
// -------------------------------------------------------------------
unsigned long contadorPassos = 0;
float prevMagnitude = 0.0;
bool stepDetected = false;

unsigned long lastDisplayUpdate = 0;
unsigned long lastMqttSend = 0;

// Estados do Treino e Temporizadores
bool treinoConcluido = false;        // Bloqueia a tela durante os 5s de "PARABENS!"
bool treinoFinalizado = false;       // Ativa o modo pós-treino (Fundo Verde + Pedômetro Azul)
unsigned long tempoInicioConcluido = 0;
const unsigned long DURACAO_TELA_CONCLUIDO = 5000; // 5 segundos de exibição do aviso

// -------------------------------------------------------------------
// CALLBACK DE RECEBIMENTO ESP-NOW
// -------------------------------------------------------------------
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
void OnDataRecv(const esp_now_recv_info *info, const uint8_t *incomingBytes, int len) {
#else
void OnDataRecv(const uint8_t *mac_addr, const uint8_t *incomingBytes, int len) {
#endif
  if (len == sizeof(incomingData)) {
    memcpy(&incomingData, incomingBytes, sizeof(incomingData));

    // Só calcula passos se o treino ainda não tiver sido concluído
    if (!treinoConcluido && !treinoFinalizado) {
      float magnitude = sqrt(incomingData.ax * incomingData.ax + 
                             incomingData.ay * incomingData.ay + 
                             incomingData.az * incomingData.az);

      // Sensibilidade calibrada para maior precisão na esteira (Limiar 1.04)
      if (magnitude > 1.04 && prevMagnitude <= 1.04 && !stepDetected) {
        contadorPassos++;
        stepDetected = true;
      } else if (magnitude < 1.01) {
        stepDetected = false;
      }
      prevMagnitude = magnitude;
    }
  }
}

// -------------------------------------------------------------------
// RENDERIZAÇÃO DA TELA DE PARABÉNS (DURANTE OS 5 SEGUNDOS)
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
  char buffPassos[30];
  snprintf(buffPassos, sizeof(buffPassos), "Total: %lu passos", contadorPassos);
  tft.drawString(buffPassos, 120, 197, 2);
}

// -------------------------------------------------------------------
// INTERFACE GRAFICA ESTATÍSTICA (ADAPTA CORES CONFORME O ESTADO)
// -------------------------------------------------------------------
void desenharInterfaceEstatica() {
  // Fundo Preto (Normal) ou Fundo Verde (Treino Finalizado)
  uint16_t corFundo = treinoFinalizado ? TFT_GREEN : TFT_BLACK;
  tft.fillScreen(corFundo);

  // Barra de Título Topo
  tft.fillRect(0, 0, 240, 32, TFT_NAVY);
  tft.setTextColor(TFT_WHITE, TFT_NAVY);
  tft.setTextDatum(MC_DATUM);
  tft.drawString("GCP-esteira IoT", 120, 16, 4);

  // Moldura de Batimentos (BPM)
  tft.drawRoundRect(5, 38, 230, 92, 5, TFT_RED);
  tft.setTextColor(treinoFinalizado ? TFT_BLACK : TFT_WHITE, corFundo);
  tft.setTextDatum(TC_DATUM);
  tft.drawString("BATIMENTOS (BPM)", 120, 46, 2);

  // Moldura do Pedômetro (Verde no normal, Azul no pós-treino)
  uint16_t corPedometro = treinoFinalizado ? TFT_BLUE : TFT_GREEN;
  tft.drawRoundRect(5, 136, 230, 96, 5, corPedometro);
  tft.setTextColor(treinoFinalizado ? TFT_BLACK : TFT_WHITE, corFundo);
  tft.setTextDatum(TC_DATUM);
  tft.drawString("PEDOMETRO (PASSOS)", 120, 144, 2);
}

// -------------------------------------------------------------------
// ATUALIZAÇÃO DOS VALORES DINÂMICOS NO DISPLAY
// -------------------------------------------------------------------
void atualizarValoresDisplay() {
  if (treinoConcluido) return; // Não desenha enquanto exibe o aviso de Parabéns

  uint16_t corFundo = treinoFinalizado ? TFT_GREEN : TFT_BLACK;
  tft.setTextDatum(MC_DATUM);

  // 1. Atualização do BPM
  tft.fillRect(10, 62, 220, 60, corFundo);
  uint32_t bpmAtual = (uint32_t)incomingData.heartBPM;

  if (incomingData.irValue < LIMIAR_DEDO_PRESENTE) {
    tft.setTextColor(TFT_DARKGREY, corFundo);
    tft.drawString("SEM DEDO", 120, 90, 4);
  } else if (bpmAtual == 0) {
    tft.setTextColor(treinoFinalizado ? TFT_NAVY : TFT_YELLOW, corFundo);
    tft.drawString("ANALISANDO...", 120, 90, 4);
  } else {
    tft.setTextColor(TFT_RED, corFundo);
    char buffBPM[10];
    snprintf(buffBPM, sizeof(buffBPM), "%3u", bpmAtual);
    tft.drawString(buffBPM, 120, 90, 4);
  }

  // 2. Atualização dos Passos (Azul se finalizado, Verde se normal)
  tft.fillRect(10, 160, 220, 65, corFundo);
  tft.setTextColor(treinoFinalizado ? TFT_BLUE : TFT_GREEN, corFundo);
  char buffPassos[15];
  snprintf(buffPassos, sizeof(buffPassos), "%lu", contadorPassos);
  tft.drawString(buffPassos, 120, 190, 4);
}

// -------------------------------------------------------------------
// CALLBACK MQTT (RECEBIMENTO DE COMANDOS RPC DO THINGSBOARD)
// -------------------------------------------------------------------
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  
  Serial.print("Comando RPC recebido [Tópico ");
  Serial.print(topic);
  Serial.print("]: ");
  Serial.println(message);

  if (String(topic).startsWith("v1/devices/me/rpc/request/")) {
    StaticJsonDocument<200> doc;
    DeserializationError error = deserializeJson(doc, message);

    if (!error) {
      const char* method = doc["method"];
      if (method && String(method) == "concluirTreino") {
        treinoConcluido = true;
        tempoInicioConcluido = millis();
        exibirTelaConcluido();
      }
    }
  }
}

// -------------------------------------------------------------------
// RECONEXÃO MQTT NÃO-BLOQUEANTE
// -------------------------------------------------------------------
void reconnectMQTT() {
  static unsigned long lastReconnectAttempt = 0;
  
  // Tenta a cada 5 segundos sem travar a execução do loop ou do ESP-NOW
  if (millis() - lastReconnectAttempt >= 5000) {
    lastReconnectAttempt = millis();
    Serial.print("Tentando reconectar ao ThingsBoard...");
    // Gera um ClientID único a cada tentativa de conexão para não ser bloqueado pelo broker
    String clientId = "ESP32_Receiver_" + String(random(0, 0xffff), HEX);
    if (client.connect("ESP32_Receiver_Node", TB_TOKEN, NULL)) {
      Serial.println(" Conectado!");
      client.subscribe("v1/devices/me/rpc/request/+");
      Serial.println("Inscrito no topico de RPC!");
    } else {
      Serial.printf(" Falha (rc=%d)\n", client.state());
    }
  }
}

// -------------------------------------------------------------------
// SETUP PRINCIPAL
// -------------------------------------------------------------------
void setup() {
  Serial.begin(115200);

  // Backlight (GPIO 4)
  pinMode(4, OUTPUT);
  digitalWrite(4, HIGH);

  // Inicialização do Display TFT
  tft.init();
  tft.setRotation(2);
  tft.invertDisplay(true);

  desenharInterfaceEstatica();

  // Wi-Fi
  WiFi.mode(WIFI_AP_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Conectando ao Wi-Fi...");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi Conectado!");

  // ESP-NOW
  if (esp_now_init() != ESP_OK) {
    Serial.println("Erro ao inicializar ESP-NOW");
    return;
  }
  esp_now_register_recv_cb(OnDataRecv);

  // Configuração MQTT
  client.setServer(TB_SERVER, TB_PORT);
  client.setCallback(mqttCallback);
}

// -------------------------------------------------------------------
// LOOP PRINCIPAL
// -------------------------------------------------------------------
void loop() {
  // 1. Gerenciamento MQTT Não-Bloqueante
  if (!client.connected()) {
    reconnectMQTT();
  } else {
    client.loop();
  }

  // 2. Transição de tela: 5s após a conclusão, altera para o layout de fundo verde e passos azuis
  if (treinoConcluido && (millis() - tempoInicioConcluido >= DURACAO_TELA_CONCLUIDO)) {
    treinoConcluido = false;
    treinoFinalizado = true;     
    desenharInterfaceEstatica(); 
  }

  // 3. Atualização periódica do Display (4 Hz / a cada 250 ms)
  if (!treinoConcluido && millis() - lastDisplayUpdate >= 250) {
    lastDisplayUpdate = millis();
    atualizarValoresDisplay();
  }

  // 4. Envio de telemetria para o ThingsBoard a cada 1 segundo (se conectado)
  if (millis() - lastMqttSend >= 1000) {
    lastMqttSend = millis();

    if (client.connected()) {
      StaticJsonDocument<128> doc;
      doc["bpm"] = incomingData.heartBPM;
      doc["passos"] = contadorPassos;
      doc["concluido"] = (treinoConcluido || treinoFinalizado);

      char payloadBuffer[128];
      serializeJson(doc, payloadBuffer);

      // Imprime o payload JSON completo e bruto no Monitor Serial
      Serial.print("[MQTT Payload Sent]: ");
      Serial.println(payloadBuffer);

      bool publicado = client.publish("v1/devices/me/telemetry", payloadBuffer);
      if (publicado) {
        Serial.println(" -> Status: Publicado com SUCESSO!");
      } else {
        Serial.println(" -> Status: FALHA na publicacao!");
      }
    }
  }
}