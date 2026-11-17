"""Núcleo do guia de cardio do GCP-Esteira (entrega V2).

Reúne o que não depende de rede nem de hardware:
  - perfil do usuário e zonas de frequência cardíaca (Tanaka + Karvonen);
  - tipos de treino, descritos como sequências de etapas por zona;
  - modelo FC = f(velocidade), treinado por treinar_modelo.py, com reserva
    heurística quando o arquivo do modelo não existe;
  - classificação da atividade pela cadência (regras);
  - condutor: máquina de estados que conduz a sessão e decide os ajustes.

O condutor recebe uma amostra por segundo (tempo, BPM, SPM) e não sabe de onde
ela vem: ThingsBoard ao vivo, arquivo gravado ou usuário simulado.
"""

from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

ARQUIVO_MODELO = Path(__file__).with_name("modelo_fc.joblib")

VEL_MIN = 3.0          # km/h, caminhada lenta
PASSO_VEL = 0.5        # resolução de velocidade das esteiras comuns
AJUSTE_MAX = 2.0       # correção acumulada máxima por zona, em km/h
MARGEM_BPM = 5.0       # tolerância fora da zona antes de ajustar
COBERTURA_MIN = 0.5    # fração mínima de leituras válidas para decidir
LIMITE_SEGURANCA = 0.95  # fração da FC máxima que dispara o alerta (RF07)
DURACAO_MIN_AUMENTO = 60  # s; etapas mais curtas não justificam aumentar a velocidade
MARGEM_INICIAL = 20.0 # BPM somados à previsão do modelo antes de qualquer medida do usuário


# ---------------------------------------------------------------------------
# Perfil e zonas
# ---------------------------------------------------------------------------
@dataclass
class Perfil:
    idade: int
    fc_repouso: float
    sexo: str = "M"          # "M" ou "F"
    peso_kg: float = 70.0
    altura_cm: float = 170.0
    vel_max: float = 12.0    # maior velocidade que o guia pode recomendar

    @property
    def fc_max(self):
        return 208.0 - 0.7 * self.idade   # Tanaka et al. (2001)

    def zona(self, k):
        """Faixa de BPM (mín., máx.) da zona k = 1..5, pela reserva de FC (Karvonen)."""
        reserva = self.fc_max - self.fc_repouso
        return (self.fc_repouso + reserva * (0.4 + 0.1 * k),
                self.fc_repouso + reserva * (0.5 + 0.1 * k))


# ---------------------------------------------------------------------------
# Tipos de treino
# ---------------------------------------------------------------------------
@dataclass
class Etapa:
    nome: str        # texto curto, mostrado no painel
    zona: int        # zona-alvo, 1..5
    duracao: int     # segundos
    vel: float = 0.0     # km/h recomendada (preenchida pelo gerador)
    bpm_min: float = 0.0
    bpm_max: float = 0.0


def _hiit(tiros=6):
    etapas = [("AQUECER", 2, 180)]
    for _ in range(tiros):
        etapas += [("TIRO", 4, 60), ("RECUPERAR", 2, 90)]
    return etapas + [("DESAQUECER", 1, 180)]


TREINOS = {
    "hiit": _hiit(),
    "continuo": [("AQUECER", 1, 180), ("RITMO", 3, 1200), ("DESAQUECER", 1, 180)],
    "progressivo": [("AQUECER", 1, 180), ("LEVE", 2, 300), ("MODERADO", 3, 300),
                    ("FORTE", 4, 180), ("DESAQUECER", 1, 180)],
    "recuperacao": [("LEVE", 1, 900)],
}


# ---------------------------------------------------------------------------
# Modelo FC = f(velocidade)
# ---------------------------------------------------------------------------
class ModeloFC:
    """Prevê a FC em regime para uma velocidade e escolhe a velocidade de uma zona.

    Com o arquivo treinado, usa a regressão de treinar_modelo.py. Sem ele, usa
    uma reta entre (3 km/h, 35% da reserva) e (vel_max, 95% da reserva), que
    serve só para o sistema funcionar antes do treino do modelo.
    """

    def __init__(self, caminho=ARQUIVO_MODELO):
        self.pacote = None
        if Path(caminho).exists():
            import joblib
            self.pacote = joblib.load(caminho)
        # Correção pessoal, em BPM. O dataset é de testes incrementais com pessoas
        # treinadas, em que a FC ainda não estabilizou em cada velocidade; o modelo
        # tende a prever FC baixa demais (velocidade alta demais) para um usuário
        # comum. Sem calibração, o guia parte da margem e sobe se a FC permitir.
        self.deslocamento = MARGEM_INICIAL if self.pacote is not None else 0.0

    @property
    def treinado(self):
        return self.pacote is not None

    def prever(self, perfil, vel):
        if self.pacote is None:
            reserva = perfil.fc_max - perfil.fc_repouso
            fracao = 0.35 + 0.60 * (vel - VEL_MIN) / max(perfil.vel_max - VEL_MIN, 1.0)
            base = perfil.fc_repouso + reserva * fracao
        else:
            import pandas as pd
            linha = pd.DataFrame([{"vel": vel, "idade": perfil.idade,
                                   "sexo_m": 1 if perfil.sexo.upper() == "M" else 0,
                                   "peso": perfil.peso_kg, "altura": perfil.altura_cm}])
            base = float(self.pacote["modelo"].predict(linha[self.pacote["colunas"]])[0])
        return base + self.deslocamento

    def velocidade_para(self, perfil, bpm_alvo):
        """Velocidade (múltiplo de 0,5 km/h) cuja FC prevista fica mais perto do alvo."""
        candidatas = []
        v = VEL_MIN
        while v <= perfil.vel_max + 1e-9:
            candidatas.append(v)
            v += PASSO_VEL
        return min(candidatas, key=lambda c: abs(self.prever(perfil, c) - bpm_alvo))

    def calibrar(self, perfil, vel, bpm_medido):
        """Âncora medida no próprio usuário (FC estável em uma velocidade conhecida)."""
        self.deslocamento = 0.0
        self.deslocamento = max(-30.0, min(40.0, bpm_medido - self.prever(perfil, vel)))

    def personalizar(self, perfil, vel, bpm_medido):
        """Aproxima o modelo do usuário: metade do erro observado vira deslocamento."""
        erro = bpm_medido - self.prever(perfil, vel)
        # Assimétrico: FC acima do previsto corrige depressa (reduz a velocidade);
        # FC abaixo do previsto corrige no máximo 5 BPM por etapa, porque uma
        # leitura baixa pode ser erro do sensor ou FC que ainda não subiu.
        passo = max(-5.0, 0.5 * erro)
        self.deslocamento = max(-30.0, min(30.0, self.deslocamento + passo))


def gerar_plano(perfil, treino, modelo, escala=1.0):
    """Traduz o tipo de treino em etapas com velocidade e faixa de BPM."""
    plano = []
    for nome, zona, duracao in TREINOS[treino]:
        bpm_min, bpm_max = perfil.zona(zona)
        vel = modelo.velocidade_para(perfil, (bpm_min + bpm_max) / 2)
        plano.append(Etapa(nome, zona, max(10, round(duracao * escala)), vel, bpm_min, bpm_max))
    return plano


# ---------------------------------------------------------------------------
# Atividade pela cadência (regras)
# ---------------------------------------------------------------------------
def atividade_por_spm(spm):
    if spm < 40:
        return "parado"
    if spm < 130:
        return "caminhada"
    if spm < 165:
        return "trote"
    return "corrida"


def atividade_esperada(vel):
    if vel <= 6.5:
        return "caminhada"
    if vel <= 9.0:
        return "trote"
    return "corrida"


ORDEM_ATIVIDADE = ["parado", "caminhada", "trote", "corrida"]


# ---------------------------------------------------------------------------
# Condutor
# ---------------------------------------------------------------------------
@dataclass
class Evento:
    t: float
    tipo: str      # etapa | decisao | alerta | aviso | fim
    texto: str
    dados: dict = field(default_factory=dict)


class Condutor:
    """Conduz a sessão. Chame passo() uma vez por segundo com a leitura atual."""

    def __init__(self, perfil, treino, modelo, escala=1.0):
        self.perfil, self.modelo, self.treino = perfil, modelo, treino
        self.plano = gerar_plano(perfil, treino, modelo, escala)
        self.ajuste = {z: 0.0 for z in range(1, 6)}   # km/h somados por zona
        self.indice = -1
        self.t_inicio_etapa = 0.0
        self.leituras = []        # (t, bpm) da etapa atual; bpm 0 = sem leitura
        self.cadencias = []       # (t, spm) da etapa atual
        self.t_aviso_ritmo = None
        self.terminado = False

    # -- consultas ----------------------------------------------------------
    @property
    def etapa(self):
        return self.plano[self.indice] if 0 <= self.indice < len(self.plano) else None

    def restante(self, t):
        return max(0.0, self.etapa.duracao - (t - self.t_inicio_etapa)) if self.etapa else 0.0

    def situacao_zona(self, bpm):
        if bpm <= 0:
            return "sem_leitura"
        if bpm < self.etapa.bpm_min:
            return "abaixo"
        return "acima" if bpm > self.etapa.bpm_max else "na_zona"

    # -- laço principal -----------------------------------------------------
    def passo(self, t, bpm, spm):
        eventos = []
        if self.terminado:
            return eventos
        if self.indice < 0:
            eventos.append(self._abre_etapa(0, t))

        self.leituras.append((t, bpm))
        self.cadencias.append((t, spm))

        # Segurança: mediana dos últimos 10 s acima do limite interrompe a etapa.
        recentes = [b for (ti, b) in self.leituras if t - ti <= 10 and b > 0]
        limite = LIMITE_SEGURANCA * self.perfil.fc_max
        if len(recentes) >= 5 and median(recentes) > limite and self.etapa.nome != "ALERTA":
            # A etapa interrompida era forte demais: esta zona e as acima ficam 1 km/h mais lentas.
            self.modelo.personalizar(self.perfil, self.etapa.vel, median(recentes))
            for z in range(self.etapa.zona, 6):
                self.ajuste[z] = max(-2 * AJUSTE_MAX, self.ajuste[z] - 2 * PASSO_VEL)
            self.plano.insert(self.indice + 1, self._etapa_alerta())
            eventos.append(Evento(t, "alerta", f"FC {median(recentes):.0f} acima de "
                                  f"{limite:.0f} BPM: recuperação imediata e zonas "
                                  f"{self.etapa.zona}+ reduzidas em 1 km/h",
                                  {"decisao": "reduzir"}))
            eventos.append(self._abre_etapa(self.indice + 1, t))
            return eventos

        eventos += self._confere_ritmo(t)

        if t - self.t_inicio_etapa >= self.etapa.duracao:
            eventos += self._fecha_etapa(t)
            if self.indice + 1 < len(self.plano):
                eventos.append(self._abre_etapa(self.indice + 1, t))
            else:
                self.terminado = True
                eventos.append(Evento(t, "fim", "Treino concluído"))
        return eventos

    # -- internos -----------------------------------------------------------
    def _etapa_alerta(self):
        bpm_min, bpm_max = self.perfil.zona(1)
        return Etapa("ALERTA", 1, 120, VEL_MIN, bpm_min, bpm_max)

    def _abre_etapa(self, indice, t):
        self.indice, self.t_inicio_etapa = indice, t
        self.leituras, self.cadencias, self.t_aviso_ritmo = [], [], None
        e = self.etapa
        if e.nome != "ALERTA":
            # Recalcula com o modelo já personalizado e soma a correção da zona.
            base = self.modelo.velocidade_para(self.perfil, (e.bpm_min + e.bpm_max) / 2)
            e.vel = min(self.perfil.vel_max, max(VEL_MIN, base + self.ajuste[e.zona]))
        return Evento(t, "etapa", f"{e.nome} {indice + 1}/{len(self.plano)}: {e.vel:.1f} km/h "
                      f"por {e.duracao} s, alvo {e.bpm_min:.0f}-{e.bpm_max:.0f} BPM",
                      {"indice": indice})

    def _confere_ritmo(self, t):
        """Avisa quando a cadência indica atividade abaixo da esperada há 20 s."""
        janela = [s for (ti, s) in self.cadencias if t - ti <= 20]
        if t - self.t_inicio_etapa < 20 or not janela:
            return []
        medida = atividade_por_spm(median(janela))
        esperada = atividade_esperada(self.etapa.vel)
        abaixo = ORDEM_ATIVIDADE.index(medida) < ORDEM_ATIVIDADE.index(esperada)
        if abaixo and (self.t_aviso_ritmo is None or t - self.t_aviso_ritmo >= 30):
            self.t_aviso_ritmo = t
            return [Evento(t, "aviso", f"Cadência indica {medida}; a etapa pede {esperada}")]
        return []

    def _fecha_etapa(self, t):
        """Decide o ajuste para as próximas etapas da mesma zona."""
        e = self.etapa
        if e.nome == "ALERTA":
            return []
        # Usa a segunda metade da etapa, quando a FC já respondeu à velocidade.
        metade = self.t_inicio_etapa + e.duracao / 2
        fim = [b for (ti, b) in self.leituras if ti >= metade]
        validas = [b for b in fim if b > 0]
        if not fim or len(validas) / len(fim) < COBERTURA_MIN:
            return [Evento(t, "decisao", f"{e.nome}: BPM sem leitura suficiente "
                           f"({len(validas)} de {len(fim)}); plano mantido",
                           {"decisao": "manter_sem_dados"})]
        bpm = median(validas)
        # Plausibilidade: trote ou corrida com FC abaixo da zona 1 indica leitura
        # errada do sensor (visto no T3 da V1). Aumentar a velocidade com base
        # nela seria perigoso, então a leitura é descartada.
        cadencia = median([s for (ti, s) in self.cadencias if ti >= metade] or [0.0])
        if (atividade_por_spm(cadencia) in ("trote", "corrida")
                and bpm < self.perfil.zona(1)[0] - 15):
            return [Evento(t, "decisao", f"{e.nome}: mediana {bpm:.0f} BPM incompatível com "
                           f"cadência de {cadencia:.0f} SPM; leitura suspeita, plano mantido",
                           {"decisao": "manter_bpm_suspeito", "bpm": bpm})]
        # Etapa curta: a FC não teve tempo de subir, então FC baixa não prova nada.
        # (Sessão real de 15/11: um tiro de 22 s com 94 BPM levou tudo ao teto.)
        if bpm < e.bpm_min - MARGEM_BPM and e.duracao < DURACAO_MIN_AUMENTO:
            return [Evento(t, "decisao", f"{e.nome}: mediana {bpm:.0f} BPM abaixo da zona, mas "
                           f"a etapa de {e.duracao} s é curta para a FC responder; plano mantido",
                           {"decisao": "manter_etapa_curta", "bpm": bpm})]
        self.modelo.personalizar(self.perfil, e.vel, bpm)
        if bpm > e.bpm_max + MARGEM_BPM:
            self.ajuste[e.zona] = max(-AJUSTE_MAX, self.ajuste[e.zona] - PASSO_VEL)
            texto, decisao = "acima da zona: reduz 0,5 km/h", "reduzir"
            # Recuperação que não recuperou: a próxima fica 30 s mais longa.
            if e.zona <= 2:
                for prox in self.plano[self.indice + 1:]:
                    if prox.zona == e.zona and prox.nome == e.nome:
                        prox.duracao += 30
                        texto += " e alonga a próxima recuperação em 30 s"
                        break
        elif bpm < e.bpm_min - MARGEM_BPM:
            self.ajuste[e.zona] = min(AJUSTE_MAX, self.ajuste[e.zona] + PASSO_VEL)
            texto, decisao = "abaixo da zona: aumenta 0,5 km/h", "aumentar"
        else:
            texto, decisao = "dentro da zona: mantém", "manter"
        return [Evento(t, "decisao", f"{e.nome}: mediana {bpm:.0f} BPM, {texto}",
                       {"decisao": decisao, "bpm": bpm})]
