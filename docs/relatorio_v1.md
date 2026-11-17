# GCP-Esteira — Entrega V1: coleta de dados, comunicação IoT e documentação inicial

Universidade Federal do Ceará — Departamento de Engenharia de Teleinformática — Engenharia de Computação — Internet das Coisas

Klaiver Santos Diogo, Lucas Ferreira da Rocha e Leonardo Máximo Viana

> **Como usar este arquivo.** É o texto do relatório, para colar no modelo da UFC usado na V0. Os trechos marcados com **[PREENCHER]** dependem dos testes no hardware e ainda não têm valor. As seções 1 a 5 descrevem o que foi projetado e implementado; a seção 6 só pode ser escrita depois das coletas. Apague este aviso ao final.

## Histórico de revisões

| Data | Versão | Descrição | Responsável |
|---|---|---|---|
| 08/09 | 1 | Descrição informal (entrega V0) | Lucas Ferreira |
| **[PREENCHER]** | 2 | Coleta de dados, comunicação MQTT e documentação inicial (entrega V1) | **[PREENCHER]** |

## 1 Objetivo da entrega

A V0 descreveu a aplicação e seus requisitos. Esta entrega mostra o sistema coletando dados reais: os sensores da braçadeira são lidos, os dados atravessam os dois enlaces sem fio (ESP-NOW e MQTT) e ficam armazenados no ThingsBoard, com gráficos gerados a partir das coletas feitas nos testes.

Requisitos da V0 tratados nesta entrega: RF01 (cadência), RF02 (frequência cardíaca), RF05 (comunicação local ESP-NOW), RF06 (integração com o ThingsBoard, sentido painel → nuvem e comando RPC) e RNF01 (latência local inferior a 100 ms).

## 2 Hardware utilizado

| Módulo | Componentes |
|---|---|
| Braçadeira | ESP32, sensor óptico MAX30102 (PPG), acelerômetro MMA8452Q, barramento I2C nos GPIO 21 (SDA) e 22 (SCL) |
| Painel | ESP32, display TFT ST7789, backlight no GPIO 4, alimentação USB |
| Servidor | ThingsBoard CE 4.3 em Docker, em um computador na mesma rede Wi-Fi do painel |

**Alteração em relação à V0.** A V0 previa o MPU6050 (acelerômetro e giroscópio). O protótipo usa o acelerômetro MMA8452Q, de três eixos e 12 bits. A cadência é calculada pela magnitude da aceleração, que não depende de giroscópio, de modo que o RF01 continua atendido com esse sensor.

A pinagem completa, os endereços I2C e os endereços MAC estão em `docs/hardware.md`. **[PREENCHER: inserir fotos da braçadeira e do painel.]**

## 3 Coleta de dados nos sensores

### 3.1 Cadência (SPM)

O acelerômetro é configurado em ±8 g e lido a 50 Hz. Para cada amostra o firmware:

1. calcula a magnitude da aceleração, `sqrt(ax² + ay² + az²)`, que não depende da orientação da braçadeira no braço;
2. remove a gravidade subtraindo uma média lenta da magnitude (constante de tempo de cerca de 1 s);
3. suaviza o resultado com um filtro passa-baixa de cerca de 5 Hz;
4. conta uma passada quando o sinal cruza um limiar para cima, respeitando um intervalo mínimo de 250 ms entre passadas e exigindo que o sinal caia abaixo da metade do limiar antes de contar a próxima.

O SPM é 60 000 dividido pela média dos últimos seis intervalos entre passadas, em milissegundos. Depois de 3 s sem passada o SPM volta a zero.

No código recebido a contagem era feita no painel, sobre as amostras que chegavam por rádio a 10 Hz, e não havia cálculo de SPM. A detecção foi movida para a braçadeira porque 10 Hz dá cerca de três amostras por passada a 180 SPM, pouco para localizar o pico.

### 3.2 Frequência cardíaca (BPM)

O MAX30102 amostra o canal infravermelho a 100 Hz. Cada amostra passa pelo detector de batimentos da biblioteca SparkFun MAX3010x (`checkForBeat`). O intervalo entre dois batimentos dá o BPM instantâneo; o valor enviado é a média dos quatro últimos instantâneos que estiverem entre 40 e 220 BPM. Se o nível do infravermelho indicar que o sensor não está encostado na pele, ou se passarem 3 s sem batimento, o BPM é zerado e o painel mostra "SEM CONTATO" ou "ANALISANDO...".

### 3.3 Registro dos dados

O firmware imprime os dados na serial em linhas CSV, gravadas no computador pelo script `tools/serial_logger.py`:

| Registro | Origem | Taxa | Conteúdo |
|---|---|---|---|
| E | braçadeira | 50 Hz | aceleração nos três eixos, magnitude, sinal filtrado, passada detectada, SPM, amostra do PPG, batimento detectado, BPM |
| R | painel | 10 Hz | cada pacote recebido, com número de sequência, RSSI, pacotes perdidos e latência |
| M | painel | 1 Hz | sucesso ou falha de cada publicação MQTT |

Os mesmos dados que chegam ao ThingsBoard são baixados pela API REST com `tools/tb_export.py`, e `tools/plot_coleta.py` gera os gráficos e um resumo numérico de cada coleta.

## 4 Comunicação IoT

### 4.1 Arquitetura

```
Braçadeira (ESP32) --ESP-NOW, 10 pacotes/s--> Painel (ESP32) --MQTT sobre Wi-Fi, 1 msg/s--> ThingsBoard
```

O protocolo de aplicação escolhido para a nuvem é o **MQTT**. O painel é o cliente MQTT; a braçadeira não usa Wi-Fi nem IP, apenas ESP-NOW, o que reduz consumo e dispensa roteador entre os dois nós.

### 4.2 Enlace local ESP-NOW

A braçadeira envia ao MAC do painel um pacote binário de 46 bytes a cada 100 ms, com confirmação na camada MAC.

| Campo | Tipo | Significado |
|---|---|---|
| `versao` | uint8 | versão do formato |
| `seq` | uint32 | número de sequência, para medir perda |
| `t_ms` | uint32 | relógio da braçadeira no envio |
| `bpm` | float | frequência cardíaca média; 0 = sem leitura |
| `irValue` | int32 | última amostra bruta do PPG |
| `ax`, `ay`, `az` | float | aceleração em g |
| `spm` | float | cadência; 0 = parado |
| `passos` | uint32 | passadas acumuladas |
| `latencia_us` | uint32 | latência medida no pacote anterior |
| `falhas_envio` | uint32 | envios sem confirmação |
| `contato` | uint8 | sensor óptico em contato com a pele |

Dois cuidados foram necessários para os dois protocolos conviverem no mesmo rádio do painel:

- **Canal.** Conectado ao Wi-Fi, o painel fica preso ao canal do roteador. A braçadeira não conhece esse canal; quando passa 2 s sem confirmação, ela testa um canal diferente a cada envio até a confirmação voltar.
- **Economia de energia do Wi-Fi.** O modo de economia do rádio do painel foi desligado, porque com ele ativo o receptor dorme entre os beacons do roteador e perde pacotes ESP-NOW.

**Medição de latência.** A braçadeira mede o tempo entre a chamada de envio e a chegada da confirmação do painel e envia esse valor no pacote seguinte. Esse tempo inclui a ida do pacote e a volta da confirmação, portanto é um limite superior para o tempo de entrega. Ele não inclui o tempo que o painel leva para redesenhar o display, que é atualizado a cada 250 ms.

### 4.3 Enlace com a nuvem (MQTT)

| Item | Valor |
|---|---|
| Broker | ThingsBoard CE, porta 1883 |
| Autenticação | token de acesso do dispositivo como usuário MQTT |
| Tópico de telemetria | `v1/devices/me/telemetry` |
| Tópico de comandos | `v1/devices/me/rpc/request/+`, com resposta em `v1/devices/me/rpc/response/<id>` |
| Período de publicação | 1 s |
| Formato | JSON |

Exemplo de mensagem publicada:

```json
{"enlace":true,"bpm":92.5,"spm":141.2,"ir":108345,"contato":true,"rssi":-54,
 "latencia_ms":1.84,"falhas_envio":0,"passos":312,"pacotes_rx":2210,
 "pacotes_perdidos":7,"perda_pct":0.3,"concluido":false}
```

Quando a braçadeira fica mais de 1 s sem enviar pacotes, o painel publica `"enlace": false` e omite os valores dos sensores, para não gravar leituras antigas como se fossem novas.

No sentido contrário, o ThingsBoard envia os comandos RPC `concluirTreino` e `reiniciarTreino`, que mudam a tela do painel. A conexão Wi-Fi e a conexão MQTT são refeitas sem travar o laço principal, de modo que o display e o ESP-NOW continuam funcionando quando a rede cai.

## 5 Metodologia dos testes

| Teste | Procedimento | O que avalia |
|---|---|---|
| T1 Repouso | 2 min sentado, braçadeira no braço. Referência: **[PREENCHER: oxímetro / relógio / pulso contado em 60 s]** | RF02: erro do BPM |
| T2 Cadência controlada | Passadas no ritmo de um metrônomo em 100, 140 e 170 SPM, 60 s em cada, com contagem manual dos passos | RF01: erro do SPM e da contagem |
| T3 Esteira | 2 min 20 s de registro efetivo (de uma gravação de 4 min, cortada após o fim da atividade) de caminhada, trote e caminhada, com o sistema completo. Duração abaixo dos 5 a 10 min previstos inicialmente | RF01, RF02 e RF06 em uso real |
| T4 Comunicação | 5 min de registro com a braçadeira a 1 m e a 3 m do painel | RF05 e RNF01: latência e perda; RF06: taxa de publicação |

Condições dos testes: **[PREENCHER: data, local, esteira, rede Wi-Fi usada, quem vestiu a braçadeira]**.

## 6 Resultados

> Esta seção depende das coletas. Para cada teste, `tools/plot_coleta.py` gera as figuras em `docs/figuras/` e um arquivo `_resumo.md` com os números das tabelas abaixo.

### 6.1 Sinais brutos

**[PREENCHER: Figura — aceleração e passadas detectadas (T2, 140 SPM), arquivo `*_aceleracao.png`.]**

**[PREENCHER: Figura — sinal PPG e batimentos detectados (T1), arquivo `*_ppg.png`.]**

**[PREENCHER: comentar o que se vê: forma do sinal, se cada passada gera um pico, ruído de movimento no PPG.]**

### 6.2 Frequência cardíaca

**[PREENCHER: Figura — BPM no tempo contra a referência (T1), arquivo `*_bpm.png`.]**

| Teste | BPM de referência | BPM medido (média ± desvio) | Erro médio absoluto |
|---|---|---|---|
| T1 Repouso | **[PREENCHER]** | **[PREENCHER]** | **[PREENCHER]** |
| T3 Esteira | 80 (valor único, medido durante o teste) | 81,1 ± 20,7 | 16,3 (20,4 %) |

No T3, o painel tinha leitura de BPM válida em 67% das amostras (941 de 1401). A referência é um único valor, de modo que o erro médio absoluto mede a diferença média por amostra em relação a esse valor, e não a precisão ao longo do teste. Por isso, o resultado é tratado como indicativo.

### 6.3 Cadência

**[PREENCHER: Figura — SPM no tempo contra o metrônomo (T2), arquivo `*_spm.png`.]**

| Metrônomo (SPM) | SPM medido (média ± desvio) | Erro médio absoluto | Passos contados à mão | Passos contados pelo sistema |
|---|---|---|---|---|
| 100 | 85,3 ± 24,0 | 24,4 (24,4 %) | 100 | 64 |
| 140 | 150,7 ± 38,1 | 31,9 (22,8 %) | 140 | 138 |
| 170 | 192,9 ± 14,3 | 24,5 (14,4 %) | 170 | 179 |

Erro médio absoluto calculado sobre as amostras com SPM maior que zero, frente à cadência do metrônomo. Amostras zeradas (mais de 3 s sem passada) ficam fora da média. Em 100 SPM, o sistema contou 36% a menos que a contagem manual; em 140 e 170 SPM, a diferença ficou em −1,4% e +5,3%. O resultado de 100 SPM precisa ser discutido no texto, junto com a hipótese de limiar fixo do detector.

No T3 (esteira, 2 min 20 s efetivos), o sistema contou 406 passos, igual à contagem manual. O SPM medido ficou em 193,9 ± 25,6. Esse teste não tem metrônomo, então não entra na coluna de erro de cadência.

### 6.4 Comunicação

**[PREENCHER: Figura — histograma de latência do ESP-NOW (T4), arquivo `*_latencia.png`.]**

**[PREENCHER: Figura — RSSI, perda e intervalo entre pacotes (T4), arquivo `*_enlace.png`.]**

| Distância | Latência mediana / p95 / máxima | Pacotes abaixo de 100 ms | Perda de pacotes | RSSI médio | Publicações MQTT com sucesso |
|---|---|---|---|---|---|
| 1 m | **[PREENCHER]** | **[PREENCHER]** | **[PREENCHER]** | **[PREENCHER]** | **[PREENCHER]** |
| 3 m | **[PREENCHER]** | **[PREENCHER]** | **[PREENCHER]** | **[PREENCHER]** | **[PREENCHER]** |

### 6.5 Dados na nuvem

**[PREENCHER: Figura — captura do dashboard do ThingsBoard durante o T3.]**

**Figura — séries exportadas do ThingsBoard durante o T3, arquivo `docs/figuras/20261115_214759_T3_efetivo_thingsboard.png`.** Ao todo, 140 pontos foram armazenados no intervalo, todos com `enlace = 1`, sem perda de publicações MQTT.

## 7 Situação dos requisitos

| ID | Requisito | Situação na V1 |
|---|---|---|
| RF01 | Medição de cadência (SPM) | Implementado na braçadeira. **[PREENCHER: validado em T2 com erro de ...]** |
| RF02 | Medição de frequência cardíaca | Implementado. **[PREENCHER: validado em T1 com erro de ...]** |
| RF03 | Instruções qualitativas de treino | Não iniciado. Previsto para a próxima entrega |
| RF04 | Métricas complementares no visor | Parcial: o visor mostra BPM, SPM e passos. Faltam tempo de etapa, meta de SPM e metrônomo visual |
| RF05 | Comunicação local ESP-NOW | Implementado. **[PREENCHER: latência medida em T4]** |
| RF06 | Integração com o ThingsBoard | Parcial: telemetria por MQTT e comandos RPC funcionando. Falta receber as metas do plano de treino |
| RF07 | Sinalização visual de segurança | Não iniciado |
| RNF01 | Latência local inferior a 100 ms | **[PREENCHER: atendido / não atendido, com o valor de T4]** |
| RNF02 | Autonomia de 2 h | Não avaliado |
| RNF03 | Ausência de cabos externos | **[PREENCHER: conforme a montagem recebida]** |
| RNF04 | Leitura rápida no visor | Parcial: valores em fonte grande; cores temáticas por instrução dependem do RF03 |
| RNF05 | Baixo custo | Atendido pelo uso de ESP32 e sensores de prateleira |

## 8 Problemas encontrados e correções

Problemas identificados pela leitura do código recebido, antes dos testes:

1. **Canal do ESP-NOW.** A braçadeira transmitia sempre no canal 1, mas o painel, ao conectar ao Wi-Fi, passa a usar o canal do roteador. Fora do canal 1 nenhum pacote chegaria. Corrigido com a busca de canal descrita em 4.2.
2. **Bloqueio na conexão Wi-Fi.** O painel esperava o Wi-Fi em um laço sem saída; sem a rede, o visor nunca funcionava. Corrigido com reconexão sem bloqueio.
3. **Escala do acelerômetro.** A conversão do código recebido dividia por 1024 um valor que, pela folha de dados do MMA8452Q, tem 4096 contagens por g na escala usada, o que daria cerca de 4 g em repouso em vez de 1 g. A conversão foi refeita. **[PREENCHER: confirmar com a "Magnitude em repouso" impressa pela braçadeira.]**
4. **Faixa do acelerômetro.** A escala de ±2 g satura na corrida; foi alterada para ±8 g.
5. **Credenciais no código.** Nome e senha do Wi-Fi e token do ThingsBoard estavam no código-fonte publicado. Foram movidos para um arquivo local fora do controle de versão.

**[PREENCHER: problemas encontrados durante os testes no hardware e como foram resolvidos.]**

## 9 Próximos passos

- Instruções qualitativas de treino no visor (RF03) e sinalização de segurança por BPM (RF07).
- Tempo de etapa, meta de SPM e metrônomo visual (RF04).
- Recebimento do plano de treino pelo ThingsBoard (RF06).
- Medição da autonomia da bateria (RNF02).

## Referências

RUI SANTOS. Servidor Web ESP32 com Acelerômetro e Giroscópio MPU-6050. YouTube. Disponível em: https://www.youtube.com/watch?v=dXcF-Uqa-gw. Acesso em: 8 set. 2026.

JULIA LABS. Você NÃO precisa de Wi-Fi! Use ESP-NOW no ESP32. YouTube. Disponível em: https://www.youtube.com/watch?v=DTTXOUSc6gQ. Acesso em: 8 set. 2026.

THINGSBOARD. MQTT Device API Reference. Disponível em: https://thingsboard.io/docs/reference/mqtt-api/. Acesso em: 13 nov. 2026.

THINGSBOARD. Installing ThingsBoard CE using Docker (Windows). Disponível em: https://thingsboard.io/docs/user-guide/install/docker-windows/. Acesso em: 13 nov. 2026.

**[PREENCHER: folhas de dados do MAX30102 e do MMA8452Q consultadas e a página da biblioteca SparkFun MAX3010x.]**
