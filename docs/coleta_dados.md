# Coleta de dados reais e treino do modelo

Este documento descreve como coletar sessões com voluntários na esteira, tratar
os dados e usá-los para treinar o modelo FC = f(velocidade, idade, sexo, peso,
altura) do guia, junto com o dataset público ou no lugar dele.

## Visão geral

```
cadastro (voluntarios.py)
   │
   ▼
coleta (coletar_sessao.py) ── guia ao vivo + serial do painel + exportação do ThingsBoard
   │                           └─> dados/reais/<sessao>/
   ▼
tratamento (preparar_dados_reais.py) ── alinha, limpa, mede qualidade, acha o regime
   │                                     └─> dados/reais/tabela_treino.csv
   ▼
treino (guia/treinar_modelo.py --modo combinado | reais)
   └─> guia/modelo_fc_<modo>.joblib  ── usado pelo guia com --modelo
```

Tudo em `dados/reais/` fica fora do Git (`.gitignore`): são dados de saúde.
Só entram no relatório os gráficos e as métricas agregadas.

## 1. Cadastro dos voluntários (uma vez por pessoa)

Cada pessoa recebe um código (`V01`, `V02`...). O nome e o termo de
autorização ficam com a equipe, fora do projeto. No cadastro vai só a data da
autorização; sem ela, a coleta e o tratamento recusam a pessoa.

```powershell
python tools/voluntarios.py adicionar --id V01 --idade 22 --sexo M --peso 70 --altura 175 --fc-repouso 62 --consentimento 2026-11-22
python tools/voluntarios.py listar
```

`--vel-max` (padrão 12 km/h) limita a velocidade que o guia pode recomendar
para a pessoa. Use um valor menor para quem não corre.

## Teste na esteira (atalho)

`tools/teste_esteira.py` faz as verificações, mostra o plano de velocidades,
pede a confirmação de segurança e roda a coleta completa:

```powershell
python tools/teste_esteira.py --voluntario V01 --porta COM8 --fc-repouso 64 --so-checar
python tools/teste_esteira.py --voluntario V01 --porta COM8 --fc-repouso 64
```

Ele só começa se passar em tudo: credenciais definidas, voluntário com
consentimento, placa certa na porta (painel, não braçadeira), braçadeira
chegando ao painel com contato na pele e BPM válido, e ThingsBoard recebendo
telemetria recente.

O protocolo padrão é `validacao` (17 min, até a zona 3): aquecer 3 min,
leve 4 min, moderado 4 min, leve 3 min, desaquecer 3 min. Use
`--treino progressivo` (21 min, com zona 4) depois de um teste sem problemas.

## 2. Protocolo da sessão

**Treino recomendado:** `progressivo` com `--escala 1.0`
(aquecer 3 min, leve 5 min, moderado 5 min, forte 3 min, desaquecer 3 min).
As etapas longas são as que chegam ao regime; o HIIT tem tiros de 60 s, que
servem para testar o guia, mas quase não geram linhas de treino.

**Por pessoa:** pelo menos 2 sessões, em dias diferentes. Para o modelo,
mais pessoas valem mais que mais sessões da mesma pessoa.

**Antes de sair**
- [ ] Braçadeira e painel gravados; teste de bancada com o dedo no sensor
- [ ] Power bank carregado para a braçadeira; cabo de dados para o painel
- [ ] Celular com hotspot em 2,4 GHz, WPA2; `receptora/secrets.h` com esse Wi-Fi
- [ ] Notebook com o projeto e as dependências (`pip install -r tools/requirements.txt`)
- [ ] Voluntário cadastrado, com a data de autorização

**Na academia, antes do treino**
1. Painel no suporte da esteira, ligado ao notebook pelo cabo de dados
   (para gravar a serial; sem o cabo, a sessão usa só o ThingsBoard).
2. Hotspot ligado perto do painel; confirme o ponto verde do MQTT na tela.
3. Braçadeira no voluntário, com o sensor bem apoiado na pele; confirme o BPM na tela.
4. Voluntário sentado por 3 min; anote a FC de repouso mostrada no painel.
5. No terminal do notebook:

```powershell
$env:TB_URL = "https://thingsboard.cloud"
$env:TB_USUARIO = "email-da-conta"
$env:TB_SENHA = "senha-da-conta"
python tools/coletar_sessao.py --voluntario V01 --treino progressivo --porta COM8 --fc-repouso 64
```

**Durante o treino**
- A velocidade da esteira é ajustada à mão, pelo voluntário ou por quem
  acompanha, seguindo o painel. Se não der para seguir (velocidade
  desconfortável, esteira com outro passo), anote o tempo do guia e a
  velocidade usada: isso vai para `velocidade_real.csv`.
- Se houver contagem manual de pulso (15 s no punho ou no pescoço, vezes 4),
  anote o tempo do guia e o BPM: isso vai para `referencia_manual.csv`.
- Alguém fica ao lado da esteira com acesso ao botão de parada. O alerta do
  painel ("FC ALTA") é sinal para reduzir a velocidade na hora.
- Ctrl+C no terminal encerra o guia; a exportação e o tratamento seguem.

**Depois do treino**
O script exporta o ThingsBoard do período e trata a sessão sozinho. Se houve
anotações, preencha os dois arquivos na pasta da sessão e trate de novo:

```powershell
python tools/preparar_dados_reais.py dados/reais/<sessao>
```

Formato das anotações (o tempo é o do guia, em segundos desde o início). Uma
correção de velocidade vale do instante anotado até o fim daquela etapa:

```
velocidade_real.csv        referencia_manual.csv
t_s,vel,nota               t_s,bpm,nota
185,6.0,esteira não tinha 6.5   300,128,punho
```

## 3. O que fica em cada pasta de sessão

| Arquivo | Origem | Conteúdo |
|---|---|---|
| `metadados.json` | coletar_sessao | voluntário, perfil usado, treino, horários, versão do código |
| `*_guia_*_sessao.csv` | guia | 1 linha/s: etapa, velocidade recomendada, zona, BPM lido, `ts_ms` |
| `*_guia_*_eventos.csv` | guia | etapas, decisões, alertas |
| `*_painel_R.csv`, `_M.csv`, `_meta.json` | serial_logger | pacotes recebidos pelo painel (10 Hz) e publicações MQTT |
| `thingsboard_TB.csv` | tb_export | telemetria guardada na nuvem no período |
| `velocidade_real.csv`, `referencia_manual.csv` | à mão | anotações (podem ficar vazias) |
| `processado/` | preparar_dados_reais | série 1 Hz, etapas, qualidade, gráfico |

## 4. Tratamento

`tools/preparar_dados_reais.py`, para cada sessão:

1. **Alinhamento.** Coloca tudo no tempo do guia, pelo horário absoluto
   (`ts_ms` do guia, `inicio_epoch_ms` da serial, `ts` do ThingsBoard).
2. **Fonte do BPM.** Serial do painel (10 Hz) se existir; senão ThingsBoard;
   senão o que o próprio guia leu.
3. **Limpeza.** Descarta BPM sem contato, fora de 40–220 ou travado no mesmo
   valor por 15 s. As lacunas não são preenchidas.
4. **Qualidade.** Cobertura de BPM válido, perda de pacotes, latência, RSSI,
   lacunas, alertas e decisões do guia (`processado/qualidade.json`).
5. **Regime.** Em cada etapa, usa só o trecho depois do primeiro minuto. A
   etapa entra no treino se durar 120 s ou mais, tiver velocidade constante,
   BPM válido em 60% do trecho, cadência de quem está se movendo e deriva
   menor que 8 BPM/min. Recuperação, desaquecimento e alerta ficam de fora.
   O motivo de cada exclusão fica em `etapas_todas.csv`.

Saídas consolidadas em `dados/reais/`: `tabela_treino.csv`,
`etapas_todas.csv` e `qualidade_sessoes.csv`.

## 5. Treino

```powershell
python guia/treinar_modelo.py --modo combinado     # público + correção pelas coletas (recomendado)
python guia/treinar_modelo.py --modo reais         # só coletas (com muitos voluntários)
```

- **Combinado:** mantém o modelo do dataset público (que dá a forma da curva
  FC x velocidade) e ajusta nas coletas só uma correção: uma constante, ou
  uma reta na velocidade. São 1 ou 2 números, que poucas pessoas conseguem
  estimar. Misturar as linhas das coletas no meio das 20 mil do dataset não
  funciona: elas quase não pesam.
- **Limite de segurança:** a correção pode aumentar a FC prevista sem limite
  (o guia fica mais lento), mas só pode reduzi-la até 10 BPM, porque uma FC
  prevista menor faz o guia recomendar velocidades maiores.
- **Reais:** treina do zero só com as coletas. Só faz sentido com muitos
  voluntários e velocidades variadas.
- **Avaliação:** um voluntário fica fora do treino por vez e o erro é medido
  nas etapas dele. Com um único voluntário, sai uma sessão por vez (mede a
  repetição entre dias da mesma pessoa, não o erro para alguém novo). A
  comparação principal é com o modelo atual (só público).
- **Escolha:** o candidato com menor erro nessa avaliação.
- **Margem inicial:** medida pelo viés fora da amostra (entre 0 e 20 BPM),
  em vez dos 20 BPM fixos do modelo público.
- **Saída:** `guia/modelo_fc_<modo>.joblib`, figura e métricas em
  `docs/figuras/v3_modelo_fc_<modo>*`. O modelo atual não é sobrescrito.

Para usar o modelo novo numa sessão:

```powershell
python tools/coletar_sessao.py --voluntario V02 --modelo guia/modelo_fc_combinado.joblib
```

Só troque o padrão do guia depois que o modelo novo tiver erro menor que o
atual em voluntários fora do treino.

## 6. Limitações

- **Sem referência independente.** O alvo do treino é o BPM da braçadeira
  tratado. O modelo aprende também os erros sistemáticos dela. As contagens
  manuais de pulso dão só uma estimativa grosseira desse erro.
- **Velocidade.** O painel não controla a esteira; a velocidade real é a do
  guia, a menos que seja anotada. Velocidades erradas e não anotadas viram
  ruído no treino.
- **Poucas pessoas.** Com menos de 5 voluntários, as métricas são indicação,
  não resultado.
- **Calor, cafeína, sono e hora do dia** mudam a FC. Anote em `--nota`.

## 7. Dados pessoais

- Só o código do voluntário entra nos arquivos.
- `dados/reais/` não vai para o Git nem para o relatório.
- A telemetria passa pelo ThingsBoard Cloud (região América do Norte) e é
  apagada por lá em 30 dias.
- Se um voluntário pedir a exclusão, apague as pastas de sessão dele e a linha
  do cadastro, rode o tratamento de novo e treine outra vez.

## Ensaio sem hardware

Para testar o fluxo inteiro sem esteira, painel ou nuvem (usuário simulado; a
sessão fica marcada como ENSAIO e não entra no treino):

```powershell
python tools/coletar_sessao.py --voluntario V01 --ensaio --escala 0.2
```
