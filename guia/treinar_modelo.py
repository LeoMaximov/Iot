"""Treina o modelo FC = f(velocidade, idade, sexo, peso, altura) do guia de cardio.

Dados: "Treadmill Maximal Exercise Tests from the Exercise Physiology and Human
Performance Lab of the University of Malaga", PhysioNet, versão 1.0.1
(https://physionet.org/content/treadmill-exercise-cardioresp/1.0.1/).
992 testes incrementais em esteira de 857 pessoas. Os arquivos subject-info.csv
e test_measure.csv devem estar em dados/externos/treadmill/ (fora do Git: a
licença do PhysioNet não permite redistribuir os dados).

Preparação:
  - mantém só a fase de esforço de cada teste (até a velocidade máxima); a
    recuperação tem FC alta com velocidade baixa e inverteria a relação;
  - resume cada teste a uma linha por velocidade (mediana da FC em cada 0,5 km/h).

Avaliação: validação cruzada em 5 partes agrupada por pessoa, de modo que
ninguém aparece no treino e no teste ao mesmo tempo.

Uso:
  python guia/treinar_modelo.py
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
COLUNAS = ["vel", "idade", "sexo_m", "peso", "altura"]


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
    return dados


def avalia(nome, fabrica, dados):
    """MAE e R² fora da amostra, com as pessoas separadas entre treino e teste."""
    previsto = np.zeros(len(dados))
    for treino, teste in GroupKFold(n_splits=5).split(dados, groups=dados["ID"]):
        modelo = fabrica().fit(dados.iloc[treino][COLUNAS], dados.iloc[treino]["fc"])
        previsto[teste] = modelo.predict(dados.iloc[teste][COLUNAS])
    return {"modelo": nome, "mae": float(mean_absolute_error(dados["fc"], previsto)),
            "r2": float(r2_score(dados["fc"], previsto))}, previsto


def linha_de_base(dados):
    """Referência sem aprendizado: FC mediana de cada velocidade, medida fora da amostra."""
    previsto = np.zeros(len(dados))
    for treino, teste in GroupKFold(n_splits=5).split(dados, groups=dados["ID"]):
        tabela = dados.iloc[treino].groupby("vel")["fc"].median()
        previsto[teste] = dados.iloc[teste]["vel"].map(tabela).fillna(tabela.median())
    return {"modelo": "mediana por velocidade", "mae": float(mean_absolute_error(dados["fc"], previsto)),
            "r2": float(r2_score(dados["fc"], previsto))}


def figura(dados, previsto, modelo, saida: Path):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dados", default=str(RAIZ / "dados" / "externos" / "treadmill"))
    ap.add_argument("--saida", default=str(Path(__file__).with_name("modelo_fc.joblib")))
    ap.add_argument("--figura", default=str(RAIZ / "docs" / "figuras" / "v2_modelo_fc.png"))
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    dados = prepara(Path(args.dados))
    print(f"{len(dados)} linhas (teste x velocidade), {dados['ID_test'].nunique()} testes, "
          f"{dados['ID'].nunique()} pessoas")

    candidatos = {
        "regressão linear": LinearRegression,
        # FC não decresce com a velocidade: a restrição mantém a inversão bem definida.
        "gradient boosting monotônico": lambda: HistGradientBoostingRegressor(
            monotonic_cst=[1, 0, 0, 0, 0], max_iter=300, learning_rate=0.06, random_state=0),
    }
    metricas, previsoes = [linha_de_base(dados)], {}
    for nome, fabrica in candidatos.items():
        m, previsoes[nome] = avalia(nome, fabrica, dados)
        metricas.append(m)
    for m in metricas:
        print(f"  {m['modelo']:32s} MAE {m['mae']:5.1f} BPM   R2 {m['r2']:.3f}")

    melhor = min(metricas[1:], key=lambda m: m["mae"])
    modelo = candidatos[melhor["modelo"]]().fit(dados[COLUNAS], dados["fc"])
    joblib.dump({"modelo": modelo, "colunas": COLUNAS, "metricas": metricas,
                 "escolhido": melhor["modelo"], "linhas": len(dados),
                 "fonte": "PhysioNet treadmill-exercise-cardioresp 1.0.1"}, args.saida)
    print(f"Modelo escolhido: {melhor['modelo']} -> {args.saida}")

    figura(dados, previsoes[melhor["modelo"]], modelo, Path(args.figura))
    resumo = Path(args.figura).with_name("v2_modelo_fc_metricas.json")
    resumo.write_text(json.dumps({"linhas": len(dados), "testes": int(dados["ID_test"].nunique()),
                                  "pessoas": int(dados["ID"].nunique()), "metricas": metricas,
                                  "escolhido": melhor["modelo"]}, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"Métricas: {resumo}")


if __name__ == "__main__":
    main()
