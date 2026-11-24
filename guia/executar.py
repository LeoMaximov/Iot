"""Executa o guia de cardio do GCP-Esteira.

Modos:
  plano       mostra as etapas geradas para o perfil e o tipo de treino
  simular     sessão completa com um usuário SIMULADO (malha fechada, sem hardware)
  reproduzir  passa uma coleta real gravada (<base>_R.csv) pelo condutor
  ao-vivo     lê a telemetria do ThingsBoard e envia cada etapa ao painel por RPC

Exemplos:
  python guia/executar.py plano --treino hiit --idade 22 --fc-repouso 62
  python guia/executar.py simular --treino hiit --idade 22 --fc-repouso 62
  python guia/executar.py reproduzir dados/brutos/20261115_214759_T3_efetivo --treino continuo --escala 0.1
  python guia/executar.py ao-vivo --treino hiit --idade 22 --fc-repouso 62 --escala 0.2

Cada sessão grava <saida>/<nome>_sessao.csv (uma linha por segundo),
<nome>_eventos.csv (etapas, decisões, alertas) e <nome>.png.
"""

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nucleo import LIMITE_SEGURANCA, TREINOS, Condutor, ModeloFC, Perfil, atividade_por_spm, gerar_plano  # noqa: E402
from nucleo import _hiit as nucleo_hiit  # noqa: E402
from tb_comum import URL_PADRAO, USUARIO_PADRAO, SENHA_PADRAO, DISPOSITIVO_PADRAO  # noqa: E402


# ---------------------------------------------------------------------------
# Registro da sessão
# ---------------------------------------------------------------------------
class Registro:
    def __init__(self, relogio=None):
        self.linhas, self.eventos = [], []
        # Ao vivo, relogio = time.time: cada linha ganha ts_ms (horário absoluto),
        # que alinha a sessão com a serial e com o ThingsBoard no tratamento.
        self.relogio = relogio

    def _carimbo(self):
        return {"ts_ms": int(self.relogio() * 1000)} if self.relogio else {}

    def amostra(self, t, bpm, spm, condutor):
        e = condutor.etapa
        self.linhas.append({
            "t": round(t, 1), **self._carimbo(), "bpm": round(bpm, 1), "spm": round(spm, 1),
            "etapa": condutor.indice + 1, "nome": e.nome, "zona": e.zona, "vel": e.vel,
            "bpm_min": round(e.bpm_min, 1), "bpm_max": round(e.bpm_max, 1),
            "situacao": condutor.situacao_zona(bpm), "atividade": atividade_por_spm(spm),
        })

    def evento(self, ev, eco=True):
        self.eventos.append({"t": round(ev.t, 1), **self._carimbo(), "tipo": ev.tipo, "texto": ev.texto,
                             "decisao": ev.dados.get("decisao", "")})
        if eco:
            print(f"[{ev.t:6.0f} s] {ev.tipo.upper():8s} {ev.texto}")

    def salva(self, base: Path):
        base.parent.mkdir(parents=True, exist_ok=True)
        for sufixo, dados in (("_sessao.csv", self.linhas), ("_eventos.csv", self.eventos)):
            if dados:
                with open(f"{base}{sufixo}", "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=list(dados[0]))
                    w.writeheader()
                    w.writerows(dados)
        print(f"Registro: {base}_sessao.csv e {base}_eventos.csv")

    def resumo(self):
        com_leitura = [l for l in self.linhas if l["situacao"] != "sem_leitura"]
        na_zona = sum(1 for l in com_leitura if l["situacao"] == "na_zona")
        decisoes = [e["decisao"] for e in self.eventos if e["tipo"] == "decisao"]
        return {
            "duracao_s": len(self.linhas),
            "cobertura_bpm_pct": 100 * len(com_leitura) / max(len(self.linhas), 1),
            "tempo_na_zona_pct": 100 * na_zona / max(len(com_leitura), 1),
            "decisoes": {d: decisoes.count(d) for d in sorted(set(decisoes))},
            "alertas": sum(1 for e in self.eventos if e["tipo"] == "alerta"),
        }


def figura(registro, base: Path, titulo, carimbo=None):
    import matplotlib.pyplot as plt
    import pandas as pd
    import plot_coleta as estilo   # mesmo estilo das figuras da V1

    df = pd.DataFrame(registro.linhas)
    ev = pd.DataFrame(registro.eventos)
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(8, 6.6), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1.4, 1.4]})

    a1.fill_between(df["t"], df["bpm_min"], df["bpm_max"], step="post", color=estilo.AZUL,
                    alpha=0.15, linewidth=0, label="Zona-alvo da etapa")
    a1.plot(df["t"], df["bpm"].where(df["bpm"] > 0), color=estilo.AZUL, label="BPM medido")
    if len(ev):
        cores = {"reduzir": estilo.LARANJA, "aumentar": estilo.TINTA, "manter": estilo.APAGADO,
                 "manter_sem_dados": estilo.APAGADO, "manter_bpm_suspeito": estilo.LARANJA,
                 "manter_etapa_curta": estilo.APAGADO}
        rotulos = {"reduzir": "Decisão: reduzir", "aumentar": "Decisão: aumentar",
                   "manter": "Decisão: manter", "manter_sem_dados": "Sem leitura: manter",
                   "manter_bpm_suspeito": "BPM suspeito: manter",
                   "manter_etapa_curta": "Etapa curta: manter"}
        marcas = {"reduzir": "v", "aumentar": "^", "manter": "o", "manter_sem_dados": "x",
                  "manter_bpm_suspeito": "s", "manter_etapa_curta": "D"}
        topo = df["bpm_max"].max() + 6
        for decisao, grupo in ev[ev["tipo"] == "decisao"].groupby("decisao"):
            a1.plot(grupo["t"], [topo] * len(grupo), linestyle="none", marker=marcas[decisao],
                    markersize=7, color=cores[decisao], label=rotulos[decisao])
        for t in ev.loc[ev["tipo"] == "alerta", "t"]:
            a1.axvline(t, color=estilo.LARANJA, linewidth=1.5)
    a1.set_ylabel("BPM")
    a1.set_title(titulo, pad=30)
    a1.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncols=4, fontsize=8, borderaxespad=0.2)

    a2.step(df["t"], df["vel"], where="post", color=estilo.AZUL)
    a2.set_ylabel("Velocidade\nrecomendada (km/h)")
    a3.plot(df["t"], df["spm"], color=estilo.AZUL, linewidth=1.0)
    a3.set_ylabel("Cadência (SPM)")
    a3.set_xlabel("Tempo (s)")
    fig.align_ylabels()
    if carimbo:
        fig.text(0.5, 0.5, carimbo, fontsize=38, color="#d03b3b", alpha=0.22, ha="center",
                 va="center", rotation=25, weight="bold")
    fig.savefig(f"{base}.png", bbox_inches="tight")
    plt.close(fig)
    print(f"Figura: {base}.png")


# ---------------------------------------------------------------------------
# Fontes de dados
# ---------------------------------------------------------------------------
class UsuarioSimulado:
    """Resposta cardíaca de 1ª ordem à velocidade recomendada, com um sensor ruidoso.

    A relação FC-velocidade "verdadeira" é de propósito diferente da do modelo,
    para que o condutor tenha o que corrigir. Nada aqui é dado medido.
    """

    def __init__(self, perfil, semente=1, resposta=1.0):
        import numpy as np
        self.rng = np.random.default_rng(semente)
        self.perfil = perfil
        self.resposta = resposta   # > 1: FC sobe mais por km/h (menos condicionado)
        self.fc = perfil.fc_repouso + 15

    def leitura(self, vel):
        p = self.perfil
        fracao = min(1.05, 0.30 + 0.085 * self.resposta * (vel - 3.0))
        regime = p.fc_repouso + (p.fc_max - p.fc_repouso) * fracao
        tau = 25.0 if regime > self.fc else 40.0
        self.fc += (regime - self.fc) / tau
        bpm = 0.0 if self.rng.random() < 0.25 else self.fc + self.rng.normal(0, 3)
        spm = (100 + 8 * (vel - 3)) if vel <= 6.5 else (158 + 6 * (vel - 7))
        return bpm, spm + self.rng.normal(0, 3)


def conduz(condutor, registro, fonte, ao_abrir_etapa=None, tempo_real=False):
    """Laço de 1 Hz. fonte(t, condutor) devolve (bpm, spm) ou None para encerrar."""
    t = 0.0
    inicio = time.monotonic()
    while not condutor.terminado:
        if condutor.indice < 0:
            leitura = (0.0, 0.0)
        else:
            leitura = fonte(t, condutor)
            if leitura is None:
                print("Fim dos dados antes do fim do treino.")
                break
        for ev in condutor.passo(t, *leitura):
            registro.evento(ev)
            if ao_abrir_etapa and ev.tipo in ("etapa", "fim"):
                ao_abrir_etapa(ev, condutor)
        if condutor.etapa and not condutor.terminado:
            registro.amostra(t, *leitura, condutor)
        t += 1.0
        if tempo_real:
            time.sleep(max(0.0, inicio + t - time.monotonic()))


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------
def modo_plano(args, perfil, modelo):
    print(f"FC máx. estimada: {perfil.fc_max:.0f} BPM | FC repouso: {perfil.fc_repouso:.0f} | "
          f"modelo: {'treinado' if modelo.treinado else 'reserva heurística (sem treino)'}")
    for k in range(1, 6):
        a, b = perfil.zona(k)
        print(f"  Zona {k}: {a:.0f} a {b:.0f} BPM -> {modelo.velocidade_para(perfil, (a + b) / 2):.1f} km/h")
    total = 0
    print(f"\nTreino '{args.treino}':")
    for i, e in enumerate(gerar_plano(perfil, args.treino, modelo, args.escala), 1):
        total += e.duracao
        print(f"  {i:2d}. {e.nome:10s} zona {e.zona}  {e.vel:4.1f} km/h  {e.duracao:4d} s  "
              f"alvo {e.bpm_min:.0f}-{e.bpm_max:.0f} BPM")
    print(f"  Total: {total // 60} min {total % 60} s")


def modo_simular(args, perfil, modelo):
    condutor, registro = Condutor(perfil, args.treino, modelo, args.escala), Registro()
    usuario = UsuarioSimulado(perfil, args.semente, args.resposta)
    conduz(condutor, registro, lambda t, c: usuario.leitura(c.etapa.vel))
    base = Path(args.saida) / f"SIMULADO_{args.treino}_resposta{args.resposta:g}"
    registro.salva(base)
    figura(registro, base, f"Sessão simulada — {args.treino}, resposta {args.resposta:g}",
           carimbo="SIMULAÇÃO")
    print(registro.resumo())


def modo_reproduzir(args, perfil, modelo):
    import pandas as pd
    r = pd.read_csv(f"{args.base}_R.csv")
    # Uma leitura por segundo: mediana do BPM válido e do SPM em cada segundo.
    r["s"] = (r["t_pc"] - r["t_pc"].iloc[0]).astype(int)
    por_s = r.groupby("s").agg(bpm=("bpm", lambda x: x[x > 0].median() if (x > 0).any() else 0.0),
                               spm=("spm", "median"))
    condutor, registro = Condutor(perfil, args.treino, modelo, args.escala), Registro()

    def fonte(t, _c):
        s = int(t)
        return (float(por_s.loc[s, "bpm"]), float(por_s.loc[s, "spm"])) if s in por_s.index else None

    conduz(condutor, registro, fonte)
    base = Path(args.saida) / f"{Path(args.base).name}_guia_{args.treino}"
    registro.salva(base)
    figura(registro, base, f"Coleta real reproduzida no condutor — {Path(args.base).name}")
    print(registro.resumo())


def modo_ao_vivo(args, perfil, modelo):
    from tb_comum import ThingsBoard
    tb = ThingsBoard(args.url, args.usuario, args.senha)
    dispositivo = tb.id_dispositivo(args.dispositivo)
    if not dispositivo:
        sys.exit(f'Dispositivo "{args.dispositivo}" não encontrado.')

    def rpc(metodo, params):
        try:
            resposta = tb.sessao.post(f"{tb.url}/api/rpc/oneway/{dispositivo}",
                                      json={"method": metodo, "params": params}, timeout=10)
            if resposta.status_code != 200:
                print(f"  (RPC {metodo}: HTTP {resposta.status_code}; o painel está conectado?)")
        except Exception as erro:   # rede: a sessão continua, o painel só fica sem a etapa
            print(f"  (RPC {metodo} falhou: {erro})")

    def telemetria(valores):
        try:
            tb.sessao.post(f"{tb.url}/api/plugins/telemetry/DEVICE/{dispositivo}/timeseries/ANY",
                           json=valores, timeout=5)
        except Exception:
            pass

    def ao_abrir_etapa(ev, condutor):
        if ev.tipo == "fim":
            rpc("encerrarTreino", {})
            return
        e = condutor.etapa
        rpc("definirEtapa", {"nome": e.nome, "vel": e.vel, "dur": e.duracao,
                             "zmin": round(e.bpm_min), "zmax": round(e.bpm_max),
                             "i": condutor.indice + 1, "n": len(condutor.plano),
                             "alerta": e.nome == "ALERTA",
                             # limite do alerta local no painel (A6): 95% da FC máx.
                             "limite_alerta": round(LIMITE_SEGURANCA * condutor.perfil.fc_max)})

    def fonte(_t, condutor):
        resposta = tb.get(f"/api/plugins/telemetry/DEVICE/{dispositivo}/values/timeseries",
                          keys="bpm,spm,enlace")
        dados = resposta.json() if resposta.status_code == 200 else {}
        agora = time.time() * 1000

        def valor(chave):
            ponto = (dados.get(chave) or [{}])[0]
            recente = agora - ponto.get("ts", 0) < 5000
            try:
                return float(ponto["value"]) if recente else 0.0
            except (KeyError, TypeError, ValueError):
                return 0.0

        bpm, spm = valor("bpm"), valor("spm")
        e = condutor.etapa
        telemetria({"guia_etapa": condutor.indice + 1, "guia_nome": e.nome, "guia_vel": e.vel,
                    "guia_bpm_min": round(e.bpm_min), "guia_bpm_max": round(e.bpm_max),
                    "guia_situacao": condutor.situacao_zona(bpm)})
        return bpm, spm

    condutor, registro = Condutor(perfil, args.treino, modelo, args.escala), Registro(time.time)
    evento_original = registro.evento

    def evento_com_telemetria(ev, eco=True):
        evento_original(ev, eco)
        if ev.tipo in ("decisao", "alerta", "aviso"):
            telemetria({"guia_evento": f"{ev.tipo}: {ev.texto}"})

    registro.evento = evento_com_telemetria
    base = Path(args.saida) / f"{datetime.now():%Y%m%d_%H%M%S}_guia_{args.treino}"
    print("Sessão ao vivo. Ctrl+C encerra e salva o que foi registrado.")
    try:
        conduz(condutor, registro, fonte, ao_abrir_etapa, tempo_real=True)
    except KeyboardInterrupt:
        rpc("encerrarTreino", {})
    if registro.linhas:
        registro.salva(base)
        figura(registro, base, f"Sessão guiada ao vivo — {args.treino}")
        print(registro.resumo())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("modo", choices=["plano", "simular", "reproduzir", "ao-vivo"])
    ap.add_argument("base", nargs="?", help="reproduzir: base da coleta (sem _R.csv)")
    ap.add_argument("--treino", choices=sorted(TREINOS), default="hiit")
    ap.add_argument("--escala", type=float, default=1.0,
                    help="multiplica a duração das etapas (0.2 = sessão 5x mais curta)")
    ap.add_argument("--tiros", type=int, help="hiit: número de tiros (padrão 6)")
    ap.add_argument("--idade", type=int, required=True)
    ap.add_argument("--fc-repouso", type=float, required=True, help="BPM medido em repouso")
    ap.add_argument("--sexo", choices=["M", "F"], default="M")
    ap.add_argument("--peso", type=float, default=70.0, help="kg")
    ap.add_argument("--altura", type=float, default=170.0, help="cm")
    ap.add_argument("--vel-max", type=float, default=12.0, help="teto de velocidade, km/h")
    ap.add_argument("--saida", default="dados/guia")
    ap.add_argument("--calibracao", metavar="VEL:BPM",
                    help="FC estável medida no usuário em uma velocidade, ex.: 6:118")
    ap.add_argument("--modelo", help="arquivo .joblib do modelo (padrão: guia/modelo_fc.joblib)")
    ap.add_argument("--semente", type=int, default=1)
    ap.add_argument("--resposta", type=float, default=1.0,
                    help="simular: ganho da FC do usuário simulado (1.3 = menos condicionado)")
    ap.add_argument("--url", default=URL_PADRAO, help="padrão: variável TB_URL")
    ap.add_argument("--usuario", default=USUARIO_PADRAO, help="padrão: variável TB_USUARIO")
    ap.add_argument("--senha", default=SENHA_PADRAO, help="padrão: variável TB_SENHA")
    ap.add_argument("--dispositivo", default=DISPOSITIVO_PADRAO)
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    if args.tiros:
        TREINOS["hiit"] = nucleo_hiit(args.tiros)
    perfil = Perfil(args.idade, args.fc_repouso, args.sexo, args.peso, args.altura, args.vel_max)
    modelo = ModeloFC(args.modelo) if args.modelo else ModeloFC()
    if args.modelo and not modelo.treinado:
        sys.exit(f"Modelo não encontrado: {args.modelo}")
    if args.calibracao:
        vel, bpm = (float(x) for x in args.calibracao.split(":"))
        modelo.calibrar(perfil, vel, bpm)
    if args.modo == "reproduzir" and not args.base:
        ap.error("reproduzir precisa da base da coleta")
    {"plano": modo_plano, "simular": modo_simular, "reproduzir": modo_reproduzir,
     "ao-vivo": modo_ao_vivo}[args.modo](args, perfil, modelo)


if __name__ == "__main__":
    main()
