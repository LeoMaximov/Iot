"""Gera os gráficos e o resumo numérico de uma coleta gravada pelo serial_logger.py.

Uso:
  python tools/plot_coleta.py dados/brutos                       # todas as coletas da pasta
  python tools/plot_coleta.py dados/brutos/20261114_101500_T2    # uma coleta (base, sem sufixo)
  python tools/plot_coleta.py <base> --janela 30 40              # trecho do zoom do sinal bruto

Para cada coleta <base> lê o que existir entre
  <base>_E.csv   sinal bruto da braçadeira (50 Hz)
  <base>_R.csv   pacotes recebidos pelo painel (10 Hz)
  <base>_M.csv   publicações MQTT (1 Hz)
  <base>_TB.csv  séries exportadas do ThingsBoard (tb_export.py)
  <base>_meta.json
e grava as figuras PNG e um <base>_resumo.md na pasta de saída (docs/figuras).
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Paleta e tinta (tema claro, para relatório impresso).
AZUL = "#2a78d6"
LARANJA = "#eb6834"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
APAGADO = "#898781"
GRADE = "#e1e0d9"
EIXO = "#c3c2b7"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
    "font.size": 10,
    "figure.dpi": 100,
    "savefig.dpi": 200,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": EIXO,
    "axes.labelcolor": TINTA_2,
    "axes.titlecolor": TINTA,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRADE,
    "grid.linewidth": 0.8,
    "xtick.color": APAGADO,
    "ytick.color": APAGADO,
    "xtick.labelcolor": TINTA_2,
    "ytick.labelcolor": TINTA_2,
    "lines.linewidth": 1.6,
    "legend.frameon": False,
})

SUFIXOS = ("_E.csv", "_R.csv", "_M.csv", "_TB.csv", "_meta.json")
REQUISITO_LATENCIA_MS = 100.0  # RF05 / RNF01


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def encontra_bases(caminho: Path):
    if caminho.is_dir():
        bases = set()
        for arquivo in caminho.iterdir():
            for sufixo in SUFIXOS:
                if arquivo.name.endswith(sufixo):
                    bases.add(arquivo.with_name(arquivo.name[: -len(sufixo)]))
        return sorted(bases)
    return [caminho]


def le_csv(base: Path, sufixo: str):
    arquivo = Path(f"{base}{sufixo}")
    if not arquivo.exists():
        return None
    df = pd.read_csv(arquivo)
    return df if len(df) else None


def le_meta(base: Path):
    arquivo = Path(f"{base}_meta.json")
    if arquivo.exists():
        return json.loads(arquivo.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------------------
# Auxiliares de figura
# ---------------------------------------------------------------------------
def salva(fig, saida: Path, base: Path, nome: str, meta: dict):
    if meta.get("simulado"):
        fig.text(0.5, 0.5, "DADOS SIMULADOS", fontsize=40, color="#d03b3b", alpha=0.25,
                 ha="center", va="center", rotation=25, weight="bold")
    destino = saida / f"{base.name}_{nome}.png"
    fig.savefig(destino, bbox_inches="tight")
    plt.close(fig)
    print(f"  figura: {destino}")


def linha_referencia(ax, valor, rotulo):
    ax.axhline(valor, color=TINTA_2, linewidth=1.0, linestyle=(0, (4, 3)))
    ax.annotate(rotulo, xy=(1.0, valor), xycoords=("axes fraction", "data"),
                xytext=(-4, 4), textcoords="offset points", ha="right", va="bottom",
                color=TINTA_2, fontsize=9)


def escolhe_janela(t, janela, largura=10.0):
    """Trecho usado no zoom do sinal bruto: o pedido, ou 10 s no meio da coleta."""
    if janela:
        return janela[0], janela[1]
    inicio, fim = float(t.iloc[0]), float(t.iloc[-1])
    if fim - inicio <= largura * 1.5:
        return inicio, fim
    meio = (inicio + fim) / 2
    return meio - largura / 2, meio + largura / 2


def titulo(meta, texto):
    teste = meta.get("teste")
    return f"{texto} — {teste}" if teste else texto


# ---------------------------------------------------------------------------
# Figuras da braçadeira (arquivo _E)
# ---------------------------------------------------------------------------
def fig_aceleracao(e, meta, janela, saida, base):
    t0, t1 = escolhe_janela(e["t"], janela)
    j = e[(e["t"] >= t0) & (e["t"] <= t1)]
    passos = j[j["passo"] == 1]

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 5), sharex=True)
    a1.plot(j["t"], j["mag"], color=AZUL)
    a1.set_ylabel("Magnitude (g)")
    a1.set_title(titulo(meta, "Aceleração do braço e passadas detectadas"))

    a2.plot(j["t"], j["filtrado"], color=AZUL, label="Sinal filtrado (sem gravidade)")
    a2.plot(passos["t"], passos["filtrado"], linestyle="none", marker="o", markersize=6,
            markerfacecolor=LARANJA, markeredgecolor="white", markeredgewidth=1.0,
            label=f"Passada detectada ({len(passos)} no trecho)")
    a2.set_ylabel("Filtrado (g)")
    a2.set_xlabel("Tempo (s)")
    a2.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=2, fontsize=9, borderaxespad=0.2)
    fig.align_ylabels()
    salva(fig, saida, base, "aceleracao", meta)


def fig_ppg(e, meta, janela, saida, base):
    t0, t1 = escolhe_janela(e["t"], janela)
    j = e[(e["t"] >= t0) & (e["t"] <= t1)]
    if j["ir"].abs().max() == 0:
        return
    batimentos = j[j["batimento"] == 1]

    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(j["t"], j["ir"], color=AZUL, label="Canal IR (bruto)")
    ax.plot(batimentos["t"], batimentos["ir"], linestyle="none", marker="o", markersize=6,
            markerfacecolor=LARANJA, markeredgecolor="white", markeredgewidth=1.0,
            label=f"Batimento detectado ({len(batimentos)} no trecho)")
    ax.set_title(titulo(meta, "Sinal PPG do MAX30102 e batimentos detectados"), pad=24)
    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel("IR (contagens do ADC)")
    ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=2, fontsize=9, borderaxespad=0.2)
    salva(fig, saida, base, "ppg", meta)


# ---------------------------------------------------------------------------
# Figuras comuns (_E ou _R)
# ---------------------------------------------------------------------------
def fig_serie(df, coluna, meta, saida, base, nome, texto_titulo, unidade, ref=None,
              rotulo_ref="", oculta_zero=False, alvo=None):
    y = df[coluna].astype(float)
    if oculta_zero:
        y = y.where(y > 0)
    if y.notna().sum() == 0:
        print(f"  ({nome}: sem valores válidos, figura não gerada)")
        return

    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(df["t"], y, color=AZUL)
    if ref:
        linha_referencia(ax, ref, f"{rotulo_ref}: {ref:g} {unidade}")
    if alvo:
        # Meta é um valor-alvo, não uma medida: desenhada à parte e fora do cálculo de erro.
        linha_referencia(ax, alvo, f"meta: {alvo:g} {unidade}")
    ax.set_title(titulo(meta, texto_titulo))
    ax.set_xlabel("Tempo (s)")
    ax.set_ylabel(unidade)
    salva(fig, saida, base, nome, meta)


def fig_latencia(lat_ms, meta, saida, base):
    if len(lat_ms) == 0:
        return
    mediana, p95, maximo = np.median(lat_ms), np.percentile(lat_ms, 95), lat_ms.max()
    dentro = 100.0 * (lat_ms < REQUISITO_LATENCIA_MS).mean()

    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.hist(lat_ms, bins=40, color=AZUL, edgecolor="white", linewidth=0.8)
    ax.axvline(mediana, color=TINTA_2, linewidth=1.0, linestyle=(0, (4, 3)))
    if maximo > REQUISITO_LATENCIA_MS / 2:
        ax.axvline(REQUISITO_LATENCIA_MS, color=LARANJA, linewidth=1.2)
        ax.annotate("limite de 100 ms", xy=(REQUISITO_LATENCIA_MS, 1.0),
                    xycoords=("data", "axes fraction"), xytext=(-4, -4),
                    textcoords="offset points", ha="right", va="top", color=TINTA_2, fontsize=9)
    ax.set_title(titulo(meta, "Latência ESP-NOW (envio até o ACK da receptora)"), pad=24)
    ax.set_xlabel("Latência (ms)")
    ax.set_ylabel("Pacotes")
    ax.text(0.0, 1.03,
            f"n = {len(lat_ms)}   mediana (tracejado) = {mediana:.2f} ms   p95 = {p95:.2f} ms   "
            f"máx. = {maximo:.2f} ms   abaixo de 100 ms: {dentro:.1f} %",
            transform=ax.transAxes, ha="left", va="bottom", color=TINTA_2, fontsize=9)
    salva(fig, saida, base, "latencia", meta)


# ---------------------------------------------------------------------------
# Figuras do painel (arquivo _R)
# ---------------------------------------------------------------------------
def perda_por_janela(r, largura=10.0):
    """Percentual de pacotes perdidos em janelas de `largura` segundos."""
    grupo = (r["t"] // largura).astype(int)
    recebidos = r.groupby(grupo)["seq"].count()
    perdidos = r.groupby(grupo)["perdidos"].last().diff()
    perdidos.iloc[0] = r.groupby(grupo)["perdidos"].last().iloc[0] - r["perdidos"].iloc[0]
    perdidos = perdidos.clip(lower=0)
    pct = 100.0 * perdidos / (recebidos + perdidos)
    return recebidos.index.to_numpy() * largura, pct.to_numpy()


def fig_enlace(r, meta, saida, base):
    tem_rssi = (r["rssi"] != 0).any()
    n = 3 if tem_rssi else 2
    fig, eixos = plt.subplots(n, 1, figsize=(8, 2.2 * n), sharex=True)
    i = 0
    if tem_rssi:
        eixos[i].plot(r["t"], r["rssi"], color=AZUL)
        eixos[i].set_ylabel("RSSI (dBm)")
        i += 1

    inicio, pct = perda_por_janela(r)
    eixos[i].bar(inicio, pct, width=9.4, align="edge", color=AZUL)
    eixos[i].set_ylabel("Perda (%)\npor janela de 10 s")
    eixos[i].set_ylim(0, max(5.0, np.nanmax(pct) * 1.15))
    i += 1

    eixos[i].plot(r["t"].iloc[1:], r["dt_chegada_ms"].iloc[1:], color=AZUL, linewidth=1.0)
    eixos[i].set_ylabel("Intervalo entre\npacotes (ms)")
    eixos[i].set_xlabel("Tempo (s)")

    eixos[0].set_title(titulo(meta, "Qualidade do enlace ESP-NOW no painel"))
    fig.align_ylabels()
    salva(fig, saida, base, "enlace", meta)


# ---------------------------------------------------------------------------
# Figura do ThingsBoard (arquivo _TB)
# ---------------------------------------------------------------------------
def fig_thingsboard(tb, meta, saida, base):
    colunas = [c for c in ("bpm", "spm") if c in tb.columns and tb[c].notna().any()]
    if not colunas:
        return
    rotulos = {"bpm": "BPM", "spm": "SPM"}
    fig, eixos = plt.subplots(len(colunas), 1, figsize=(8, 2.6 * len(colunas)), sharex=True,
                              squeeze=False)
    for ax, coluna in zip(eixos[:, 0], colunas):
        serie = tb[["t", coluna]].dropna()
        ax.plot(serie["t"], serie[coluna], color=AZUL)
        ax.set_ylabel(rotulos[coluna])
    eixos[0, 0].set_title(titulo(meta, "Telemetria armazenada no ThingsBoard (recebida por MQTT)"))
    eixos[-1, 0].set_xlabel("Tempo (s)")
    fig.align_ylabels()
    salva(fig, saida, base, "thingsboard", meta)


# ---------------------------------------------------------------------------
# Resumo numérico
# ---------------------------------------------------------------------------
def estatisticas_serie(valores, ref):
    """Linhas (rótulo, valor) de média, desvio e erro frente à referência."""
    v = valores[valores > 0]
    if len(v) == 0:
        return [("amostras válidas", "0")]
    linhas = [("média ± desvio padrão", f"{v.mean():.1f} ± {v.std():.1f}")]
    if ref:
        linhas.append(("referência", f"{ref:g}"))
        linhas.append(("erro médio absoluto", f"{(v - ref).abs().mean():.1f} "
                                              f"({100 * (v - ref).abs().mean() / ref:.1f} %)"))
    return linhas


def processa(base: Path, saida: Path, janela):
    meta = le_meta(base)
    e, r, m, tb = (le_csv(base, s) for s in ("_E.csv", "_R.csv", "_M.csv", "_TB.csv"))
    if e is None and r is None and m is None and tb is None:
        print(f"{base}: nenhum arquivo de dados encontrado")
        return
    print(f"\n{base.name}")
    resumo = []  # (seção, rótulo, valor)

    if e is not None:
        e["t"] = (e["t_ms"] - e["t_ms"].iloc[0]) / 1000.0
        duracao = e["t"].iloc[-1]
        resumo += [("Braçadeira (50 Hz)", "duração", f"{duracao:.1f} s"),
                   ("Braçadeira (50 Hz)", "taxa de amostragem efetiva",
                    f"{(len(e) - 1) / duracao:.1f} Hz" if duracao > 0 else "-"),
                   ("Braçadeira (50 Hz)", "passadas contadas",
                    f"{int(e['passos'].iloc[-1] - e['passos'].iloc[0])}"),
                   ("Braçadeira (50 Hz)", "tempo com contato do sensor óptico",
                    f"{100 * e['contato'].mean():.0f} %")]
        resumo += [("BPM (braçadeira)", a, b)
                   for a, b in estatisticas_serie(e["bpm"], meta.get("ref_bpm"))]
        resumo += [("SPM (braçadeira)", a, b)
                   for a, b in estatisticas_serie(e["spm"], meta.get("ref_spm"))]
        fig_aceleracao(e, meta, janela, saida, base)
        fig_ppg(e, meta, janela, saida, base)

    # BPM, SPM e latência: usa o painel quando existir (dado que cruzou o ESP-NOW).
    fonte = r if r is not None else e
    if r is not None:
        r["t"] = (r["t_rx_ms"] - r["t_rx_ms"].iloc[0]) / 1000.0
        recebidos = len(r)
        perdidos = int(r["perdidos"].iloc[-1] - r["perdidos"].iloc[0])
        resumo += [("Enlace ESP-NOW", "duração", f"{r['t'].iloc[-1]:.1f} s"),
                   ("Enlace ESP-NOW", "pacotes recebidos", f"{recebidos}"),
                   ("Enlace ESP-NOW", "pacotes perdidos",
                    f"{perdidos} ({100 * perdidos / (recebidos + perdidos):.2f} %)"),
                   ("Enlace ESP-NOW", "intervalo entre pacotes (mediana / p95 / máx.)",
                    f"{r['dt_chegada_ms'].iloc[1:].median():.0f} / "
                    f"{r['dt_chegada_ms'].iloc[1:].quantile(0.95):.0f} / "
                    f"{r['dt_chegada_ms'].iloc[1:].max():.0f} ms")]
        if (r["rssi"] != 0).any():
            resumo.append(("Enlace ESP-NOW", "RSSI médio", f"{r['rssi'].mean():.0f} dBm"))
        if e is None:
            resumo += [("BPM (painel)", a, b)
                       for a, b in estatisticas_serie(r["bpm"], meta.get("ref_bpm"))]
            resumo += [("SPM (painel)", a, b)
                       for a, b in estatisticas_serie(r["spm"], meta.get("ref_spm"))]
        fig_enlace(r, meta, saida, base)

    if fonte is not None:
        fig_serie(fonte, "bpm", meta, saida, base, "bpm", "Frequência cardíaca medida", "BPM",
                  ref=meta.get("ref_bpm"), rotulo_ref="referência", oculta_zero=True,
                  alvo=meta.get("meta_bpm"))
        fig_serie(fonte, "spm", meta, saida, base, "spm", "Cadência medida", "SPM",
                  ref=meta.get("ref_spm"), rotulo_ref="metrônomo")
        lat_ms = fonte.loc[fonte["lat_us"] >= 0, "lat_us"].to_numpy(dtype=float) / 1000.0
        if len(lat_ms):
            resumo += [("Latência ESP-NOW", "medidas", f"{len(lat_ms)}"),
                       ("Latência ESP-NOW", "mediana / p95 / máx.",
                        f"{np.median(lat_ms):.2f} / {np.percentile(lat_ms, 95):.2f} / "
                        f"{lat_ms.max():.2f} ms"),
                       ("Latência ESP-NOW", "abaixo de 100 ms",
                        f"{100 * (lat_ms < REQUISITO_LATENCIA_MS).mean():.1f} %")]
            fig_latencia(lat_ms, meta, saida, base)

    if m is not None:
        ok = int(m["ok"].sum())
        resumo += [("MQTT", "publicações com sucesso",
                    f"{ok} de {len(m)} ({100 * ok / len(m):.1f} %)")]

    if tb is not None:
        tb["t"] = (tb["ts"] - tb["ts"].iloc[0]) / 1000.0
        resumo += [("ThingsBoard", "pontos armazenados", f"{len(tb)}"),
                   ("ThingsBoard", "período", f"{tb['t'].iloc[-1]:.0f} s")]
        fig_thingsboard(tb, meta, saida, base)

    linhas = [f"# Resumo — {base.name}", ""]
    if meta.get("simulado"):
        linhas += ["**DADOS SIMULADOS — não usar no relatório.**", ""]
    if meta.get("nota"):
        linhas += [f"Observação: {meta['nota']}", ""]
    linhas += ["| Grupo | Métrica | Valor |", "|---|---|---|"]
    linhas += [f"| {s} | {a} | {b} |" for s, a, b in resumo]
    destino = saida / f"{base.name}_resumo.md"
    destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"  resumo: {destino}")
    for s, a, b in resumo:
        print(f"    {s} | {a}: {b}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caminho", help="pasta com coletas ou base de uma coleta (sem sufixo)")
    ap.add_argument("--saida", default="docs/figuras", help="pasta das figuras")
    ap.add_argument("--janela", nargs=2, type=float, metavar=("INICIO", "FIM"),
                    help="trecho, em segundos, usado no zoom do sinal bruto")
    args = ap.parse_args()

    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    bases = encontra_bases(Path(args.caminho))
    if not bases:
        sys.exit(f"Nenhuma coleta encontrada em {args.caminho}")
    for base in bases:
        processa(base, saida, args.janela)


if __name__ == "__main__":
    main()
