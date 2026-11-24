"""Cadastro de voluntários da coleta de dados reais (dados/reais/voluntarios.csv).

Cada pessoa é identificada só por um código (V01, V02...). O nome e o termo de
autorização ficam fora do projeto, com a equipe; aqui vai apenas a data em que
a autorização foi dada. Sem essa data, a coleta e o tratamento recusam a pessoa.

Colunas:
  id            código do voluntário (V01, V02...)
  idade         anos
  sexo          M ou F
  peso_kg       kg
  altura_cm     cm
  fc_repouso    BPM em repouso (pode ser sobrescrita na hora da sessão)
  vel_max       maior velocidade que o guia pode recomendar para a pessoa, km/h
  consentimento data da autorização (AAAA-MM-DD)
  observacao    texto livre (sem dados que identifiquem a pessoa)

Uso:
  python tools/voluntarios.py listar
  python tools/voluntarios.py adicionar --id V02 --idade 24 --sexo F --peso 58 \\
      --altura 165 --fc-repouso 66 --consentimento 2026-11-25
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_REAIS = RAIZ / "dados" / "reais"
COLUNAS = ["id", "idade", "sexo", "peso_kg", "altura_cm", "fc_repouso", "vel_max",
           "consentimento", "observacao"]


def caminho_cadastro(raiz=PASTA_REAIS):
    return Path(raiz) / "voluntarios.csv"


def cria_cadastro(raiz=PASTA_REAIS):
    caminho = caminho_cadastro(raiz)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if not caminho.exists():
        with open(caminho, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COLUNAS)
    return caminho


def carrega(raiz=PASTA_REAIS):
    """Dicionário id -> registro (com números já convertidos)."""
    caminho = caminho_cadastro(raiz)
    if not caminho.exists():
        return {}
    voluntarios = {}
    with open(caminho, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if not (linha.get("id") or "").strip():
                continue
            v = {k: (linha.get(k) or "").strip() for k in COLUNAS}
            for chave in ("idade",):
                v[chave] = int(float(v[chave]))
            for chave in ("peso_kg", "altura_cm", "fc_repouso"):
                v[chave] = float(v[chave])
            v["vel_max"] = float(v["vel_max"]) if v["vel_max"] else 12.0
            v["sexo"] = v["sexo"].upper()
            voluntarios[v["id"]] = v
    return voluntarios


def valida(voluntario):
    """Lista de problemas que impedem usar o voluntário (vazia = pode usar)."""
    problemas = []
    if not voluntario.get("consentimento"):
        problemas.append("sem data de consentimento registrada")
    else:
        try:
            date.fromisoformat(voluntario["consentimento"])
        except ValueError:
            problemas.append(f"data de consentimento inválida: {voluntario['consentimento']}")
    if voluntario.get("sexo") not in ("M", "F"):
        problemas.append("sexo deve ser M ou F")
    if not 10 <= voluntario.get("idade", 0) <= 80:
        problemas.append("idade fora de 10 a 80 anos")
    if not 30 <= voluntario.get("peso_kg", 0) <= 150:
        problemas.append("peso fora de 30 a 150 kg")
    if not 120 <= voluntario.get("altura_cm", 0) <= 215:
        problemas.append("altura fora de 120 a 215 cm")
    if not 35 <= voluntario.get("fc_repouso", 0) <= 110:
        problemas.append("FC de repouso fora de 35 a 110 BPM")
    return problemas


def obtem(id_voluntario, raiz=PASTA_REAIS):
    """Registro do voluntário, ou encerra com a explicação do problema."""
    voluntarios = carrega(raiz)
    if id_voluntario not in voluntarios:
        cria_cadastro(raiz)
        sys.exit(f'Voluntário "{id_voluntario}" não está em {caminho_cadastro(raiz)}. '
                 f"Cadastre com: python tools/voluntarios.py adicionar --id {id_voluntario} ...")
    v = voluntarios[id_voluntario]
    problemas = valida(v)
    if problemas:
        sys.exit(f"Voluntário {id_voluntario} não pode ser usado: " + "; ".join(problemas))
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", default=str(PASTA_REAIS), help="pasta dos dados reais")
    sub = ap.add_subparsers(dest="comando", required=True)
    sub.add_parser("listar")
    ad = sub.add_parser("adicionar")
    ad.add_argument("--id", required=True)
    ad.add_argument("--idade", type=int, required=True)
    ad.add_argument("--sexo", choices=["M", "F"], required=True)
    ad.add_argument("--peso", type=float, required=True, help="kg")
    ad.add_argument("--altura", type=float, required=True, help="cm")
    ad.add_argument("--fc-repouso", type=float, required=True)
    ad.add_argument("--vel-max", type=float, default=12.0)
    ad.add_argument("--consentimento", required=True, help="data da autorização, AAAA-MM-DD")
    ad.add_argument("--observacao", default="")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    if args.comando == "listar":
        voluntarios = carrega(args.raiz)
        if not voluntarios:
            print(f"Nenhum voluntário em {caminho_cadastro(args.raiz)}.")
        for v in voluntarios.values():
            problemas = valida(v)
            estado = "ok" if not problemas else "PENDENTE: " + "; ".join(problemas)
            print(f"{v['id']:5s} {v['idade']:3d} anos  {v['sexo']}  {v['peso_kg']:5.1f} kg  "
                  f"{v['altura_cm']:5.1f} cm  FC rep. {v['fc_repouso']:5.1f}  "
                  f"consent. {v['consentimento'] or '-':10s}  {estado}")
        return

    novo = {"id": args.id, "idade": args.idade, "sexo": args.sexo, "peso_kg": args.peso,
            "altura_cm": args.altura, "fc_repouso": args.fc_repouso, "vel_max": args.vel_max,
            "consentimento": args.consentimento, "observacao": args.observacao}
    problemas = valida(novo)
    if problemas:
        sys.exit("Cadastro recusado: " + "; ".join(problemas))
    if args.id in carrega(args.raiz):
        sys.exit(f"{args.id} já está cadastrado. Edite o CSV para alterar.")
    caminho = cria_cadastro(args.raiz)
    with open(caminho, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=COLUNAS).writerow(novo)
    print(f"{args.id} cadastrado em {caminho}")


if __name__ == "__main__":
    main()
