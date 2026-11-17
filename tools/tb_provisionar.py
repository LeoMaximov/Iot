"""Cria (se ainda não existir) o dispositivo do painel no ThingsBoard e mostra o token.

O token impresso vai em TB_TOKEN no arquivo receptora/secrets.h.

Uso:
  python tools/tb_provisionar.py
  python tools/tb_provisionar.py --url http://localhost:8080 --dispositivo "GCP-Esteira Painel"
"""

import argparse

from tb_comum import ThingsBoard, adiciona_argumentos


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    adiciona_argumentos(ap)
    args = ap.parse_args()

    tb = ThingsBoard(args.url, args.usuario, args.senha)
    id_dispositivo = tb.id_dispositivo(args.dispositivo)
    if id_dispositivo:
        print(f'Dispositivo "{args.dispositivo}" já existe.')
    else:
        id_dispositivo = tb.post("/api/device", {"name": args.dispositivo, "type": "default"})["id"]["id"]
        print(f'Dispositivo "{args.dispositivo}" criado.')

    resposta = tb.get(f"/api/device/{id_dispositivo}/credentials")
    resposta.raise_for_status()
    credenciais = resposta.json()
    print(f"ID:    {id_dispositivo}")
    print(f"Token: {credenciais['credentialsId']}")
    print("\nCopie o token para TB_TOKEN em receptora/secrets.h.")


if __name__ == "__main__":
    main()
