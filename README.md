# GCP-Esteira — Guia de Cadência e Pace para Esteira

Projeto da disciplina de Internet das Coisas (Engenharia de Computação, UFC).
Equipe: Klaiver Santos Diogo, Lucas Ferreira da Rocha e Leonardo Máximo Viana.

Uma braçadeira mede a frequência cardíaca (BPM) e a cadência de passadas (SPM) do corredor e envia os dados, sem fio, a um visor fixado no painel da esteira. O visor publica a telemetria no ThingsBoard por MQTT.

```
 Braçadeira (ESP32)                Painel (ESP32)                  PC / servidor
 MAX30102  -> BPM      ESP-NOW     display TFT ST7789     MQTT     ThingsBoard
 MMA8452Q  -> SPM   ------------>  BPM, SPM, passos    --------->  séries temporais
                      10 pacotes/s                      1 msg/s    e dashboard
```

## Estado atual

- **V1 (coleta e comunicação):** firmware gravado nas duas placas e validado de ponta a ponta (braçadeira → painel → ThingsBoard). Testes T1, T2 e T3 feitos; resultados em `docs/relatorio_v1.md`.
- **V2 (guia de cardio):** o serviço `guia/` monta o treino, envia cada etapa ao painel e ajusta as etapas pela FC e pela cadência. Validado com usuário simulado, com os dados reais do T3 e com uma sessão curta ao vivo no hardware; **ainda sem sessão guiada completa com uma pessoa na esteira**. Detalhes em `docs/relatorio_v2.md`.

## Guia de cardio (V2)

```powershell
# uma vez: baixar subject-info.csv e test_measure.csv de
# https://physionet.org/content/treadmill-exercise-cardioresp/1.0.1/ para dados/externos/treadmill/
python guia/treinar_modelo.py

python guia/executar.py plano   --treino hiit --idade 22 --fc-repouso 62    # mostra as etapas
python guia/executar.py simular --treino hiit --idade 22 --fc-repouso 62    # sessão com usuário simulado
python guia/executar.py ao-vivo --treino hiit --idade 22 --fc-repouso 62    # conduz o painel pelo ThingsBoard
```

Tipos de treino: `hiit`, `continuo`, `progressivo` e `recuperacao`. `--escala 0.2` encurta as etapas para demonstração; `--vel-max` define o teto de velocidade; `--calibracao 6:118` informa uma FC estável medida no usuário (118 BPM a 6 km/h). O modo ao vivo precisa do painel conectado ao ThingsBoard e grava a sessão em `dados/guia/`.

O guia recomenda a velocidade, mas não controla a esteira. É um protótipo acadêmico, não orientação médica.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `emissora/` | firmware da braçadeira |
| `receptora/` | firmware do painel |
| `thingsboard/` | `docker-compose.yml` do ThingsBoard local |
| `tools/` | scripts Python de coleta, exportação e gráficos |
| `guia/` | guia de cardio da V2: zonas, modelo, regras de decisão e execução |
| `dados/brutos/` | CSV das coletas reais |
| `dados/guia/` | registros das sessões do guia (simuladas, reproduzidas e ao vivo) |
| `dados/externos/` | dataset público usado no treino do modelo (fora do Git) |
| `docs/` | documentação de hardware, relatórios V1 e V2, plano da V2 e figuras |

`emissora/pacote.h` e `receptora/pacote.h` são cópias do mesmo arquivo (formato do pacote ESP-NOW) e precisam ser mantidas iguais.

## Preparação do ambiente

### Firmware

Com o [arduino-cli](https://arduino.github.io/arduino-cli/) (ou os mesmos passos pela Arduino IDE 2):

```powershell
winget install ArduinoSA.CLI
arduino-cli config init
arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32
arduino-cli lib install "SparkFun MAX3010x Pulse and Proximity Sensor Library" PubSubClient ArduinoJson TFT_eSPI
```

Configurações locais, que não vão para o Git:

1. Copie `receptora/secrets.example.h` para `receptora/secrets.h` e preencha Wi-Fi, IP do servidor e token.
2. Preencha a pinagem do display em `docs/TFT_eSPI_User_Setup.h` (veja `docs/hardware.md`) e copie o arquivo por cima do `User_Setup.h` da biblioteca TFT_eSPI.

Compilar e gravar (troque as portas pelas suas; `python tools/serial_logger.py --listar` mostra as disponíveis):

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32 emissora
arduino-cli upload  --fqbn esp32:esp32:esp32 -p COM5 emissora
arduino-cli compile --fqbn esp32:esp32:esp32 receptora
arduino-cli upload  --fqbn esp32:esp32:esp32 -p COM6 receptora
```

`esp32:esp32:esp32` é a placa genérica "ESP32 Dev Module"; ajuste se o modelo for outro.

### Python

```powershell
pip install -r tools/requirements.txt
```

### ThingsBoard local

Requer o Docker Desktop aberto e cerca de 4 GB de RAM livres.

```powershell
cd thingsboard
docker compose run --rm -e INSTALL_TB=true -e LOAD_DEMO=true thingsboard-ce   # só na primeira vez
docker compose up -d
cd ..
python tools/tb_provisionar.py     # cria o dispositivo e imprime o token
```

- Interface: <http://localhost:8080>, usuário `tenant@thingsboard.org`, senha `tenant`.
- O painel e o PC precisam estar na mesma rede Wi-Fi de 2,4 GHz. Em `secrets.h`, `TB_SERVER` é o IPv4 do PC nessa rede (`ipconfig`).
- Se o painel não conectar ao MQTT, libere a porta no firewall do Windows (PowerShell como administrador):
  `New-NetFirewallRule -DisplayName "ThingsBoard MQTT" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow`

## Primeira execução

Como o circuito foi recebido já montado, valide nesta ordem. Os detalhes e os campos a anotar estão em `docs/hardware.md`.

1. **Braçadeira sozinha no USB.** A serial deve listar os dois sensores na varredura I2C e mostrar magnitude em repouso perto de 1,00 g.
2. **Painel sozinho no USB.** Anote o MAC impresso e compare com `MAC_RECEPTORA` em `emissora/emissora.ino`. O display deve desenhar a interface.
3. **Os dois ligados.** O círculo da esquerda no título do painel fica verde. A braçadeira procura sozinha o canal Wi-Fi em que o painel está; isso leva alguns segundos após ligar.
4. **Com o ThingsBoard no ar.** O círculo da direita fica verde e a aba "Latest telemetry" do dispositivo atualiza a cada segundo.

## Comunicação

### ESP-NOW (braçadeira → painel)

Pacote de 46 bytes definido em `pacote.h`, enviado a 10 Hz para o MAC do painel, com confirmação (ACK) na camada MAC.

| Campo | Tipo | Significado |
|---|---|---|
| `versao` | uint8 | versão do formato (2) |
| `seq` | uint32 | contador de pacotes, usado pelo painel para medir perda |
| `t_ms` | uint32 | relógio da braçadeira no envio |
| `bpm` | float | média dos 4 últimos batimentos; 0 = sem leitura |
| `irValue` | int32 | última amostra bruta do PPG |
| `ax`, `ay`, `az` | float | aceleração em g |
| `spm` | float | passadas por minuto; 0 = parado |
| `passos` | uint32 | passadas desde o boot |
| `latencia_us` | uint32 | tempo entre o envio e o ACK do pacote anterior |
| `falhas_envio` | uint32 | envios sem ACK desde o boot |
| `contato` | uint8 | 1 = sensor óptico encostado na pele |

O painel fica no canal do roteador Wi-Fi. A braçadeira não conhece esse canal: quando fica 2 s sem ACK, passa a testar um canal a cada envio até o ACK voltar.

### MQTT (painel → ThingsBoard)

| Item | Valor |
|---|---|
| Broker | ThingsBoard, porta 1883, sem TLS |
| Autenticação | token do dispositivo como usuário MQTT, senha vazia |
| Telemetria | publica em `v1/devices/me/telemetry`, 1 mensagem por segundo |
| Comandos | assina `v1/devices/me/rpc/request/+`, responde em `v1/devices/me/rpc/response/<id>` |

Exemplo de telemetria:

```json
{"enlace":true,"bpm":92.5,"spm":141.2,"ir":108345,"contato":true,"rssi":-54,
 "latencia_ms":1.84,"falhas_envio":0,"passos":312,"pacotes_rx":2210,
 "pacotes_perdidos":7,"perda_pct":0.3,"concluido":false}
```

Sem pacotes da braçadeira há mais de 1 s, o painel publica `"enlace":false` e omite os valores de sensor.

Comandos RPC aceitos: `concluirTreino` (mostra a tela de conclusão e congela a contagem de passos) e `reiniciarTreino` (volta à tela normal e zera a contagem).

### Dashboard

No ThingsBoard, crie um dashboard "GCP-Esteira V1" com o dispositivo "GCP-Esteira Painel" como fonte de dados. Os nomes dos widgets podem variar um pouco entre versões:

1. Gráfico de série temporal com `bpm`.
2. Gráfico de série temporal com `spm`.
3. Cartões de valor com `bpm`, `spm` e `passos`.
4. Gráfico de série temporal com `rssi` e outro com `latencia_ms` e `perda_pct`.
5. Dois botões de comando RPC, com os métodos `concluirTreino` e `reiniciarTreino`.

Depois exporte o dashboard (menu do dashboard → exportar) para `thingsboard/dashboard_gcp_esteira.json`.

## Coleta de dados e gráficos

O firmware imprime linhas `CSV,<tipo>,...` na serial. O `serial_logger.py` grava cada tipo em um arquivo:

| Tipo | Origem | Taxa | Conteúdo |
|---|---|---|---|
| `E` | braçadeira no USB | 50 Hz | sinal bruto do acelerômetro e do PPG, passadas e batimentos detectados |
| `R` | painel no USB | 10 Hz | cada pacote recebido, RSSI, perda e latência |
| `M` | painel no USB | 1 Hz | resultado de cada publicação MQTT |

```powershell
# grava 60 s do painel, registrando o valor do metrônomo como referência
python tools/serial_logger.py --porta COM6 --teste T2_140spm --ref-spm 140 --duracao 60

# baixa o que o ThingsBoard armazenou nos últimos 5 minutos, junto da mesma coleta
python tools/tb_export.py --minutos 5 --base dados/brutos/<data_hora>_T2_140spm

# gera as figuras e o resumo em docs/figuras
python tools/plot_coleta.py dados/brutos
```

Figuras geradas por coleta: aceleração com passadas detectadas, PPG com batimentos detectados, BPM e SPM no tempo contra a referência, histograma de latência do ESP-NOW, qualidade do enlace (RSSI, perda, intervalo entre pacotes) e séries armazenadas no ThingsBoard. O arquivo `<coleta>_resumo.md` traz médias, erros e percentuais prontos para o relatório.

Abrir a porta serial reinicia a ESP32; use `--sem-reset` para evitar.

### Protocolo de testes da V1

| Teste | Procedimento | Placa no USB |
|---|---|---|
| T1 Repouso | 2 min sentado. Referência: oxímetro, relógio ou pulso contado em 60 s (`--ref-bpm`) | braçadeira |
| T2 Cadência | Metrônomo em 100, 140 e 170 SPM, 60 s cada, uma gravação por cadência (`--ref-spm`) | braçadeira, depois painel |
| T3 Esteira | 5 a 10 min: caminhada, trote, caminhada, com o ThingsBoard no ar | painel |
| T4 Comunicação | 5 min parado a 1 m e depois a 3 m do painel | painel |

Os limiares de passada (`LIMIAR_PASSO_G`, `PASSOS_POR_PICO`) e de contato (`LIMIAR_CONTATO_IR`) em `emissora.ino` são valores iniciais. Ajuste-os olhando as figuras de T1 e T2 antes de fazer T3.

### Testar os scripts sem hardware

```powershell
python tools/simula_coleta.py --spm 140
python tools/plot_coleta.py dados/simulados --saida dados/simulados/figuras
```

Isso gera um sinal **sintético**, fora do Git, e toda figura sai carimbada "DADOS SIMULADOS". Serve só para conferir os scripts; não usar no relatório.

## Documentação

- `docs/hardware.md` — pinagem, endereços e verificação da primeira ligação.
- `docs/relatorio_v1.md` — texto do relatório da entrega V1.
- `docs/relatorio_v2.md` — texto do relatório da entrega V2 (cumulativo).
- `docs/plano_v2.md` — plano original da V2 e o que foi cortado pelo prazo.
- `Entrega v0 ... .pdf` — descrição da aplicação e requisitos (entrega V0).
