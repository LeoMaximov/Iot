# GCP-Esteira — Plano da entrega V2

Entrega V2: análise de dados, IA, tomada de decisão e documentação cumulativa.

> **Situação.** Com o prazo de um dia, o plano abaixo foi reduzido. Feito: serviço `guia/`, modelo FC-velocidade treinado com dataset público do PhysioNet, regras de decisão e de segurança, tela de treino e RPC no painel, e `relatorio_v2.md`. Cortado: correção do BPM e do detector de passadas (etapa 0), classificador de atividade treinado (ficou em regras sobre o SPM), coletas C0 a C3 e a comparação treino guiado × sem guia. O que ficou de fora está na seção 8 do relatório da V2.

## 1 Objetivo

Transformar as medições (BPM, SPM, passos) em um **guia de cardio**: o usuário escolhe o tipo de treino, e o sistema monta e conduz a sessão, mostrando no painel a velocidade e a duração de cada etapa (por exemplo, um HIIT), e ajusta as etapas seguintes conforme a resposta medida.

Isso cobre os requisitos da V0 que ficaram abertos na V1: RF03 (instruções de treino), RF04 (tempo de etapa e meta de SPM), RF06 (plano de treino vindo do ThingsBoard) e RF07 (sinalização de segurança por BPM).

## 2 O que a V1 mostrou e que condiciona o plano

| Fato medido na V1 | Consequência para a V2 |
|---|---|
| BPM instável: erro médio de 16 BPM no T3, leitura válida em 50 a 67% do tempo | O modelo não pode decidir sobre o BPM bruto. Melhorar o BPM é a etapa 0 |
| SPM bom em 140 e 170 (−1,4% e +5,3% na contagem), ruim em 100 (−36%) | Ajustar o detector de passadas antes de usar o SPM em caminhada |
| O sistema não mede nem controla a velocidade da esteira | O guia **recomenda** a velocidade; o usuário ajusta a esteira. A cadência serve para conferir se ele obedeceu |
| Só há um cabo de dados | As coletas de treino usam o painel e o ThingsBoard; o sinal bruto da braçadeira só em coletas separadas |
| T4 não foi feito; dashboard, fotos e campos `[PREENCHER]` pendentes | Entram na documentação cumulativa da V2 |

## 3 Arquitetura

```
Braçadeira --ESP-NOW--> Painel --MQTT--> ThingsBoard <--REST/RPC--> Serviço "guia" (Python, no PC)
                          ^                                              |
                          +------------- etapa atual (RPC) --------------+
```

- **Serviço `guia/` (novo, Python):** lê a telemetria do ThingsBoard, roda os modelos, decide e envia a etapa atual ao painel por RPC. Fica no PC, ao lado do ThingsBoard.
- **Painel:** ganha a tela de treino (etapa, velocidade-alvo, tempo restante, zona de BPM em cor) e os comandos RPC `iniciarTreino`, `definirEtapa` e `encerrarTreino`. Continua funcionando sem o serviço, como na V1.
- **Braçadeira:** só as correções da etapa 0. O pacote ESP-NOW ganha um campo de qualidade do BPM (`PACOTE_VERSAO` 3).
- **ThingsBoard:** dashboard com seletor do tipo de treino, perfil do usuário (atributos) e gráficos de zona e etapas.

O modelo roda no PC, e não na ESP32, porque assim dá para treinar, avaliar e trocar o modelo sem regravar placas, e os dados já estão no ThingsBoard.

## 4 Os três blocos de "inteligência"

### 4.1 Perfil cardíaco do usuário (análise de dados)

- Entradas: idade (digitada), BPM de repouso (medido, 2 min parado) e o teste de calibração da seção 5.
- FC máxima estimada por fórmula (Tanaka: 208 − 0,7 × idade) e zonas pela reserva de FC (Karvonen).
- Saída: faixas de BPM das zonas 1 a 5 para aquele usuário.

### 4.2 Modelo velocidade ↔ resposta (IA: regressão por usuário)

- Treinado com o teste de calibração: para cada velocidade, o BPM e o SPM em regime estável.
- Dois ajustes: `BPM = f(velocidade)`, para escolher a velocidade que leva à zona pedida, e `velocidade = g(SPM)`, para conferir se o usuário está na velocidade recomendada.
- Começar com regressão linear (poucos pontos por usuário) e comparar com um modelo de 1ª ordem com constante de tempo, que prevê quanto tempo o BPM leva para subir e descer. Esse segundo modelo é o que dimensiona os intervalos do HIIT.

### 4.3 Classificador de atividade (IA: aprendizado supervisionado)

- Classes: parado, caminhada, trote, corrida.
- Atributos em janelas de 5 s: SPM, média e desvio da magnitude da aceleração, energia do sinal filtrado.
- Árvore de decisão ou random forest (scikit-learn), validada deixando uma sessão inteira de fora.
- Uso: detectar se o usuário realmente mudou de ritmo quando a etapa mudou, e segurar a contagem da etapa enquanto ele não mudar.

### 4.4 Gerador e condutor de treino (tomada de decisão)

- Tipos de treino como modelos de zonas e tempos: HIIT, contínuo moderado, progressivo e recuperação.
- O gerador traduz zonas em velocidades com o modelo 4.2 e monta a lista de etapas (velocidade, duração, zona-alvo, SPM esperado).
- Durante a sessão, a cada etapa encerrada:
  - BPM acima da zona por mais de N s: reduz a velocidade do próximo tiro ou alonga a recuperação;
  - BPM não chegou à zona: aumenta um passo de velocidade, até o teto calibrado;
  - BPM sem qualidade: mantém o plano e avisa no painel, sem decidir no escuro;
  - BPM acima de 95% da FC máxima: alerta visual e etapa de recuperação imediata (RF07).
- Toda decisão é registrada na telemetria (`decisao`, `motivo`), para os gráficos do relatório.

## 5 Dados novos a coletar

| Coleta | Procedimento | Para quê |
|---|---|---|
| C0 Repouso | 2 min parado, com referência de BPM anotada a cada 20 s | Validar a correção do BPM e obter a FC de repouso |
| C1 Calibração em degraus | 4, 6, 8 e 10 km/h (ou até onde for confortável), 2 a 3 min cada, anotando a velocidade da esteira e o BPM de referência ao fim de cada degrau | Treinar 4.2 e rotular 4.3 |
| C2 Treino guiado | Uma sessão de cada tipo, conduzida pelo sistema | Avaliar a tomada de decisão |
| C3 Treino sem guia | Mesmo treino seguido por cronômetro, sem o sistema | Linha de base para comparar com C2 |

Sempre com referência de BPM medida durante a coleta (relógio ou oxímetro) e com mais de uma pessoa, se possível.

## 6 Como avaliar (resultados do relatório)

- BPM: erro médio absoluto e cobertura contra a referência, antes e depois da etapa 0.
- Modelo 4.2: erro do BPM previsto em um degrau deixado de fora.
- Classificador 4.3: acurácia e matriz de confusão, por sessão deixada de fora.
- Decisão: percentual do tempo dentro da zona-alvo em C2 contra C3, e número e motivo dos ajustes.
- Comunicação: tempo entre a decisão e a etapa aparecer no painel; T4 da V1.

## 7 Etapas, em ordem

| # | Etapa | Depende de hardware |
|---|---|---|
| 0 | Corrigir o BPM (rejeição de valores fora de faixa, mediana, indicador de qualidade) e o detector de passadas em cadência baixa; repetir T1 e T2 a 100 SPM | sim |
| 1 | Tela de treino e RPCs no painel; pacote versão 3 | sim |
| 2 | Serviço `guia/`: leitura da telemetria, perfil e zonas (4.1), tipos de treino e condutor com regras fixas (4.4 sem modelo) | não, testável com dados simulados |
| 3 | Coletas C0 e C1 | sim |
| 4 | Treinar e avaliar 4.2 e 4.3 em notebooks/scripts; ligar os modelos ao condutor | não |
| 5 | Coletas C2 e C3 e análise | sim |
| 6 | Dashboard, `relatorio_v2.md` cumulativo, pendências da V1 (T4, MMA8453Q, fotos) | parcial |

As etapas 2 e 4 podem andar em paralelo com 0 e 1, por pessoas diferentes.

## 8 Riscos e decisões em aberto

- **BPM no braço em movimento.** Se a etapa 0 não levar o erro a um nível utilizável, o condutor decide pela cadência e pelo tempo, e o BPM fica só como alerta de segurança. O plano funciona nos dois casos.
- **Poucos dados.** Com um ou dois usuários, os modelos são pessoais, não gerais. O relatório deve dizer isso.
- **Segurança.** O guia nunca recomenda acima da maior velocidade calibrada nem acima de 95% da FC máxima estimada. É um protótipo acadêmico, não orientação médica.
- **A confirmar com a equipe/professor:** prazo da V2; se a disciplina exige um modelo treinado (o 4.2 e o 4.3 atendem) ou aceita regras; quantas pessoas participam das coletas.
