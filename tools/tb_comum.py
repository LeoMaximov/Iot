"""Funções compartilhadas pelos scripts que falam com a API REST do ThingsBoard."""

import os
import sys

try:
    import requests
except ImportError:
    sys.exit("Falta o requests. Instale com: pip install -r tools/requirements.txt")

URL_PADRAO = os.environ.get("TB_URL", "http://localhost:8080")
# Usuário de tenant criado pela instalação com dados de demonstração (LOAD_DEMO=true).
USUARIO_PADRAO = os.environ.get("TB_USUARIO", "tenant@thingsboard.org")
SENHA_PADRAO = os.environ.get("TB_SENHA", "tenant")
DISPOSITIVO_PADRAO = "GCP-Esteira Painel"


def adiciona_argumentos(ap):
    ap.add_argument("--url", default=URL_PADRAO, help="endereço do ThingsBoard")
    ap.add_argument("--usuario", default=USUARIO_PADRAO)
    ap.add_argument("--senha", default=SENHA_PADRAO)
    ap.add_argument("--dispositivo", default=DISPOSITIVO_PADRAO, help="nome do dispositivo")


class ThingsBoard:
    def __init__(self, url, usuario, senha):
        self.url = url.rstrip("/")
        self.sessao = requests.Session()
        try:
            resposta = self.sessao.post(f"{self.url}/api/auth/login",
                                        json={"username": usuario, "password": senha}, timeout=10)
        except requests.ConnectionError:
            sys.exit(f"Não foi possível conectar a {self.url}. O ThingsBoard está no ar?")
        if resposta.status_code != 200:
            sys.exit(f"Login recusado ({resposta.status_code}). Confira --usuario e --senha.")
        self.sessao.headers["X-Authorization"] = f"Bearer {resposta.json()['token']}"

    def get(self, caminho, **params):
        resposta = self.sessao.get(f"{self.url}{caminho}", params=params, timeout=30)
        return resposta

    def post(self, caminho, corpo):
        resposta = self.sessao.post(f"{self.url}{caminho}", json=corpo, timeout=30)
        resposta.raise_for_status()
        return resposta.json()

    def id_dispositivo(self, nome):
        """ID do dispositivo com esse nome, ou None se não existir."""
        resposta = self.get("/api/tenant/devices", deviceName=nome)
        if resposta.status_code == 404:
            return None
        resposta.raise_for_status()
        return resposta.json()["id"]["id"]
