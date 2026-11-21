import os
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.twitch.tv/helix"

# Categorias que o sistema acompanha (nomes exatamente como aparecem na Twitch).
# Para mudar sem mexer no código: crie a variável CATEGORIAS no GitHub
# (Settings > Secrets and variables > Actions > Variables).
# Use "todas" para acompanhar qualquer categoria.
CATEGORIAS_PADRAO = "Grand Theft Auto VI,Grand Theft Auto V"


def categorias_configuradas():
    texto = os.getenv("CATEGORIAS") or CATEGORIAS_PADRAO
    if texto.strip().lower() in ("todas", "todos", "*"):
        return []
    return [nome.strip() for nome in texto.split(",") if nome.strip()]


class Twitch:
    def __init__(self):
        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        self.token = None

        if not self.client_id or not self.client_secret:
            raise RuntimeError("Credenciais da Twitch não configuradas.")

    def autenticar(self):
        resposta = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials"
            },
            timeout=30
        )
        resposta.raise_for_status()
        self.token = resposta.json()["access_token"]

    def requisicao(self, endpoint, params=None):
        for _ in range(2):
            if not self.token:
                self.autenticar()

            resposta = requests.get(
                f"{API}/{endpoint}",
                params=params or {},
                headers={
                    "Client-Id": self.client_id,
                    "Authorization": f"Bearer {self.token}"
                },
                timeout=30
            )

            if resposta.status_code == 401:  # token vencido: pega outro e tenta de novo
                self.token = None
                continue

            resposta.raise_for_status()
            return resposta.json()

        raise RuntimeError("A Twitch recusou o token de acesso.")

    def achar_categorias(self, nomes):
        """Devolve {game_id: nome} das categorias que existem na Twitch."""
        if not nomes:
            return {}
        dados = self.requisicao("games", {"name": nomes}).get("data", [])
        return {jogo["id"]: jogo["name"] for jogo in dados}

    def descobrir_transmissoes(self, limite=100):
        nomes = categorias_configuradas()
        filtro = {}

        if nomes:
            categorias = self.achar_categorias(nomes)
            if not categorias:
                print("Nenhuma destas categorias existe na Twitch:", nomes, flush=True)
                return []
            print("Categorias monitoradas:", list(categorias.values()), flush=True)
            filtro = {"game_id": list(categorias)}

        encontrados = []
        cursor = None

        while len(encontrados) < limite:
            params = {"first": min(100, limite - len(encontrados)), **filtro}

            if cursor:
                params["after"] = cursor

            resultado = self.requisicao("streams", params)
            transmissoes = resultado.get("data", [])

            if not transmissoes:
                break

            encontrados.extend(transmissoes)

            cursor = resultado.get("pagination", {}).get("cursor")

            if not cursor:
                break

        return encontrados

    def buscar_clipes(self, broadcaster_id, dias=1, limite=10):
        inicio = (
            datetime.now(timezone.utc) -
            timedelta(days=dias)
        ).isoformat(timespec="seconds").replace("+00:00", "Z")

        resultado = self.requisicao(
            "clips",
            {
                "broadcaster_id": broadcaster_id,
                "started_at": inicio,
                "first": min(limite, 100)
            }
        )

        return resultado.get("data", [])


def descobrir():
    twitch = Twitch()
    transmissoes = twitch.descobrir_transmissoes()

    resultado = []

    for live in transmissoes:
        resultado.append({
            "plataforma": "twitch",
            "canal": live.get("user_name"),
            "canal_id": live.get("user_id"),
            "titulo": live.get("title"),
            "categoria": live.get("game_name"),
            "game_id": live.get("game_id"),
            "idioma": live.get("language"),
            "espectadores": live.get("viewer_count", 0),
            "inicio": live.get("started_at"),
            "thumbnail": live.get("thumbnail_url")
        })

    return resultado


if __name__ == "__main__":
    for live in descobrir():
        print(
            live["canal"],
            "|",
            live["categoria"],
            "|",
            live["espectadores"],
            "espectadores"
        )
