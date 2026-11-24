"""Treina o modelo FC = f(velocidade, idade, sexo, peso, altura) do guia de cardio.

Fontes de dados:
  publico   "Treadmill Maximal Exercise Tests from the Exercise Physiology and Human
            Performance Lab of the University of Malaga", PhysioNet, versão 1.0.1
            (https://physionet.org/content/treadmill-exercise-cardioresp/1.0.1/).
            992 testes incrementais em esteira de 857 pessoas. Os arquivos
            subject-info.csv e test_measure.csv ficam em dados/externos/treadmill/
            (fora do Git).
  reais     coletas do próprio projeto, tratadas por tools/preparar_dados_reais.py
            (dados/reais/tabela_treino.csv): uma linha por etapa em regime, com a
            mediana do BPM da braçadeira na velocidade da esteira.

Modos (--modo):
  publico    só o dataset público (comportamento original).
  combinado  modelo do dataset público + uma correção ajustada nas coletas:
             constante, ou linear na velocidade. São 1 ou 2 parâmetros, que poucas
             pessoas conseguem estimar; o público continua dando a forma da curva.
  reais      modelo treinado só com as coletas (precisa de vários voluntários).

Avaliação com as coletas: deixa um voluntário de fora por vez (o modelo não vê
nenhuma linha dele e é testado nas etapas dele), que é o erro esperado para um
usuário novo. Com um único voluntário, deixa uma sessão de fora por vez, o que
mede só a repetição entre dias da mesma pessoa. A comparação principal é com o
modelo atual do guia (só público).

A margem inicial do guia (BPM somados à previsão antes de medir o usuário) vale
20 BPM para o modelo só do público. Nos outros modos ela é medida: é a mediana do
erro fora da amostra nas coletas, limitada entre 0 e 20 BPM.

Uso:
  python guia/treinar_modelo.py
  python guia/treinar_modelo.py --modo combinado
  python guia/treinar_modelo.py --modo reais
"""

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
from nucleo import MARGEM_INICIAL, ModeloCorrigido  # noqa: E402

COLUNAS = ["vel", "idade", "sexo_m", "peso", "altura"]
FONTE_PUBLICO = "PhysioNet treadmill-exercise-cardioresp 1.0.1"
INCLINACAO_MAX = 5.0   # BPM por km/h: limite da correção linear (evita extrapolação)
# Segurança: uma correção negativa faz o guia recomendar velocidades MAIORES. Ela é
# limitada a -10 BPM em toda a faixa de 3 a 12 km/h; para cima não há limite
# (guia mais lento é o lado seguro do erro).
CORRECAO_MIN = -10.0
FAIXA_VEL = (3.0, 12.0)

CANDIDATOS = {
    "regressão linear": LinearRegression,
    # FC não decresce com a velocidade: a restrição mantém a inversão bem definida.
    "gradient boosting monotônico": lambda: HistGradientBoostingRegressor(
        monotonic_cst=[1, 0, 0, 0, 0], max_iter=300, learning_rate=0.06, random_state=0),
}
MODELO_PUBLICO = "gradient boosting monotônico"   # o que o guia usa hoje
CORRECOES = {"público + correção constante": "constante",
             "público + correção por velocidade": "linear"}


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------
def prepara(pasta: Path):
    medidas = pd.read_csv(pasta / "test_measure.csv")
    pessoas = pd.read_csv(pasta / "subject-info.csv")
    medidas = medidas.dropna(subset=["HR", "Speed"])
    medidas = medidas[medidas["HR"].between(40, 220) & medidas["Speed"].between(3, 22)]

    # Só a fase de esforço: do início até o instante da velocidade máxima do teste.
    t_pico = medidas.loc[medidas.groupby("ID_test")["Speed"].idxmax(), ["ID_test", "time"]]
    medidas = medidas.merge(t_pico.rename(columns={"time": "t_pico"}), on="ID_test")
    medidas = medidas[medidas["time"] <= medidas["t_pico"]]

    medidas["vel"] = (medidas["Speed"] * 2).round() / 2
    por_vel = (medidas.groupby(["ID_test", "vel"])
               .agg(fc=("HR", "median"), n=("HR", "size")).reset_index())
    por_vel = por_vel[por_vel["n"] >= 5]

    dados = por_vel.merge(pessoas, on="ID_test")
    dados = dados.rename(columns={"Age": "idade", "Weight": "peso", "Height": "altura"})
    dados["sexo_m"] = 1 - dados["Sex"]   # no dataset, Sex = 1 é feminino
    dados = dados.dropna(subset=COLUNAS + ["fc"])
    dados = dados[dados["idade"].between(10, 80) & dados["peso"].between(30, 150)
                  & dados["altura"].between(120, 215)]
    return dados.reset_index(drop=True)


def prepara_reais(caminho: Path):
    if not caminho.exists():
        sys.exit(f"{caminho} não existe. Rode tools/preparar_dados_reais.py depois das coletas.")
    dados = pd.read_csv(caminho).dropna(subset=COLUNAS + ["fc"])
    if dados.empty:
        sys.exit(f"{caminho} não tem etapas em regime. Veja os motivos em etapas_todas.csv.")
    return dados.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Avaliação
# ---------------------------------------------------------------------------
def metricas(nome, real, previsto, grupos=None):
    real = np.asarray(real, dtype=float)
    ok = ~np.isnan(previsto)
    m = {"modelo": nome, "n": int(ok.sum()),
         "mae": float(mean_absolute_error(real[ok], previsto[ok])) if ok.any() else None,
         "r2": float(r2_score(real[ok], previsto[ok])) if ok.sum() > 2 else None,
         "vies": float(np.mean(real[ok] - previsto[ok])) if ok.any() else None}
    if grupos is not None:
        erro = pd.Series(np.abs(real - previsto))
        m["por_grupo"] = {str(g): float(e) for g, e in
                          erro.groupby(np.asarray(grupos)).mean().dropna().items()}
    return m


def avalia_publico(nome, fabrica, dados):
    """MAE e R² fora da amostra, com as pessoas separadas entre treino e teste."""
    previsto = np.zeros(len(dados))
    for treino, teste in GroupKFold(n_splits=5).split(dados, groups=dados["ID"]):
        modelo = fabrica().fit(dados.iloc[treino][COLUNAS], dados.iloc[treino]["fc"])
        previsto[teste] = modelo.predict(dados.iloc[teste][COLUNAS])
    return metricas(nome, dados["fc"], previsto), previsto


def linha_de_base_publico(dados):
    """Referência sem aprendizado: FC mediana de cada velocidade, medida fora da amostra."""
    previsto = np.zeros(len(dados))
    for treino, teste in GroupKFold(n_splits=5).split(dados, groups=dados["ID"]):
        tabela = dados.iloc[treino].groupby("vel")["fc"].median()
        previsto[teste] = dados.iloc[teste]["vel"].map(tabela).fillna(tabela.median())
    return metricas("mediana por velocidade", dados["fc"], previsto)


def fora_da_amostra(prever, reais, grupos):
    """Para cada grupo, prever(treino, teste) com o grupo fora do treino."""
    previsto = np.full(len(reais), np.nan)
    for g in pd.unique(grupos):
        teste = np.asarray(grupos == g)
        if teste.all():
            continue
        previsto[teste] = prever(reais[~teste], reais[teste])
    return previsto


def ajusta_correcao(residuos: pd.DataFrame, tipo):
    """(intercepto, inclinação) da correção, a partir das colunas vel e residuo."""
    if tipo == "constante" or residuos["vel"].nunique() < 2:
        intercepto, inclinacao = float(residuos["residuo"].median()), 0.0
    else:
        lr = LinearRegression().fit(residuos[["vel"]], residuos["residuo"])
        inclinacao = float(np.clip(lr.coef_[0], -INCLINACAO_MAX, INCLINACAO_MAX))
        intercepto = float(np.median(residuos["residuo"] - inclinacao * residuos["vel"]))
    menor = min(intercepto + inclinacao * v for v in FAIXA_VEL)
    if menor < CORRECAO_MIN:
        intercepto += CORRECAO_MIN - menor
    return intercepto, inclinacao


# ---------------------------------------------------------------------------
# Figuras
# ---------------------------------------------------------------------------
def figura_publico(dados, previsto, modelo, saida: Path):
    import matplotlib.pyplot as plt
    import plot_coleta as estilo

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.6))
    g = dados.groupby("vel")["fc"]
    vel = g.median().index.to_numpy()
    a1.fill_between(vel, g.quantile(0.25), g.quantile(0.75), color=estilo.AZUL, alpha=0.15,
                    linewidth=0, label="Dados: 25% a 75%")
    a1.plot(vel, g.median(), color=estilo.AZUL, label="Dados: mediana")
    ref = pd.DataFrame({"vel": vel, "idade": 22, "sexo_m": 1, "peso": 70, "altura": 175})
    a1.plot(vel, modelo.predict(ref[COLUNAS]), color=estilo.LARANJA, linestyle=(0, (4, 3)),
            label="Modelo: homem, 22 anos, 70 kg")
    a1.set_xlabel("Velocidade (km/h)")
    a1.set_ylabel("FC (BPM)")
    a1.set_title("FC por velocidade no dataset", pad=38)
    a1.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=2, fontsize=8, borderaxespad=0.2)

    erro = (pd.Series(previsto, index=dados.index) - dados["fc"]).abs().groupby(dados["vel"]).mean()
    a2.bar(erro.index, erro.values, width=0.4, color=estilo.AZUL)
    a2.set_xlabel("Velocidade (km/h)")
    a2.set_ylabel("Erro médio absoluto (BPM)")
    a2.set_title("Erro fora da amostra, por velocidade", pad=38)
    fig.tight_layout()
    saida.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(saida, bbox_inches="tight")
    plt.close(fig)
    print(f"Figura: {saida}")


def figura_reais(reais, previsto_novo, previsto_publico, nome_novo, grupos, rotulo_grupo,
                 saida: Path):
    import matplotlib.pyplot as plt
    import plot_coleta as estilo

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.8))
    ok = ~np.isnan(previsto_novo)
    if previsto_publico is not None:
        a1.scatter(reais["fc"], previsto_publico, s=18, marker="x", color=estilo.APAGADO,
                   label="Só dataset público (atual)")
    a1.scatter(reais.loc[ok, "fc"], previsto_novo[ok], s=22, color=estilo.AZUL, label=nome_novo)
    limites = [reais["fc"].min() - 10, reais["fc"].max() + 10]
    a1.plot(limites, limites, color=estilo.TINTA, linewidth=0.8, linestyle=(0, (4, 3)))
    a1.set_xlabel("FC medida em regime (BPM)")
    a1.set_ylabel("FC prevista fora da amostra (BPM)")
    a1.set_title("Previsto x medido nas coletas", pad=38)
    a1.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=1, fontsize=8, borderaxespad=0.2)

    nomes = [str(g) for g in pd.unique(grupos)]
    x = np.arange(len(nomes))
    largura = 0.4 if previsto_publico is not None else 0.6
    novo = metricas("", reais["fc"], previsto_novo, grupos)["por_grupo"]
    a2.bar(x - (0.2 if previsto_publico is not None else 0), [novo.get(n, np.nan) for n in nomes],
           width=largura, color=estilo.AZUL, label=nome_novo)
    if previsto_publico is not None:
        pub = metricas("", reais["fc"], previsto_publico, grupos)["por_grupo"]
        a2.bar(x + 0.2, [pub.get(n, np.nan) for n in nomes], width=largura, color=estilo.APAGADO,
               label="Só dataset público (atual)")
    a2.set_xticks(x, nomes, rotation=0 if rotulo_grupo == "voluntário" else 30, ha="right"
                  if rotulo_grupo != "voluntário" else "center", fontsize=8)
    a2.set_xlabel(f"{rotulo_grupo.capitalize()} fora do treino")
    a2.set_ylabel("Erro médio absoluto (BPM)")
    a2.set_title("Erro fora da amostra", pad=38)
    a2.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=1, fontsize=8, borderaxespad=0.2)
    fig.tight_layout()
    saida.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(saida, bbox_inches="tight")
    plt.close(fig)
    print(f"Figura: {saida}")


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------
def treina_publico(args):
    dados = prepara(Path(args.dados))
    print(f"{len(dados)} linhas (teste x velocidade), {dados['ID_test'].nunique()} testes, "
          f"{dados['ID'].nunique()} pessoas")
    lista, previsoes = [linha_de_base_publico(dados)], {}
    for nome, fabrica in CANDIDATOS.items():
        m, previsoes[nome] = avalia_publico(nome, fabrica, dados)
        lista.append(m)
    for m in lista:
        print(f"  {m['modelo']:32s} MAE {m['mae']:5.1f} BPM   R2 {m['r2']:.3f}")

    melhor = min(lista[1:], key=lambda m: m["mae"])
    modelo = CANDIDATOS[melhor["modelo"]]().fit(dados[COLUNAS], dados["fc"])
    saida = Path(args.saida or Path(__file__).with_name("modelo_fc.joblib"))
    joblib.dump({"modelo": modelo, "colunas": COLUNAS, "metricas": lista,
                 "escolhido": melhor["modelo"], "linhas": len(dados), "modo": "publico",
                 "fonte": FONTE_PUBLICO}, saida)
    print(f"Modelo escolhido: {melhor['modelo']} -> {saida}")

    figura = Path(args.figura or RAIZ / "docs" / "figuras" / "v2_modelo_fc.png")
    figura_publico(dados, previsoes[melhor["modelo"]], modelo, figura)
    resumo = figura.with_name(figura.stem + "_metricas.json")
    resumo.write_text(json.dumps({"linhas": len(dados), "testes": int(dados["ID_test"].nunique()),
                                  "pessoas": int(dados["ID"].nunique()), "metricas": lista,
                                  "escolhido": melhor["modelo"]}, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"Métricas: {resumo}")


def treina_com_reais(args):
    reais = prepara_reais(Path(args.reais))
    n_vol, n_ses = reais["voluntario"].nunique(), reais["sessao"].nunique()
    print(f"Coletas: {len(reais)} etapas em regime, {n_ses} sessões, {n_vol} voluntário(s)")

    if n_vol >= 2:
        grupos, rotulo = reais["voluntario"].to_numpy(), "voluntário"
    elif n_ses >= 2:
        grupos, rotulo = reais["sessao"].to_numpy(), "sessão"
        print("Um único voluntário: a avaliação deixa uma SESSÃO de fora por vez. Isso mede a "
              "repetição entre dias da mesma pessoa, não o erro para um usuário novo.")
    else:
        sys.exit("É preciso pelo menos 2 sessões (de preferência, de voluntários diferentes).")
    if args.modo == "reais" and n_vol < 5:
        print(f"Atenção: modelo só com coletas e {n_vol} voluntário(s); "
              "ele tende a decorar essas pessoas.")

    # Modelo atual do guia: só dataset público.
    publico = prepara(Path(args.dados))
    base = CANDIDATOS[MODELO_PUBLICO]().fit(publico[COLUNAS], publico["fc"])
    prev_publico = base.predict(reais[COLUNAS])
    reais["residuo"] = reais["fc"] - prev_publico

    lista = [metricas("só dataset público (atual)", reais["fc"], prev_publico, grupos),
             metricas("mediana por velocidade (fora da amostra)", reais["fc"], fora_da_amostra(
                 lambda tr, te: te["vel"].map(tr.groupby("vel")["fc"].median())
                 .fillna(tr["fc"].median()).to_numpy(), reais, grupos), grupos)]
    previsoes = {}
    if args.modo == "combinado":
        for nome, tipo in CORRECOES.items():
            def prever(tr, te, tipo=tipo):
                a, b = ajusta_correcao(tr, tipo)
                return base.predict(te[COLUNAS]) + a + b * te["vel"].to_numpy()
            previsoes[nome] = fora_da_amostra(prever, reais, grupos)
    else:
        for nome, fabrica in CANDIDATOS.items():
            previsoes[nome] = fora_da_amostra(
                lambda tr, te, f=fabrica: f().fit(tr[COLUNAS], tr["fc"]).predict(te[COLUNAS]),
                reais, grupos)
    lista += [metricas(nome, reais["fc"], p, grupos) for nome, p in previsoes.items()]

    print(f"Erro nas coletas, um(a) {rotulo} fora do treino por vez:")
    for m in lista:
        mae = f"{m['mae']:5.1f}" if m["mae"] is not None else "  -  "
        r2 = f"{m['r2']:6.3f}" if m["r2"] is not None else "   -  "
        vies = f"{m['vies']:+5.1f}" if m["vies"] is not None else "  -  "
        print(f"  {m['modelo']:42s} MAE {mae} BPM   R2 {r2}   viés {vies} BPM   (n={m['n']})")

    avaliados = [m for m in lista if m["modelo"] in previsoes and m["mae"] is not None]
    melhor = min(avaliados, key=lambda m: m["mae"])
    margem = float(np.clip(np.nanmedian(reais["fc"] - previsoes[melhor["modelo"]]),
                           0.0, MARGEM_INICIAL))
    if args.modo == "combinado":
        a, b = ajusta_correcao(reais, CORRECOES[melhor["modelo"]])
        modelo = ModeloCorrigido(base, a, b)
        print(f"Correção final: {a:+.1f} BPM {b:+.2f} BPM por km/h")
        fonte = f"{FONTE_PUBLICO} + correção pelas coletas próprias"
    else:
        modelo = CANDIDATOS[melhor["modelo"]]().fit(reais[COLUNAS], reais["fc"])
        fonte = "coletas próprias"

    saida = Path(args.saida or Path(__file__).with_name(f"modelo_fc_{args.modo}.joblib"))
    joblib.dump({"modelo": modelo, "colunas": COLUNAS, "metricas": lista,
                 "escolhido": melhor["modelo"], "modo": args.modo, "fonte": fonte,
                 "linhas_reais": len(reais), "voluntarios": sorted(reais["voluntario"].unique()),
                 "avaliacao": f"um(a) {rotulo} fora por vez", "margem_inicial": margem}, saida)
    print(f"Modelo escolhido: {melhor['modelo']} (margem inicial {margem:.1f} BPM) -> {saida}")
    print(f"Comparado ao modelo atual: MAE {lista[0]['mae']:.1f} -> {melhor['mae']:.1f} BPM")
    print(f"Para usar no guia: --modelo {saida}")

    figura = Path(args.figura or RAIZ / "docs" / "figuras" / f"v3_modelo_fc_{args.modo}.png")
    figura_reais(reais, previsoes[melhor["modelo"]], prev_publico, melhor["modelo"], grupos,
                 rotulo, figura)
    resumo = figura.with_name(figura.stem + "_metricas.json")
    resumo.write_text(json.dumps({"modo": args.modo, "etapas_reais": len(reais), "sessoes": int(n_ses),
                                  "voluntarios": int(n_vol), "avaliacao": f"um(a) {rotulo} fora por vez",
                                  "margem_inicial": margem, "metricas": lista,
                                  "escolhido": melhor["modelo"]}, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"Métricas: {resumo}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modo", choices=["publico", "combinado", "reais"], default="publico")
    ap.add_argument("--dados", default=str(RAIZ / "dados" / "externos" / "treadmill"),
                    help="pasta do dataset público")
    ap.add_argument("--reais", default=str(RAIZ / "dados" / "reais" / "tabela_treino.csv"),
                    help="tabela gerada por tools/preparar_dados_reais.py")
    ap.add_argument("--saida", help="arquivo .joblib (padrão: guia/modelo_fc.joblib no modo "
                                    "publico, guia/modelo_fc_<modo>.joblib nos outros)")
    ap.add_argument("--figura", help="arquivo .png da figura de avaliação")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    if args.modo == "publico":
        treina_publico(args)
    else:
        treina_com_reais(args)


if __name__ == "__main__":
    main()
