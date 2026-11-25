"""Trata as sessões de coleta (dados/reais/<sessao>/) e monta a tabela de treino.

Para cada sessão:
  1. alinha no tempo do guia (segundos desde o início do treino) as fontes de BPM:
     serial do painel (*_R.csv, 10 Hz) > ThingsBoard (*_TB.csv, 1 Hz) > próprio guia;
  2. limpa o BPM: sem contato, fora de 40-220 BPM ou travado no mesmo valor por 15 s;
  3. resume em uma linha por segundo           -> processado/serie_1hz.csv
  4. mede a qualidade do enlace e do sinal     -> processado/qualidade.json
  5. resume cada etapa no trecho de regime     -> processado/etapas.csv
  6. desenha a sessão                          -> processado/sessao.png

Depois junta todas as sessões já tratadas da pasta:
  dados/reais/tabela_treino.csv      etapas em regime; entrada de treinar_modelo.py --reais
  dados/reais/etapas_todas.csv       todas as etapas, com o motivo de exclusão
  dados/reais/qualidade_sessoes.csv  uma linha por sessão

Regime: a FC leva de 30 a 90 s para estabilizar depois de uma troca de
velocidade. De cada etapa usa-se só o trecho depois do primeiro minuto (ou da
metade, se a etapa for curta), e a etapa só entra no treino se durar 120 s ou
mais, tiver velocidade constante, BPM válido em 60% do trecho, cadência de quem
está se movendo e FC sem deriva forte. Etapas de recuperação, desaquecimento e
alerta ficam de fora, como a fase de recuperação no dataset público.

Limitação: sem cinta de referência, o "fc" da tabela é o BPM da braçadeira
tratado. As contagens manuais de pulso (referencia_manual.csv) dão só uma
estimativa grosseira do erro.

Uso:
  python tools/preparar_dados_reais.py                       # todas as sessões
  python tools/preparar_dados_reais.py dados/reais/<sessao>  # uma sessão
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(RAIZ / "guia"))

import voluntarios as cadastro  # noqa: E402

BPM_MIN, BPM_MAX = 40, 220
TRAVADO_S = 15            # mesmo BPM por este tempo = sensor travado
LACUNA_S = 3              # segundos seguidos sem nenhuma amostra = lacuna
ESPERA_REGIME_S = 60      # descarta o início de cada etapa (FC ainda subindo)
DURACAO_REGIME_S = 120    # etapas mais curtas não chegam ao regime
JANELA_MIN_S = 20
COBERTURA_MIN = 0.6
SPM_PARADO = 40
DERIVA_MAX = 8.0          # BPM/min dentro do trecho de regime
JANELA_REF_S = 15         # contagem manual: 15 s de pulso x 4
FORA_DO_TREINO = {"ALERTA", "RECUPERAR", "DESAQUECER"}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def limpo(x):
    """Converte para tipos que o json aceita (NaN vira null)."""
    if isinstance(x, dict):
        return {k: limpo(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [limpo(v) for v in x]
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if np.isnan(x) else round(float(x), 3)
    return x


def arquivo(pasta: Path, padrao: str):
    """O arquivo mais recente da pasta com esse padrão (fora de processado/)."""
    candidatos = sorted(p for p in pasta.glob(padrao) if p.is_file())
    if len(candidatos) > 1:
        print(f"  aviso: {len(candidatos)} arquivos {padrao}; usando {candidatos[-1].name}")
    return candidatos[-1] if candidatos else None


def le_anotacao(caminho: Path, colunas):
    if not caminho.exists():
        return pd.DataFrame(columns=colunas)
    df = pd.read_csv(caminho, comment="#")
    return df.dropna(subset=[c for c in colunas if c != "nota"])


def trechos(mascara):
    """Lista de (início, fim) dos trechos em que a máscara booleana é verdadeira."""
    saida, inicio = [], None
    for i, valor in enumerate(list(mascara) + [False]):
        if valor and inicio is None:
            inicio = i
        elif not valor and inicio is not None:
            saida.append((inicio, i))
            inicio = None
    return saida


def marca_travado(bpm: pd.Series):
    """True nos segundos em que o BPM ficou idêntico por TRAVADO_S segundos ou mais."""
    valores = bpm.to_numpy(dtype=float)
    travado = np.zeros(len(valores), dtype=bool)
    inicio = 0
    for i in range(1, len(valores) + 1):
        if i == len(valores) or valores[i] != valores[inicio]:   # NaN quebra a sequência
            if i - inicio >= TRAVADO_S and not np.isnan(valores[inicio]):
                travado[inicio:i] = True
            inicio = i
    return travado


# ---------------------------------------------------------------------------
# Leitura e alinhamento
# ---------------------------------------------------------------------------
def carrega_guia(pasta):
    caminho = arquivo(pasta, "*_sessao.csv")
    if caminho is None:
        raise FileNotFoundError("sem registro do guia (*_sessao.csv)")
    guia = pd.read_csv(caminho)
    caminho_ev = arquivo(pasta, "*_eventos.csv")
    eventos = (pd.read_csv(caminho_ev) if caminho_ev is not None
               else pd.DataFrame(columns=["t", "tipo", "texto", "decisao"]))
    return guia, eventos


def instante_zero(guia, meta):
    """Horário absoluto (ms) do t = 0 do guia."""
    if "ts_ms" in guia.columns and guia["ts_ms"].notna().any():
        return float((guia["ts_ms"] - guia["t"] * 1000).median()), "guia (ts_ms)"
    return float(meta["inicio_epoch_ms"]), "metadados (aproximado, sem ts_ms no guia)"


def carrega_serial(pasta, t0_ms):
    caminho = arquivo(pasta, "*_R.csv")
    if caminho is None:
        return None
    meta_serial = Path(str(caminho)[: -len("_R.csv")] + "_meta.json")
    m = json.loads(meta_serial.read_text(encoding="utf-8")) if meta_serial.exists() else {}
    inicio = m.get("inicio_epoch_ms")
    if inicio is None:
        if "inicio" not in m:
            print("  aviso: serial sem horário de início; ignorada")
            return None
        inicio = datetime.fromisoformat(m["inicio"]).timestamp() * 1000
        print("  aviso: serial gravada por logger antigo; alinhamento com precisão de 1 s")
    r = pd.read_csv(caminho)
    r["t"] = (inicio + r["t_pc"] * 1000 - t0_ms) / 1000
    return r


def carrega_tb(pasta, t0_ms):
    caminho = arquivo(pasta, "*_TB.csv")
    if caminho is None:
        return None
    tb = pd.read_csv(caminho)
    tb["t"] = (tb["ts"] - t0_ms) / 1000
    return tb


def sinal_por_segundo(guia, r, tb):
    """Tabela indexada pelo segundo s, com o BPM válido da melhor fonte disponível."""
    if r is not None and len(r):
        r = r.copy()
        r["s"] = np.floor(r["t"]).astype(int)
        r["valido"] = (r["contato"] == 1) & r["bpm"].between(BPM_MIN, BPM_MAX)
        sinal = r.groupby("s").agg(n_amostras=("seq", "size"), contato=("contato", "mean"),
                                   spm=("spm", "median"), rssi=("rssi", "median"))
        sinal["bpm"] = r[r["valido"]].groupby("s")["bpm"].median()
        lat = r[r["lat_us"] >= 0]
        sinal["latencia_ms"] = lat.groupby("s")["lat_us"].median() / 1000
        return sinal, "serial do painel"
    if tb is not None and len(tb) and "bpm" in tb.columns:
        tb = tb.copy()
        tb["s"] = np.floor(tb["t"]).astype(int)
        valido = tb["bpm"].between(BPM_MIN, BPM_MAX)
        if "contato" in tb.columns:
            valido &= tb["contato"] == 1
        sinal = tb.groupby("s").agg(n_amostras=("bpm", "size"))
        for coluna in ("contato", "spm", "rssi", "latencia_ms", "alerta_local"):
            if coluna in tb.columns:
                sinal[coluna] = tb.groupby("s")[coluna].median()
        sinal["bpm"] = tb[valido].groupby("s")["bpm"].median()
        return sinal, "ThingsBoard"
    g = guia.copy()
    g["s"] = g["t"].round().astype(int)
    sinal = g.groupby("s").agg(spm=("spm", "median"))
    sinal["bpm"] = g[g["bpm"].between(BPM_MIN, BPM_MAX)].groupby("s")["bpm"].median()
    sinal["n_amostras"] = g.groupby("s").size()
    return sinal, "guia (1 Hz)"


def por_segundo(guia, r, tb, pasta):
    base = guia[["t", "etapa", "nome", "zona", "vel", "bpm_min", "bpm_max"]].copy()
    base = base.rename(columns={"vel": "vel_guia"})
    base["s"] = base["t"].round().astype(int)
    sinal, fonte = sinal_por_segundo(guia, r, tb)
    seg = base.merge(sinal, left_on="s", right_index=True, how="left").drop(columns="s")
    if tb is not None and "alerta_local" in tb.columns and "alerta_local" not in seg.columns:
        tb_s = tb.assign(s=np.floor(tb["t"]).astype(int)).groupby("s")["alerta_local"].max()
        seg["alerta_local"] = seg["t"].round().astype(int).map(tb_s)

    # Velocidade real da esteira: a do guia, salvo anotação em velocidade_real.csv.
    # Cada anotação vale do instante anotado até o fim daquela etapa (na etapa
    # seguinte o guia pede outra velocidade e a pessoa volta a segui-lo).
    seg["vel"] = seg["vel_guia"]
    anot = le_anotacao(pasta / "velocidade_real.csv", ["t_s", "vel", "nota"])
    for _, linha in anot.sort_values("t_s").iterrows():
        na_hora = seg.loc[seg["t"] >= linha["t_s"], "etapa"]
        if na_hora.empty:
            continue
        alvo = (seg["t"] >= linha["t_s"]) & (seg["etapa"] == na_hora.iloc[0])
        seg.loc[alvo, "vel"] = float(linha["vel"])

    seg["bpm_bruto"] = seg["bpm"]
    seg["travado"] = marca_travado(seg["bpm"])
    seg["bpm_valido"] = seg["bpm"].where(~seg["travado"])
    return seg.drop(columns="bpm"), fonte


# ---------------------------------------------------------------------------
# Qualidade, referência manual e etapas
# ---------------------------------------------------------------------------
def compara_referencia(pasta, seg):
    ref = le_anotacao(pasta / "referencia_manual.csv", ["t_s", "bpm", "nota"])
    if ref.empty:
        return None
    linhas = []
    for _, x in ref.iterrows():
        janela = seg[(seg["t"] > x["t_s"] - JANELA_REF_S) & (seg["t"] <= x["t_s"])]["bpm_valido"]
        medido = janela.median() if janela.notna().any() else np.nan
        linhas.append({"t_s": x["t_s"], "bpm_manual": x["bpm"], "bpm_bracadeira": medido,
                       "erro": medido - x["bpm"]})
    erros = [abs(l["erro"]) for l in linhas if not np.isnan(l["erro"])]
    return {"comparacoes": linhas, "n": len(erros),
            "erro_medio_absoluto": float(np.mean(erros)) if erros else None}


def qualidade(seg, r, tb, eventos, fonte, ref):
    sem_amostra = seg["n_amostras"].fillna(0) == 0
    lacunas = [(a, b) for a, b in trechos(sem_amostra) if b - a >= LACUNA_S]
    q = {
        "fonte_bpm": fonte,
        "duracao_s": len(seg),
        "cobertura_bpm_pct": 100 * seg["bpm_valido"].notna().mean(),
        "segundos_travados": int(seg["travado"].sum()),
        "contato_pct": 100 * seg["contato"].mean() if "contato" in seg and seg["contato"].notna().any() else None,
        "lacunas": len(lacunas),
        "segundos_em_lacuna": int(sum(b - a for a, b in lacunas)),
    }
    if r is not None and len(r):
        d = r["seq"].diff().dropna()
        perdidos = float((d[d > 0] - 1).sum())
        q["reinicios_bracadeira"] = int((d <= 0).sum())
        q["perda_pacotes_pct"] = 100 * perdidos / (perdidos + len(r))
        lat = r.loc[r["lat_us"] >= 0, "lat_us"] / 1000
        q["latencia_mediana_ms"] = lat.median() if len(lat) else None
        q["latencia_p95_ms"] = lat.quantile(0.95) if len(lat) else None
        q["rssi_mediano"] = r["rssi"].median()
        q["rssi_minimo"] = r["rssi"].min()
    elif tb is not None and {"pacotes_rx", "pacotes_perdidos"} <= set(tb.columns):
        rx = tb["pacotes_rx"].dropna()
        perd = tb["pacotes_perdidos"].dropna()
        if len(rx) > 1 and len(perd) > 1 and rx.iloc[-1] >= rx.iloc[0]:
            delta_rx, delta_perd = rx.iloc[-1] - rx.iloc[0], perd.iloc[-1] - perd.iloc[0]
            q["perda_pacotes_pct"] = 100 * delta_perd / max(delta_rx + delta_perd, 1)
        if "latencia_ms" in tb.columns:
            q["latencia_mediana_ms"] = tb["latencia_ms"].median()
        if "rssi" in tb.columns:
            q["rssi_mediano"], q["rssi_minimo"] = tb["rssi"].median(), tb["rssi"].min()
    if "alerta_local" in seg.columns:
        q["segundos_alerta_local"] = int((seg["alerta_local"].fillna(0) > 0).sum())
    q["alertas_guia"] = int((eventos["tipo"] == "alerta").sum()) if len(eventos) else 0
    decisoes = eventos.loc[eventos["tipo"] == "decisao", "decisao"] if len(eventos) else pd.Series(dtype=str)
    q["decisoes"] = {str(k): int(n) for k, n in decisoes.value_counts().items()}
    q["referencia_manual"] = ref
    return q


def resume_etapas(seg, perfil):
    from nucleo import ModeloFC, Perfil
    p = Perfil(perfil["idade"], perfil["fc_repouso"], perfil["sexo"], perfil["peso_kg"],
               perfil["altura_cm"], perfil.get("vel_max", 12.0))
    modelo = ModeloFC()
    modelo.deslocamento = 0.0   # previsão "crua" do modelo, para comparar com o medido

    linhas = []
    for etapa, bloco in seg.groupby("etapa", sort=True):
        nome, dur = str(bloco["nome"].iloc[0]), len(bloco)
        t_ini, t_fim = float(bloco["t"].min()), float(bloco["t"].max()) + 1
        janela = bloco[bloco["t"] >= t_ini + min(ESPERA_REGIME_S, dur / 2)]
        validos = janela.dropna(subset=["bpm_valido"])
        vel_constante = janela["vel"].nunique() == 1
        vel = float(janela["vel"].median())
        cobertura = janela["bpm_valido"].notna().mean() if len(janela) else 0.0
        fc = validos["bpm_valido"].median() if len(validos) else np.nan
        spm = janela["spm"].median() if "spm" in janela and janela["spm"].notna().any() else np.nan
        deriva = (np.polyfit(validos["t"], validos["bpm_valido"], 1)[0] * 60
                  if len(validos) >= 10 else np.nan)

        if nome in FORA_DO_TREINO:
            motivo = "recuperação, desaquecimento ou alerta"
        elif dur < DURACAO_REGIME_S:
            motivo = f"etapa curta (< {DURACAO_REGIME_S} s)"
        elif len(janela) < JANELA_MIN_S:
            motivo = "trecho de regime curto"
        elif not vel_constante:
            motivo = "velocidade mudou no trecho"
        elif cobertura < COBERTURA_MIN:
            motivo = f"BPM válido em menos de {COBERTURA_MIN:.0%} do trecho"
        elif not np.isnan(spm) and spm < SPM_PARADO:
            motivo = "cadência de quem está parado"
        elif not np.isnan(deriva) and abs(deriva) > DERIVA_MAX:
            motivo = f"FC com deriva de {deriva:+.0f} BPM/min"
        else:
            motivo = ""

        fc_modelo = modelo.prever(p, vel)
        linhas.append({
            "etapa": int(etapa), "nome": nome, "zona": int(bloco["zona"].iloc[0]),
            "t_inicio": t_ini, "t_fim": t_fim, "duracao_s": dur,
            "janela_inicio": float(janela["t"].min()) if len(janela) else np.nan,
            "vel": vel, "vel_constante": vel_constante,
            "fc": fc, "fc_p25": validos["bpm_valido"].quantile(0.25) if len(validos) else np.nan,
            "fc_p75": validos["bpm_valido"].quantile(0.75) if len(validos) else np.nan,
            "n_validos": len(validos), "cobertura": cobertura, "spm": spm,
            "deriva_bpm_min": deriva, "fc_modelo": fc_modelo, "residuo": fc - fc_modelo,
            "uso_treino": motivo == "", "motivo_exclusao": motivo,
            "idade": p.idade, "sexo_m": 1 if p.sexo.upper() == "M" else 0,
            "peso": p.peso_kg, "altura": p.altura_cm, "fc_repouso": p.fc_repouso,
        })
    return pd.DataFrame(linhas)


def figura(seg, etapas, eventos, destino, titulo):
    import matplotlib.pyplot as plt
    import plot_coleta as estilo

    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(8, 6.6), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1.4, 1.4]})
    a1.fill_between(seg["t"], seg["bpm_min"], seg["bpm_max"], step="post", color=estilo.AZUL,
                    alpha=0.12, linewidth=0, label="Zona-alvo")
    a1.plot(seg["t"], seg["bpm_bruto"], color=estilo.APAGADO, linewidth=0.8, label="BPM bruto")
    a1.plot(seg["t"], seg["bpm_valido"], color=estilo.AZUL, linewidth=1.2, label="BPM válido")
    rotulo = "Regime usado no treino"
    for _, e in etapas[etapas["uso_treino"]].iterrows():
        a1.axvspan(e["janela_inicio"], e["t_fim"], color=estilo.LARANJA, alpha=0.15, linewidth=0,
                   label=rotulo)
        a1.hlines(e["fc"], e["janela_inicio"], e["t_fim"], color=estilo.LARANJA, linewidth=2)
        rotulo = None
    if len(eventos):
        for t in eventos.loc[eventos["tipo"] == "alerta", "t"]:
            a1.axvline(t, color=estilo.LARANJA, linewidth=1.5)
    a1.set_ylabel("BPM")
    a1.set_title(titulo, pad=30)
    a1.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=4, fontsize=8, borderaxespad=0.2)

    a2.step(seg["t"], seg["vel"], where="post", color=estilo.AZUL, label="Esteira")
    if (seg["vel"] != seg["vel_guia"]).any():
        a2.step(seg["t"], seg["vel_guia"], where="post", color=estilo.APAGADO,
                linestyle=(0, (4, 3)), label="Guia")
        a2.legend(fontsize=8, loc="upper left")
    a2.set_ylabel("Velocidade\n(km/h)")
    if "spm" in seg.columns:
        a3.plot(seg["t"], seg["spm"], color=estilo.AZUL, linewidth=1.0)
    a3.set_ylabel("Cadência (SPM)")
    a3.set_xlabel("Tempo do guia (s)")
    fig.align_ylabels()
    fig.savefig(destino, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------
def sessoes_em(raiz: Path):
    return [p for p in sorted(raiz.iterdir()) if p.is_dir() and (p / "metadados.json").exists()]


def processa(pasta: Path, voluntarios, com_figura=True):
    meta = json.loads((pasta / "metadados.json").read_text(encoding="utf-8"))
    v = voluntarios.get(meta["voluntario"])
    problemas = cadastro.valida(v) if v else ["voluntário não está no cadastro"]
    if problemas:
        raise ValueError(f"{meta['voluntario']}: " + "; ".join(problemas))

    guia, eventos = carrega_guia(pasta)
    t0_ms, origem_tempo = instante_zero(guia, meta)
    r = carrega_serial(pasta, t0_ms)
    tb = carrega_tb(pasta, t0_ms)
    seg, fonte = por_segundo(guia, r, tb, pasta)
    ref = compara_referencia(pasta, seg)
    q = qualidade(seg, r, tb, eventos, fonte, ref)
    q = {"sessao": meta["sessao"], "voluntario": meta["voluntario"], "treino": meta["treino"],
         "ensaio": bool(meta.get("ensaio")), "estado": meta.get("estado"),
         "origem_tempo": origem_tempo, **q}
    etapas = resume_etapas(seg, meta["perfil"])
    etapas.insert(0, "voluntario", meta["voluntario"])
    etapas.insert(0, "sessao", meta["sessao"])
    etapas["ensaio"] = q["ensaio"]
    q["etapas_em_regime"] = int(etapas["uso_treino"].sum())

    saida = pasta / "processado"
    saida.mkdir(exist_ok=True)
    seg.to_csv(saida / "serie_1hz.csv", index=False, float_format="%.3f")
    etapas.to_csv(saida / "etapas.csv", index=False, float_format="%.3f")
    (saida / "qualidade.json").write_text(json.dumps(limpo(q), indent=2, ensure_ascii=False),
                                          encoding="utf-8")
    if com_figura:
        figura(seg, etapas, eventos, saida / "sessao.png",
               f"{meta['sessao']} — BPM de {fonte}" + (" (ENSAIO)" if q["ensaio"] else ""))
    return q


def consolida(raiz: Path, incluir_ensaios=False):
    etapas, qualidades = [], []
    for pasta in sessoes_em(raiz):
        p_etapas, p_q = pasta / "processado" / "etapas.csv", pasta / "processado" / "qualidade.json"
        if p_etapas.exists() and p_q.exists():
            etapas.append(pd.read_csv(p_etapas))
            q = json.loads(p_q.read_text(encoding="utf-8"))
            q.pop("referencia_manual", None)
            q["decisoes"] = json.dumps(q.get("decisoes", {}), ensure_ascii=False)
            qualidades.append(q)
    if not etapas:
        return None
    todas = pd.concat(etapas, ignore_index=True)
    todas.to_csv(raiz / "etapas_todas.csv", index=False, float_format="%.3f")
    pd.DataFrame(qualidades).to_csv(raiz / "qualidade_sessoes.csv", index=False, float_format="%.2f")

    treino = todas[todas["uso_treino"] & (incluir_ensaios | ~todas["ensaio"])]
    colunas = ["voluntario", "sessao", "etapa", "nome", "vel", "idade", "sexo_m", "peso", "altura",
               "fc_repouso", "fc", "n_validos", "spm", "fc_modelo"]
    treino[colunas].to_csv(raiz / "tabela_treino.csv", index=False, float_format="%.2f")
    return todas, treino


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sessoes", nargs="*", help="pastas de sessão (padrão: todas em --raiz)")
    ap.add_argument("--raiz", default=str(cadastro.PASTA_REAIS), help="pasta dos dados reais")
    ap.add_argument("--sem-figura", action="store_true")
    ap.add_argument("--incluir-ensaios", action="store_true",
                    help="coloca as sessões de ensaio (simuladas) na tabela de treino")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    raiz = Path(args.raiz)
    if not raiz.exists():
        sys.exit(f"{raiz} não existe. Faça uma coleta com tools/coletar_sessao.py primeiro.")
    voluntarios = cadastro.carrega(raiz)
    pastas = [Path(p) for p in args.sessoes] or sessoes_em(raiz)
    for pasta in pastas:
        try:
            q = processa(pasta, voluntarios, not args.sem_figura)
        except (ValueError, FileNotFoundError, KeyError) as erro:
            print(f"{pasta.name}: IGNORADA ({erro})")
            continue
        perda = q.get("perda_pacotes_pct")
        print(f"{pasta.name}: BPM de {q['fonte_bpm']}, {q['duracao_s']} s, "
              f"cobertura {q['cobertura_bpm_pct']:.0f}%, "
              f"perda {'-' if perda is None else f'{perda:.1f}%'}, "
              f"{q['etapas_em_regime']} etapa(s) em regime"
              + (", ENSAIO" if q["ensaio"] else ""))
        if q["referencia_manual"] and q["referencia_manual"]["erro_medio_absoluto"] is not None:
            print(f"  pulso manual: erro médio {q['referencia_manual']['erro_medio_absoluto']:.1f} BPM "
                  f"em {q['referencia_manual']['n']} contagem(ns)")

    resultado = consolida(raiz, args.incluir_ensaios)
    if resultado is None:
        print("Nenhuma sessão tratada.")
        return
    todas, treino = resultado
    n_vol = treino["voluntario"].nunique()
    print(f"\nTabela de treino: {len(treino)} etapas em regime, {treino['sessao'].nunique()} sessões, "
          f"{n_vol} voluntário(s) -> {raiz / 'tabela_treino.csv'}")
    motivos = todas.loc[~todas["uso_treino"], "motivo_exclusao"].value_counts()
    if len(motivos):
        print("Etapas fora do treino: " + "; ".join(f"{m} ({n})" for m, n in motivos.items()))
    if n_vol < 3:
        print("Atenção: com menos de 3 voluntários, a avaliação do modelo com dados reais "
              "não é confiável.")


if __name__ == "__main__":
    main()
