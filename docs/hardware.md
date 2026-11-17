# Hardware do GCP-Esteira

Este documento separa o que foi **lido no código** do que ainda precisa ser **conferido na bancada**. Preencha os campos marcados com `?` na primeira vez que ligar as placas e apague esta frase quando terminar.

## Braçadeira (emissora)

| Item | Valor | Origem |
|---|---|---|
| Microcontrolador | ESP32 (modelo da placa: `?`) | conferir |
| MAC | `68:09:47:9E:0C:C4` | serial da emissora, primeira gravação (com o MAC lido pela eFuse) |
| I2C SDA / SCL | GPIO 21 / GPIO 22 | `emissora.ino` |
| Sensor óptico | MAX30102, endereço I2C `0x57` | confirmado na varredura |
| Acelerômetro | **MMA8453Q**, endereço `0x1C` | confirmado na varredura e no WHO_AM_I |
| WHO_AM_I do acelerômetro | `0x3A` (MMA8453Q; o MMA8452Q responderia `0x2A`) | serial da emissora ao ligar |
| Magnitude em repouso | `1,02 g` (com a placa parada sobre a mesa) | serial da emissora ao ligar |
| Alimentação | `?` (bateria LiPo? capacidade?) | conferir |

### Divergência em relação à V0

O documento da V0 cita o acelerômetro/giroscópio **MPU6050**. O circuito montado usa o acelerômetro **MMA8453Q** (3 eixos, 10 bits, sem giroscópio), identificado pelo WHO_AM_I `0x3A`. O firmware aceita também o MMA8452Q (`0x2A`) e o MMA8451Q (`0x1A`), que são da mesma família de registradores. Para contar passadas pela magnitude da aceleração o giroscópio não é necessário, então a troca não afeta o RF01.

### Configuração dos sensores no firmware

- MMA8452Q: escala ±8 g, taxa de saída 100 Hz, lido a 50 Hz. Os dados vêm alinhados à esquerda em 16 bits; em ±8 g isso dá 4096 contagens por g.
- MAX30102: configuração padrão da biblioteca SparkFun (400 amostras/s com média de 4, ou seja, 100 Hz efetivos), LED vermelho em amplitude baixa.

## Painel (receptora)

| Item | Valor | Origem |
|---|---|---|
| Microcontrolador | ESP32 (modelo da placa: `?`) | conferir |
| MAC | `?` (o código antigo esperava `E0:5A:1B:4A:A6:18`) | serial da receptora ao ligar |
| Display | TFT com controlador ST7789 | documento da V0 |
| Resolução | `?` (a interface indica 240x240) | conferir no módulo |
| Backlight (BLK) | GPIO 4 (D4) | `receptora.ino`; fio conferido na montagem |
| SDA (MOSI) | GPIO 23 (D23) | fio conferido na montagem |
| SCK (SCLK) | GPIO 18 (D18) | fio conferido na montagem |
| DC | GPIO 16 (RX2) | fio conferido na montagem |
| RES (RST) | GPIO 17 (TX2) | fio conferido na montagem |
| CS | não ligado (`-1`) | fio conferido na montagem; se a tela não responder, ligar o CS ao GND do módulo, se houver |
| VCC / GND | 3V3 / GND | fio conferido na montagem |
| MOSI / SCLK / CS / DC / RST | `?` / `?` / `?` / `?` / `?` | seguir os fios do display |
| Alimentação | USB | documento da V0 |

Depois de preencher a pinagem do display, copie-a para `docs/TFT_eSPI_User_Setup.h`.

## Lista de verificação da primeira ligação

1. Ligar a braçadeira no USB e abrir a serial a 115200 baud.
   - `Dispositivos I2C:` deve listar `0x57` e `0x1C` (ou `0x1D`). Se faltar um, o problema é de fiação ou alimentação do sensor.
   - `Magnitude em repouso` deve ficar perto de 1,00 g com a placa parada.
   - Anotar o `MAC desta placa (emissora)`.
2. Ligar o painel no USB.
   - Anotar o `MAC desta placa (receptora)`. Se for diferente de `MAC_RECEPTORA` em `emissora.ino`, corrigir e regravar a emissora.
   - O display deve mostrar a tela com as duas molduras. Tela branca ou apagada indica pinagem errada em `User_Setup.h`.
3. Com as duas ligadas, o círculo da esquerda na barra de título do painel fica verde quando chegam pacotes da braçadeira; o da direita fica verde quando o MQTT está conectado.

## Fotos

Colocar aqui as fotos da montagem (`docs/figuras/foto_bracadeira.jpg`, `docs/figuras/foto_painel.jpg`).
