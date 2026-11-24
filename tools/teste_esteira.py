"""Teste real na esteira: confere tudo, mostra o plano, pede a confirmação de
segurança e roda a coleta completa (tools/coletar_sessao.py).

Verificações antes de começar (o teste só segue se todas passarem):
  1. TB_URL, TB_USUARIO e TB_SENHA definidas neste terminal;
  2. voluntário cadastrado, com data de consentimento;
  3. a placa na porta é o PAINEL (e não a braçadeira);
  4. o painel está recebendo a braçadeira, com contato na pele e BPM válido;
  5. o ThingsBoard está recebendo telemetria recente do painel.

Protocolo padrão: "validacao" (17 min): aquecer 3 min (zona 1), leve 4 min
(zona 2), moderado 4 min (zona 3), leve 3 min (zona 2), desaquecer 3 min.
Não passa da zona 3. Use --treino progressivo (21 min, com zona 4) só depois
de um teste de validação sem problemas.

Uso:
  python tools/teste_esteira.py --voluntario V01 --porta COM8 --fc-repouso 64
  python tools/teste_esteira.py --voluntario V01 --porta COM8 --treino progressivo
  python tools/teste_esteira.py --voluntario V01 --so-checar      # só as verificações
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FERRAMENTAS = RAIZ / "tools"
sys.path.insert(0, str(FERRAMENTAS))
sys.path.insert(0, str(RAIZ / "guia"))

import voluntarios as cadastro  # noqa: E402
from nucleo import LIMITE_SEGURANCA, TREINOS, ModeloFC, Perfil, gerar_plano  # noqa: E402

OK, FALHA, AVISO = "[ OK ]", "[FALHA]", "[AVISO]"


def mostra(estado, texto):
    print(f"  {estado} {texto}")
    return estado != FALHA


# ---------------------------------------------------------------------------
# Verificações
# ---------------------------------------------------------------------------
def checa_credenciais():
    faltando = [v for v in ("TB_URL", "TB_USUARIO", "TB_SENHA") if not os.environ.get(v)]
    if faltando:
        return mostra(FALHA, f"credenciais do ThingsBoard faltando neste terminal: {', '.join(faltando)}")
    if "localhost" in os.environ["TB_URL"]:
        return mostra(FALHA, "TB_URL aponta para localhost; use https://thingsboard.cloud")
    return mostra(OK, f"credenciais definidas ({os.environ['TB_URL']})")


def checa_porta(porta, segundos=10.0):
    """Lê a serial sem acionar o reset e diz qual placa é e se a braçadeira chega."""
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        return mostra(FALHA, "falta o pyserial (pip install -r tools/requirements.txt)"), None
    disponiveis = [p.device for p in list_ports.comports()]
    if porta not in disponiveis:
        return mostra(FALHA, f"{porta} não existe. Portas: {', '.join(disponiveis) or 'nenhuma'} "
                             "(o cabo transmite dados?)"), None

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = porta, 115200, 0.5
    s.dtr = False   # nas placas ESP32 comuns, DTR/RTS acionam o reset ao abrir a porta
    s.rts = False
    try:
        s.open()
    except serial.SerialException as erro:
        return mostra(FALHA, f"não consegui abrir {porta} ({erro}). Feche o monitor serial "
                             "do Arduino ou outro programa usando a porta."), None
    linhas, fim = [], time.monotonic() + segundos
    while time.monotonic() < fim:
        bruto = s.readline()
        if bruto:
            linhas.append(bruto.decode("utf-8", errors="replace").strip())
    s.close()

    reiniciou = any("POWERON_RESET" in l or "=== GCP-Esteira" in l for l in linhas)
    pacotes_r = [l.split(",") for l in linhas if l.startswith("CSV,R,")]
    if any(l.startswith("CSV,E,") or "Bracadeira" in l for l in linhas):
        return mostra(FALHA, f"{porta} é a BRAÇADEIRA. Conecte o painel no cabo de dados."), None
    if not pacotes_r and not any(l.startswith("CSV,M,") for l in linhas):
        return mostra(FALHA, f"{porta} não enviou dados do painel em {segundos:.0f} s. "
                             "A placa está ligada e com o firmware do painel?"), None
    ok = mostra(OK, f"{porta} é o painel" + (" (ele reiniciou ao abrir a porta; normal)"
                                              if reiniciou else ""))

    if not pacotes_r:
        return mostra(FALHA, "o painel não está recebendo a braçadeira (nenhum pacote ESP-NOW). "
                             "Ela está ligada e perto do painel?") and ok, None
    # Campos de CSV,R: t_rx_ms,seq,t_tx_ms,dt,rssi,perdidos,bpm,spm,passos,ir,contato,...
    try:
        contato = [int(p[12]) for p in pacotes_r]
        bpm = [float(p[8]) for p in pacotes_r]
        rssi = [int(p[6]) for p in pacotes_r]
    except (IndexError, ValueError):
        return mostra(AVISO, "pacotes da braçadeira em formato inesperado") and ok, None
    taxa = len(pacotes_r) / segundos
    frac_contato = sum(contato) / len(contato)
    ultimo_bpm = bpm[-1]
    ok &= mostra(OK if taxa >= 7 else AVISO,
                 f"braçadeira chegando ao painel: {taxa:.1f} pacotes/s (esperado ~10), "
                 f"RSSI {sorted(rssi)[len(rssi) // 2]} dBm")
    if frac_contato < 0.8:
        ok &= mostra(FALHA, f"contato com a pele em só {frac_contato:.0%} dos pacotes. "
                            "Ajuste a braçadeira (sensor apoiado, sem folga).")
    elif not 40 <= ultimo_bpm <= 220:
        ok &= mostra(AVISO, f"contato ok, mas o BPM ainda não estabilizou ({ultimo_bpm:.0f}). "
                            "Espere 20 s parado e rode de novo.")
    else:
        ok &= mostra(OK, f"contato com a pele em {frac_contato:.0%} dos pacotes, BPM {ultimo_bpm:.0f}")
    return ok, ultimo_bpm


def checa_thingsboard(dispositivo, tentativas=6):
    from tb_comum import ThingsBoard
    try:
        tb = ThingsBoard(os.environ["TB_URL"], os.environ["TB_USUARIO"], os.environ["TB_SENHA"])
    except SystemExit as erro:
        return mostra(FALHA, f"ThingsBoard: {erro}")
    id_disp = tb.id_dispositivo(dispositivo)
    if not id_disp:
        return mostra(FALHA, f'dispositivo "{dispositivo}" não encontrado no ThingsBoard')
    # O painel pode ter acabado de reiniciar: espera até ~30 s a telemetria voltar.
    idade = None
    for i in range(tentativas):
        r = tb.get(f"/api/plugins/telemetry/DEVICE/{id_disp}/values/timeseries", keys="bpm,enlace")
        dados = r.json() if r.status_code == 200 else {}
        ponto = (dados.get("enlace") or dados.get("bpm") or [{}])[0]
        if "ts" in ponto:
            idade = time.time() - ponto["ts"] / 1000
            if idade < 10:
                break
        if i < tentativas - 1:
            time.sleep(5)
    if idade is None:
        return mostra(FALHA, "o ThingsBoard não tem telemetria do painel")
    if idade >= 10:
        return mostra(FALHA, f"última telemetria do painel tem {idade:.0f} s. O painel está no "
                             "Wi-Fi (hotspot ligado, 2,4 GHz) e com o ponto do MQTT verde?")
    return mostra(OK, f"ThingsBoard recebendo o painel (última mensagem há {idade:.0f} s)")


def mostra_plano(v, fc_repouso, treino, modelo_arquivo):
    perfil = Perfil(v["idade"], fc_repouso, v["sexo"], v["peso_kg"], v["altura_cm"], v["vel_max"])
    modelo = ModeloFC(modelo_arquivo) if modelo_arquivo else ModeloFC()
    plano = gerar_plano(perfil, treino, modelo)
    total = sum(e.duracao for e in plano)
    print(f"\nPlano '{treino}' para {v['id']} ({total // 60} min {total % 60} s), "
          f"FC máx. estimada {perfil.fc_max:.0f} BPM, "
          f"alerta acima de {LIMITE_SEGURANCA * perfil.fc_max:.0f} BPM:")
    t = 0
    for i, e in enumerate(plano, 1):
        print(f"  {t // 60:2d}:{t % 60:02d}  {i}. {e.nome:10s} {e.vel:4.1f} km/h  "
              f"{e.duracao // 60} min {e.duracao % 60:02d} s  alvo {e.bpm_min:.0f}-{e.bpm_max:.0f} BPM")
        t += e.duracao
    print("  O guia pode mudar as velocidades durante o treino, conforme a FC medida.")
    return max(e.vel for e in plano)


def confirma_seguranca(vel_max_plano):
    print(f"""
Antes de começar, confirme (a velocidade pode chegar a {vel_max_plano:.1f} km/h):
  - alguém acompanha ao lado da esteira, com o botão de parada ao alcance;
  - a chave de segurança (clipe) da esteira está presa na roupa de quem corre;
  - o voluntário sabe que deve reduzir a velocidade na hora se o painel mostrar
    "FC ALTA", ou se sentir tontura, dor no peito ou falta de ar fora do normal;
  - a velocidade da esteira é ajustada À MÃO, seguindo o painel. Se não der para
    seguir a velocidade do guia, anote o tempo e a velocidade usada.
  - Ctrl+C neste terminal encerra o guia (a gravação e a exportação seguem).""")
    resposta = input("Tudo certo para começar? Digite 'sim': ").strip().lower()
    return resposta == "sim"


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--voluntario", required=True)
    ap.add_argument("--porta", default="COM8", help="porta serial do painel")
    ap.add_argument("--treino", choices=sorted(TREINOS), default="validacao")
    ap.add_argument("--fc-repouso", type=float,
                    help="FC de repouso medida hoje (3 min sentado); padrão: a do cadastro")
    ap.add_argument("--modelo", help="arquivo .joblib do modelo usado pelo guia")
    ap.add_argument("--nota", default="", help="local, condições, ocorrências")
    ap.add_argument("--dispositivo", default="GCP-Esteira Painel")
    ap.add_argument("--raiz", default=str(cadastro.PASTA_REAIS))
    ap.add_argument("--so-checar", action="store_true", help="faz só as verificações e sai")
    ap.add_argument("--forcar", action="store_true",
                    help="segue mesmo com verificação falhando (não recomendado)")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace", line_buffering=True)

    print("Verificações:")
    tudo_ok = checa_credenciais()
    v = None
    try:
        v = cadastro.obtem(args.voluntario, args.raiz)
        tudo_ok &= mostra(OK, f"voluntário {v['id']} cadastrado, consentimento em {v['consentimento']}")
    except SystemExit as erro:
        tudo_ok = mostra(FALHA, str(erro)) and tudo_ok
    ok_porta, _ = checa_porta(args.porta)
    tudo_ok &= ok_porta
    if os.environ.get("TB_URL") and os.environ.get("TB_SENHA"):
        tudo_ok &= checa_thingsboard(args.dispositivo)

    if v is None:
        sys.exit("\nCadastre o voluntário antes do teste (python tools/voluntarios.py adicionar ...).")
    fc_repouso = args.fc_repouso or v["fc_repouso"]
    vel_max_plano = mostra_plano(v, fc_repouso, args.treino, args.modelo)

    if not tudo_ok:
        print("\nAlguma verificação falhou. Corrija e rode de novo.")
        if not args.forcar:
            sys.exit(1)
        print("--forcar: seguindo mesmo assim.")
    else:
        print("\nTodas as verificações passaram.")
    if args.so_checar:
        return
    if not confirma_seguranca(vel_max_plano):
        sys.exit("Teste cancelado.")

    comando = [sys.executable, str(FERRAMENTAS / "coletar_sessao.py"),
               "--voluntario", v["id"], "--treino", args.treino, "--escala", "1.0",
               "--porta", args.porta, "--fc-repouso", str(fc_repouso), "--raiz", args.raiz,
               "--nota", f"teste na esteira; {args.nota}".strip("; ")]
    if args.modelo:
        comando += ["--modelo", args.modelo]
    print("\nIniciando a coleta. Suba na esteira quando o guia mostrar a primeira etapa.\n")
    # O Ctrl+C chega também ao coletar_sessao, que encerra o guia e segue para a
    # exportação; daqui só esperamos ele terminar.
    from coletar_sessao import roda_ate_terminar
    sys.exit(roda_ate_terminar(comando))


if __name__ == "__main__":
    main()
