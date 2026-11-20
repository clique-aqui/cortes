
import os
import requests
from datetime import datetime, timedelta, timezone


API = "https://api.twitch.tv/helix"


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

        if resposta.status_code == 401:
            self.autenticar()
            return self.requisicao(endpoint, params)

        resposta.raise_for_status()
        return resposta.json()

    def descobrir_transmissoes(self, limite=100):
        encontrados = []
        cursor = None

        while len(encontrados) < limite:
            quantidade = min(100, limite - len(encontrados))

            params = {
                "first": quantidade
            }

            if cursor:
                params["after"] = cursor

            resultado = self.requisicao("streams", params)

            transmissões = resultado.get("data", [])

            if not transmissões:
                break

            encontrados.extend(transmissões)

            cursor = resultado.get("pagination", {}).get("cursor")

            if not cursor:
                break

        return encontrados

    def buscar_clipes(self, broadcaster_id, dias=3, limite=10):
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
    transmissões = twitch.descobrir_transmissoes()

    resultado = []

    for live in transmissões:
        resultado.append({
            "plataforma": "twitch",
            "canal": live.get("user_name"),
            "canal_id": live.get("user_id"),
            "titulo": live.get("title"),
            "categoria": live.get("game_name"),
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
