// Copie este arquivo para "secrets.h" (na mesma pasta) e preencha com os dados
// da sua rede de teste. O secrets.h está no .gitignore e não deve ser versionado.
#pragma once

#define WIFI_SSID "brisa-1969474"
#define WIFI_PASS ""

// IP do computador que roda o ThingsBoard (ipconfig -> Endereço IPv4),
// na mesma rede Wi-Fi do painel.
#define TB_SERVER "192.168.0.10"
#define TB_PORT   1883

// Token de acesso do dispositivo criado no ThingsBoard
// (ou impresso por tools/tb_provisionar.py).
#define TB_TOKEN  "token-do-dispositivo"
