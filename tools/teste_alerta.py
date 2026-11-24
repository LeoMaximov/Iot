"""Teste de bancada do alerta local (A6) do painel.

Envia ao painel um definirEtapa com um limite de alerta baixo, para o alerta
disparar com uma FC normal em repouso (por exemplo, o dedo no sensor).
Não controla a esteira: o painel só mostra informações.

Uso (com TB_URL, TB_USUARIO e TB_SENHA definidos no terminal):
  python tools/teste_alerta.py --limite 60 --segundos 60
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tb_comum import ThingsBoard, adiciona_argumentos  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    adiciona_argumentos(ap)
    ap.add_argument("--limite", type=int, default=60,
                    help="limite do alerta em BPM (abaixo da FC atual para disparar)")
    ap.add_argument("--segundos", type=int, default=60,
                    help="tempo que a etapa de teste fica ativa no painel")
    args = ap.parse_args()

    tb = ThingsBoard(args.url, args.usuario, args.senha)
    dispositivo = tb.id_dispositivo(args.dispositivo)
    if dispositivo is None:
        sys.exit(f'Dispositivo "{args.dispositivo}" não encontrado.')

    def rpc(metodo, params):
        resposta = tb.sessao.post(f"{tb.url}/api/rpc/oneway/{dispositivo}",
                                  json={"method": metodo, "params": params}, timeout=10)
        print(f"RPC {metodo}: HTTP {resposta.status_code}")

    rpc("definirEtapa", {"nome": "TESTE", "vel": 6.0, "dur": args.segundos,
                         "zmin": 0, "zmax": 250, "i": 1, "n": 1,
                         "alerta": False, "limite_alerta": args.limite})
    print(f"Alerta com limite {args.limite} BPM ativo por {args.segundos} s no painel.")
    time.sleep(args.segundos)
    rpc("reiniciarTreino", {})
    print("Teste encerrado: painel voltou ao layout normal.")


if __name__ == "__main__":
    main()
