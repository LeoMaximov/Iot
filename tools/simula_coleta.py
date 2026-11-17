"""Gera uma coleta SINTÉTICA para testar plot_coleta.py sem o hardware.

Os arquivos vão para dados/simulados/ (fora do Git) com "simulado": true no
_meta.json; o plot_coleta.py carimba "DADOS SIMULADOS" em toda figura feita a
partir deles. Nada disto é dado de sensor e não deve entrar no relatório.

O detector de passadas abaixo é uma cópia em Python do que roda em
emissora/emissora.ino (processaAceleracao), para conferir a lógica e os
limiares em um sinal conhecido antes dos testes reais.

Uso:
  python tools/simula_coleta.py --spm 140 --bpm 95 --duracao 60
  python tools/plot_coleta.py dados/simulados --saida dados/simulados/figuras
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Mesmos valores de emissora.ino.
LIMIAR_PASSO_G = 0.15
REFRATARIO_PASSO_MS = 250
INTERVALO_MAX_PASSO_MS = 2000
TIMEOUT_PASSO_MS = 3000
N_INTERVALOS = 6


def detecta_passadas(t_ms, magnitude):
    """Porta de processaAceleracao(): devolve (filtrado, passo, passos, spm) por amostra."""
    gravidade = magnitude[0]
    filtrado = 0.0
    armado = True
    ultimo_passo = 0
    passos = 0
    intervalos = []
    saida = []
    for agora, mag in zip(t_ms, magnitude):
        gravidade += 0.02 * (mag - gravidade)
        filtrado += 0.40 * ((mag - gravidade) - filtrado)
        passo = 0
        if armado and filtrado > LIMIAR_PASSO_G and (agora - ultimo_passo) >= REFRATARIO_PASSO_MS:
            passo, armado = 1, False
            intervalo = agora - ultimo_passo
            if ultimo_passo != 0 and intervalo <= INTERVALO_MAX_PASSO_MS:
                intervalos = (intervalos + [intervalo])[-N_INTERVALOS:]
            else:
                intervalos = []
            ultimo_passo = agora
            passos += 1
        elif not armado and filtrado < LIMIAR_PASSO_G * 0.5:
            armado = True
        if ultimo_passo != 0 and (agora - ultimo_passo) > TIMEOUT_PASSO_MS:
            intervalos = []
        spm = 60000.0 * len(intervalos) / sum(intervalos) if intervalos else 0.0
        saida.append((filtrado, passo, passos, spm))
    return map(np.array, zip(*saida))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spm", type=float, default=140)
    ap.add_argument("--bpm", type=float, default=95)
    ap.add_argument("--duracao", type=float, default=60, help="segundos")
    ap.add_argument("--perda", type=float, default=0.02, help="fração de pacotes perdidos")
    ap.add_argument("--saida", default="dados/simulados")
    ap.add_argument("--semente", type=int, default=1)
    args = ap.parse_args()

    rng = np.random.default_rng(args.semente)
    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    base = pasta / f"SIMULADO_{args.spm:.0f}spm"

    # --- Braçadeira, 50 Hz ---------------------------------------------------
    t_ms = np.arange(0, int(args.duracao * 1000), 20)
    t = t_ms / 1000.0
    f_passo = args.spm / 60.0
    impacto = np.clip(np.sin(2 * np.pi * f_passo * t), 0, None) ** 3
    balanco = 0.10 * np.sin(np.pi * f_passo * t)  # braço: metade da frequência
    mag = 1.0 + 0.6 * impacto + balanco + rng.normal(0, 0.03, len(t))
    filtrado, passo, passos, spm = detecta_passadas(t_ms, mag)

    f_bat = args.bpm / 60.0
    fase = 2 * np.pi * f_bat * t
    ir = (110000 + 600 * np.clip(np.sin(fase), 0, None) ** 2 + 150 * np.sin(2 * np.pi * 0.2 * t)
          + rng.normal(0, 25, len(t)))
    batimento = (np.diff(np.floor(fase / (2 * np.pi)), prepend=0) > 0).astype(int)
    bpm = np.where(t > 4, args.bpm + rng.normal(0, 1.5, len(t)), 0.0)
    lat_us = rng.gamma(4.0, 450.0, len(t)).astype(int)

    e = pd.DataFrame({
        "t_pc": t, "t_ms": t_ms, "ax": 0.1 * mag, "ay": 0.2 * mag, "az": 0.97 * mag,
        "mag": mag.round(3), "filtrado": filtrado.round(3), "passo": passo, "passos": passos,
        "spm": spm.round(1), "ir": ir.astype(int), "batimento": batimento, "bpm": bpm.round(1),
        "contato": 1, "lat_us": lat_us, "falhas": 0, "canal": 6,
    })
    e.to_csv(f"{base}_E.csv", index=False)

    # --- Painel, 10 Hz, com perdas --------------------------------------------
    r = e.iloc[::5].reset_index(drop=True)
    seq = np.arange(len(r))
    recebido = rng.random(len(r)) >= args.perda
    recebido[0] = True
    r, seq = r[recebido].reset_index(drop=True), seq[recebido]
    t_rx = r["t_ms"].to_numpy() + 3
    painel = pd.DataFrame({
        "t_pc": r["t_pc"], "t_rx_ms": t_rx, "seq": seq, "t_tx_ms": r["t_ms"],
        "dt_chegada_ms": np.diff(t_rx, prepend=t_rx[0]), "rssi": rng.normal(-52, 3, len(r)).round(),
        "perdidos": seq - np.arange(len(r)), "bpm": r["bpm"], "spm": r["spm"],
        "passos": r["passos"], "ir": r["ir"], "contato": 1, "lat_us": r["lat_us"],
        "falhas_envio": 0, "ax": r["ax"], "ay": r["ay"], "az": r["az"],
    })
    painel.to_csv(f"{base}_R.csv", index=False)

    # --- MQTT e ThingsBoard, 1 Hz ---------------------------------------------
    segundos = np.arange(1, int(args.duracao))
    pd.DataFrame({"t_pc": segundos, "t_ms": segundos * 1000, "ok": 1, "wifi_rssi": -48,
                  "pub_ok": segundos, "pub_falha": 0}).to_csv(f"{base}_M.csv", index=False)
    por_segundo = painel.iloc[::10]
    pd.DataFrame({"ts": 1_800_000_000_000 + por_segundo["t_rx_ms"].to_numpy(),
                  "bpm": por_segundo["bpm"].to_numpy(),
                  "spm": por_segundo["spm"].to_numpy()}).to_csv(f"{base}_TB.csv", index=False)

    meta = {"teste": f"SIMULADO {args.spm:.0f} SPM", "inicio": datetime.now().isoformat(timespec="seconds"),
            "ref_bpm": args.bpm, "ref_spm": args.spm, "nota": "Sinal sintético.", "simulado": True}
    Path(f"{base}_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                         encoding="utf-8")

    validos = spm[spm > 0]
    print(f"Arquivos em {base}_*.csv")
    print(f"Detector: {passos[-1]} passadas (esperado ~{args.spm * args.duracao / 60:.0f}), "
          f"SPM médio {validos.mean():.1f} (alvo {args.spm:g})")


if __name__ == "__main__":
    main()
