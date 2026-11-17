"""Exporta para CSV as séries temporais que o ThingsBoard armazenou.

Serve de evidência de que a telemetria publicada por MQTT chegou à plataforma.
O arquivo sai como <base>_TB.csv, no formato lido pelo plot_coleta.py.

Uso:
  # últimos 15 minutos, junto de uma coleta já gravada pelo serial_logger
  python tools/tb_export.py --minutos 15 --base dados/brutos/20261114_101500_T3_esteira

  # intervalo explícito (hora local)
  python tools/tb_export.py --inicio "2026-11-14 10:15" --fim "2026-11-14 10:30" --base dados/brutos/T3
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from tb_comum import ThingsBoard, adiciona_argumentos

BOOLEANOS = {"true": 1, "false": 0}
CHAVES_PADRAO ="bpm,spm,passos,rssi,perda_pct,latencia_ms,pacotes_rx,pacotes_perdidos"


def para_ms(texto):
    return int(datetime.fromisoformat(texto).timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    adiciona_argumentos(ap)
    ap.add_argument("--base", required=True, help="base do arquivo de saída (sem sufixo)")
    ap.add_argument("--minutos", type=float, help="exporta os últimos N minutos")
    ap.add_argument("--inicio", help='início, ex.: "2026-11-14 10:15"')
    ap.add_argument("--fim", help="fim (padrão: agora)")
    ap.add_argument("--chaves", default=CHAVES_PADRAO, help="chaves de telemetria, separadas por vírgula")
    args = ap.parse_args()

    fim = para_ms(args.fim) if args.fim else int(time.time() * 1000)
    if args.minutos:
        inicio = fim - int(args.minutos * 60_000)
    elif args.inicio:
        inicio = para_ms(args.inicio)
    else:
        ap.error("informe --minutos ou --inicio")

    tb = ThingsBoard(args.url, args.usuario, args.senha)
    id_dispositivo = tb.id_dispositivo(args.dispositivo)
    if not id_dispositivo:
        sys.exit(f'Dispositivo "{args.dispositivo}" não encontrado.')

    resposta = tb.get(f"/api/plugins/telemetry/DEVICE/{id_dispositivo}/values/timeseries",
                      keys=args.chaves, startTs=inicio, endTs=fim, limit=100000,
                      agg="NONE", orderBy="ASC")
    resposta.raise_for_status()
    dados = resposta.json()
    if not dados:
        sys.exit("Nenhum dado no período. Confira o intervalo e se o painel estava publicando.")

    series = []
    for chave, pontos in dados.items():
        # O ThingsBoard devolve booleanos como texto ("true"/"false"); viram 1/0.
        valores = {p["ts"]: BOOLEANOS.get(str(p["value"]).lower(), p["value"]) for p in pontos}
        serie = pd.Series(valores, name=chave)
        series.append(pd.to_numeric(serie, errors="coerce"))
    tabela = pd.concat(series, axis=1).sort_index()
    tabela.index.name = "ts"

    destino = Path(f"{args.base}_TB.csv")
    destino.parent.mkdir(parents=True, exist_ok=True)
    tabela.to_csv(destino)
    duracao = (tabela.index[-1] - tabela.index[0]) / 1000
    print(f"{destino}: {len(tabela)} pontos em {duracao:.0f} s, chaves: {', '.join(tabela.columns)}")


if __name__ == "__main__":
    main()
