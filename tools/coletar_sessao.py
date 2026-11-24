"""Conduz uma sessão de coleta completa e guarda tudo em uma pasta.

Passos, em ordem:
  1. confere o voluntário no cadastro (com data de consentimento);
  2. cria dados/reais/<AAAAMMDD_HHMMSS>_<voluntario>_<treino>/ com metadados.json;
  3. se --porta foi dada, grava a serial do painel (serial_logger.py) em segundo plano;
  4. roda o guia ao vivo (guia/executar.py ao-vivo) com o perfil do voluntário;
  5. encerra a gravação serial e exporta a telemetria do ThingsBoard do período;
  6. trata a sessão (preparar_dados_reais.py).

As credenciais do ThingsBoard vêm de TB_URL, TB_USUARIO e TB_SENHA, definidas no
terminal antes de rodar. Ctrl+C durante o treino encerra o guia e segue para a
exportação, sem perder o que já foi registrado.

Uso:
  python tools/coletar_sessao.py --voluntario V01 --treino progressivo --porta COM8
  python tools/coletar_sessao.py --voluntario V01 --fc-repouso 64     # sem serial
  python tools/coletar_sessao.py --voluntario V01 --ensaio --escala 0.1  # sem hardware nem nuvem
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FERRAMENTAS = RAIZ / "tools"
sys.path.insert(0, str(FERRAMENTAS))
sys.path.insert(0, str(RAIZ / "guia"))

import voluntarios as cadastro  # noqa: E402
from nucleo import TREINOS  # noqa: E402

CHAVES_TB = ("bpm,spm,passos,contato,enlace,ir,rssi,perda_pct,latencia_ms,falhas_envio,"
             "pacotes_rx,pacotes_perdidos,alerta_local,etapa,vel_alvo,guia_etapa,guia_vel")


def roda_ate_terminar(comando):
    """Roda o comando e espera ele terminar, mesmo com Ctrl+C.

    O Ctrl+C chega ao filho também; subprocess.run o mataria 0,25 s depois, antes
    de o guia salvar o registro. Aqui o filho decide quando terminar.
    """
    processo = subprocess.Popen(comando)
    while True:
        try:
            return processo.wait()
        except KeyboardInterrupt:
            print("\nCtrl+C recebido: esperando o guia salvar o registro...")


def versao_codigo():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ,
                              capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def salva_json(caminho, dados):
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


def cria_modelos_de_anotacao(pasta):
    """Arquivos para preencher à mão depois da sessão (o tratamento usa se tiverem linhas)."""
    (pasta / "referencia_manual.csv").write_text(
        "t_s,bpm,nota\n", encoding="utf-8")   # pulso contado à mão: t_s = tempo do guia
    (pasta / "velocidade_real.csv").write_text(
        "t_s,vel,nota\n", encoding="utf-8")   # quando a esteira não ficou na velocidade do guia


def confere_credenciais(ensaio):
    if ensaio:
        return
    url = os.environ.get("TB_URL", "")
    faltando = [v for v in ("TB_URL", "TB_USUARIO", "TB_SENHA") if not os.environ.get(v)]
    if faltando or "localhost" in url:
        sys.exit("Defina TB_URL, TB_USUARIO e TB_SENHA neste terminal antes da coleta "
                 f"(faltando: {', '.join(faltando) or 'TB_URL aponta para localhost'}).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voluntario", required=True, help="código no cadastro, ex.: V01")
    ap.add_argument("--treino", choices=sorted(TREINOS), default="progressivo",
                    help="progressivo tem etapas longas, melhores para treinar o modelo")
    ap.add_argument("--escala", type=float, default=1.0, help="multiplica a duração das etapas")
    ap.add_argument("--fc-repouso", type=float, help="FC de repouso medida hoje (sobrescreve o cadastro)")
    ap.add_argument("--porta", help="porta serial do painel, ex.: COM8 (opcional)")
    ap.add_argument("--espera", type=float, default=20.0,
                    help="segundos entre ligar a gravação serial e começar o guia")
    ap.add_argument("--modelo", help="arquivo .joblib do modelo usado pelo guia")
    ap.add_argument("--nota", default="", help="observação livre (local, condições, ocorrências)")
    ap.add_argument("--raiz", default=str(cadastro.PASTA_REAIS), help="pasta dos dados reais")
    ap.add_argument("--ensaio", action="store_true",
                    help="testa o fluxo com usuário simulado, sem hardware nem ThingsBoard; "
                         "a sessão fica marcada e não entra no treino")
    ap.add_argument("--sem-tratamento", action="store_true", help="não roda o tratamento no fim")
    args = ap.parse_args()
    # line_buffering: as mensagens daqui saem na ordem certa entre as dos subprocessos.
    sys.stdout.reconfigure(errors="replace", line_buffering=True)

    confere_credenciais(args.ensaio)
    v = cadastro.obtem(args.voluntario, args.raiz)
    fc_repouso = args.fc_repouso or v["fc_repouso"]

    inicio = datetime.now()
    nome = f"{inicio:%Y%m%d_%H%M%S}_{v['id']}_{args.treino}" + ("_ENSAIO" if args.ensaio else "")
    pasta = Path(args.raiz) / nome
    pasta.mkdir(parents=True, exist_ok=False)
    cria_modelos_de_anotacao(pasta)

    meta = {
        "sessao": nome,
        "voluntario": v["id"],
        "treino": args.treino,
        "escala": args.escala,
        "ensaio": args.ensaio,
        "perfil": {"idade": v["idade"], "sexo": v["sexo"], "peso_kg": v["peso_kg"],
                   "altura_cm": v["altura_cm"], "fc_repouso": fc_repouso, "vel_max": v["vel_max"]},
        "inicio": inicio.isoformat(timespec="seconds"),
        "inicio_epoch_ms": int(inicio.timestamp() * 1000),
        "fim": None,
        "porta_painel": args.porta,
        "modelo": args.modelo or "guia/modelo_fc.joblib",
        "versao_codigo": versao_codigo(),
        "nota": args.nota,
        "estado": "em andamento",
    }
    salva_json(pasta / "metadados.json", meta)
    print(f"Sessão {nome}\n  pasta: {pasta}")

    # 1) Gravação serial do painel, em segundo plano.
    logger, sinal_parada = None, pasta / ".parar_serial"
    if args.porta and not args.ensaio:
        logger = subprocess.Popen([sys.executable, str(FERRAMENTAS / "serial_logger.py"),
                                   "--porta", args.porta, "--teste", "painel", "--saida", str(pasta),
                                   "--sem-reset", "--parar-se-existir", str(sinal_parada)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        print(f"  gravando a serial do painel ({args.porta}); aguardando {args.espera:.0f} s "
              "para a placa estabilizar...")
        time.sleep(args.espera)
        if logger.poll() is not None:
            print("  AVISO: a gravação serial parou logo no início (porta ocupada ou errada?). "
                  "A sessão segue só com o ThingsBoard.")
            logger = None

    # 2) Guia.
    comando = [sys.executable, str(RAIZ / "guia" / "executar.py"),
               "simular" if args.ensaio else "ao-vivo",
               "--treino", args.treino, "--escala", str(args.escala),
               "--idade", str(v["idade"]), "--fc-repouso", str(fc_repouso), "--sexo", v["sexo"],
               "--peso", str(v["peso_kg"]), "--altura", str(v["altura_cm"]),
               "--vel-max", str(v["vel_max"]), "--saida", str(pasta)]
    if args.modelo:
        comando += ["--modelo", args.modelo]
    print("\n--- Guia ---")
    codigo_guia = roda_ate_terminar(comando)
    fim = datetime.now()

    # 3) Encerra a gravação serial.
    if logger is not None:
        sinal_parada.touch()
        try:
            logger.wait(timeout=15)
        except subprocess.TimeoutExpired:
            logger.terminate()
            print("  AVISO: a gravação serial não encerrou sozinha e foi interrompida.")
        sinal_parada.unlink(missing_ok=True)

    # 4) Exporta o ThingsBoard do período (com folga nas pontas).
    codigo_tb = None
    if not args.ensaio:
        print("\n--- Exportação do ThingsBoard ---")
        codigo_tb = subprocess.run([
            sys.executable, str(FERRAMENTAS / "tb_export.py"),
            "--base", str(pasta / "thingsboard"),
            "--inicio", f"{inicio - timedelta(seconds=60):%Y-%m-%d %H:%M:%S}",
            "--fim", f"{fim + timedelta(seconds=30):%Y-%m-%d %H:%M:%S}",
            "--chaves", CHAVES_TB]).returncode
        if codigo_tb != 0:
            print("  AVISO: exportação falhou. Repita depois (o ThingsBoard guarda 30 dias):\n"
                  f'  python tools/tb_export.py --base "{pasta / "thingsboard"}" '
                  f'--inicio "{inicio - timedelta(seconds=60):%Y-%m-%d %H:%M:%S}" '
                  f'--fim "{fim + timedelta(seconds=30):%Y-%m-%d %H:%M:%S}" --chaves {CHAVES_TB}')

    meta.update({"fim": fim.isoformat(timespec="seconds"),
                 "duracao_s": round((fim - inicio).total_seconds()),
                 "codigo_guia": codigo_guia, "codigo_exportacao": codigo_tb,
                 "serial_gravada": any(pasta.glob("*_R.csv")),
                 "estado": "concluida" if codigo_guia == 0 else "interrompida"})
    salva_json(pasta / "metadados.json", meta)

    # 5) Tratamento.
    if not args.sem_tratamento:
        print("\n--- Tratamento ---")
        subprocess.run([sys.executable, str(FERRAMENTAS / "preparar_dados_reais.py"),
                        "--raiz", args.raiz, str(pasta)])

    print(f"\nSessão salva em {pasta}")
    print("Se houve contagem manual de pulso ou a esteira ficou fora da velocidade do guia,\n"
          "preencha referencia_manual.csv / velocidade_real.csv na pasta e rode de novo:\n"
          f'  python tools/preparar_dados_reais.py "{pasta}"')


if __name__ == "__main__":
    main()
