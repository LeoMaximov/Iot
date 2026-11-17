"""Grava em CSV as linhas "CSV,<tipo>,..." que o firmware imprime na serial.

Tipos de registro:
  E  braçadeira (emissora), 50 Hz: sinal bruto do acelerômetro e do PPG
  R  painel (receptora), 10 Hz: cada pacote ESP-NOW recebido
  M  painel (receptora), 1 Hz: resultado de cada publicação MQTT

Para cada tipo que aparecer é criado um arquivo
  <saida>/<AAAAMMDD_HHMMSS>_<teste>_<tipo>.csv
com a coluna t_pc (segundos desde o início da gravação, relógio do PC) na frente.
As demais linhas (mensagens de diagnóstico) vão para o console e para um .log.

Exemplos:
  python tools/serial_logger.py --porta COM5 --teste T1_repouso --ref-bpm 72
  python tools/serial_logger.py --porta COM6 --teste T2_140spm --ref-spm 140 --duracao 60
  python tools/serial_logger.py --listar
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("Falta o pyserial. Instale com: pip install -r tools/requirements.txt")

# Usados quando o logger é ligado depois do boot e não vê as linhas CSVH do firmware.
CABECALHOS = {
    "E": "t_ms,ax,ay,az,mag,filtrado,passo,passos,spm,ir,batimento,bpm,contato,"
         "lat_us,falhas,canal".split(","),
    "R": "t_rx_ms,seq,t_tx_ms,dt_chegada_ms,rssi,perdidos,bpm,spm,passos,ir,contato,"
         "lat_us,falhas_envio,ax,ay,az".split(","),
    "M": "t_ms,ok,wifi_rssi,pub_ok,pub_falha".split(","),
}


def listar_portas():
    portas = list(list_ports.comports())
    if not portas:
        print("Nenhuma porta serial encontrada. A placa está conectada por um cabo USB de dados?")
    for p in portas:
        print(f"{p.device}\t{p.description}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listar", action="store_true", help="lista as portas seriais e sai")
    ap.add_argument("--porta", help="porta serial, ex.: COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--teste", default="teste", help="nome do teste, usado no nome do arquivo")
    ap.add_argument("--saida", default="dados/brutos", help="pasta de saída")
    ap.add_argument("--duracao", type=float, help="encerra sozinho após N segundos")
    ap.add_argument("--ref-bpm", type=float, help="BPM de referência (oxímetro, relógio...)")
    ap.add_argument("--ref-spm", type=float, help="SPM de referência (metrônomo)")
    ap.add_argument("--nota", default="", help="observação livre sobre o teste")
    ap.add_argument("--sem-reset", action="store_true",
                    help="não reinicia a placa ao abrir a porta")
    args = ap.parse_args()

    # O boot do ESP32 imprime bytes que o console do Windows não sabe exibir;
    # sem isto, um caractere estranho derrubaria a gravação.
    sys.stdout.reconfigure(errors="replace")

    if args.listar:
        listar_portas()
        return
    if not args.porta:
        ap.error("informe --porta (use --listar para ver as disponíveis)")

    pasta = Path(args.saida)
    pasta.mkdir(parents=True, exist_ok=True)
    inicio = datetime.now()
    base = pasta / f"{inicio:%Y%m%d_%H%M%S}_{args.teste}"

    porta = serial.Serial()
    porta.port = args.porta
    porta.baudrate = args.baud
    porta.timeout = 1
    if args.sem_reset:
        # Nas placas ESP32 comuns, DTR/RTS acionam o reset ao abrir a porta.
        porta.dtr = False
        porta.rts = False
    porta.open()

    meta = {
        "teste": args.teste,
        "inicio": inicio.isoformat(timespec="seconds"),
        "porta": args.porta,
        "ref_bpm": args.ref_bpm,
        "ref_spm": args.ref_spm,
        "nota": args.nota,
        "simulado": False,
    }
    Path(f"{base}_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                         encoding="utf-8")

    cabecalhos = {k: list(v) for k, v in CABECALHOS.items()}
    arquivos, escritores, contagem = {}, {}, {}
    log = open(f"{base}.log", "w", encoding="utf-8")
    t0 = time.monotonic()
    proximo_resumo = t0 + 5
    print(f"Gravando em {base}_<tipo>.csv  (Ctrl+C para encerrar)")

    try:
        while True:
            agora = time.monotonic()
            if args.duracao and (agora - t0) >= args.duracao:
                break
            bruto = porta.readline()
            if not bruto:
                continue
            linha = bruto.decode("utf-8", errors="replace").strip()
            campos = linha.split(",")

            if campos[0] == "CSVH" and len(campos) > 2:
                if campos[1] not in escritores:  # só vale antes da primeira linha de dados
                    cabecalhos[campos[1]] = campos[2:]
            elif campos[0] == "CSV" and len(campos) > 2:
                tipo, valores = campos[1], campos[2:]
                esperado = cabecalhos.get(tipo)
                if esperado is None or len(valores) != len(esperado):
                    log.write(f"[linha descartada] {linha}\n")  # linha truncada ou tipo novo
                    continue
                if tipo not in escritores:
                    arquivos[tipo] = open(f"{base}_{tipo}.csv", "w", newline="", encoding="utf-8")
                    escritores[tipo] = csv.writer(arquivos[tipo])
                    escritores[tipo].writerow(["t_pc"] + esperado)
                    contagem[tipo] = 0
                escritores[tipo].writerow([f"{agora - t0:.3f}"] + valores)
                contagem[tipo] += 1
            elif linha:
                print(f"  {linha}")
                log.write(linha + "\n")

            if agora >= proximo_resumo:
                proximo_resumo = agora + 5
                decorrido = agora - t0
                resumo = "  ".join(f"{t}: {n} linhas ({n / decorrido:.1f}/s)"
                                   for t, n in sorted(contagem.items()))
                print(f"[{decorrido:6.0f} s] {resumo or 'nenhuma linha CSV ainda'}")
    except KeyboardInterrupt:
        pass
    finally:
        porta.close()
        log.close()
        for f in arquivos.values():
            f.close()

    print("\nEncerrado.")
    for tipo, n in sorted(contagem.items()):
        print(f"  {base}_{tipo}.csv: {n} linhas")
    if not contagem:
        print("  Nenhuma linha CSV recebida: confira a porta, o baud e se o firmware V1 está gravado.")


if __name__ == "__main__":
    main()
