
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from plataformas.twitch import descobrir, Twitch

BASE = Path(__file__).parent
DADOS = BASE / "dados"

DADOS.mkdir(exist_ok=True)


def salvar(nome, conteudo):
    caminho = DADOS / nome
    caminho.write_text(
        json.dumps(conteudo, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def carregar(nome, padrao):
    caminho = DADOS / nome

    if not caminho.exists():
        return padrao

    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return padrao


def executar_busca():
    print("Iniciando descoberta mundial...")

    transmissões = descobrir()

    transmissões.sort(
        key=lambda item: item.get("espectadores", 0),
        reverse=True
    )

    salvar("transmissoes.json", transmissões)

    status = carregar("status.json", {})

    status.update({
        "ultima_busca": datetime.now(
            timezone.utc
        ).isoformat(),
        "plataforma": "Twitch",
        "transmissoes_encontradas": len(transmissões),
        "situacao": "Busca concluída"
    })

    salvar("status.json", status)

    print("Transmissões encontradas:", len(transmissões))

    for live in transmissões[:10]:
        print(
            live["canal"],
            "|",
            live["categoria"],
            "|",
            live["espectadores"],
            "espectadores"
        )

    return transmissões

def executar_busca_clipes():
    print("Iniciando busca de clipes da Twitch...")

    transmissoes = carregar("transmissoes.json", [])

    if not transmissoes:
        print("Nenhuma transmissão encontrada.")
        print("Execute primeiro: python main.py buscar")
        return

    twitch = Twitch()

    # Começa testando os 10 canais com mais espectadores
    canais = transmissoes[:10]

    todos_clipes = []

    for live in canais:
        canal = live.get("canal", "Desconhecido")
        canal_id = live.get("canal_id")

        if not canal_id:
            continue

        print("Buscando clipes de:", canal)

        try:
            clipes = twitch.buscar_clipes(
                broadcaster_id=canal_id,
                dias=3,
                limite=10
            )

            for clipe in clipes:
                todos_clipes.append({
                    "plataforma": "twitch",
                    "canal": canal,
                    "titulo": clipe.get("title"),
                    "url": clipe.get("url"),
                    "visualizacoes": clipe.get("view_count", 0),
                    "criado_em": clipe.get("created_at"),
                    "duracao": clipe.get("duration")
                })

        except Exception as erro:
            print("Erro ao buscar clipes de", canal, ":", erro)

    todos_clipes.sort(
        key=lambda item: item.get("visualizacoes", 0),
        reverse=True
    )

    salvar("clipes.json", todos_clipes)

    print("Busca concluída!")
    print("Total de clipes encontrados:", len(todos_clipes))

    for clipe in todos_clipes[:10]:
        print(
            clipe["canal"],
            "|",
            clipe["titulo"],
            "|",
            clipe["visualizacoes"],
            "visualizações"
        )

def status():
    dados = carregar("status.json", {})
    print(json.dumps(dados, indent=2, ensure_ascii=False))


def teste():
    print("Executando teste de descoberta...")
    transmissões = executar_busca()

    if transmissões:
        print("Teste concluído com sucesso.")
    else:
        print("Nenhuma transmissão encontrada.")


def ajuda():
    print("""
CORTES AI

Comandos disponíveis:

python main.py buscar
python main.py teste
python main.py status

A publicação automática será ativada
depois da validação dos conectores,
dos direitos de uso e do editor.
""")


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else "ajuda"

comandos = {
    "buscar": executar_busca,
    "clipes": executar_busca_clipes,
    "teste": teste,
    "status": status,
    "ajuda": ajuda
}

    if comando not in comandos:
        ajuda()
        sys.exit(1)

    try:
        comandos[comando]()
    except Exception as erro:
        print("ERRO:", erro)

        salvar("status.json", {
            "situacao": "Erro",
            "mensagem": str(erro),
            "quando": datetime.now(
                timezone.utc
            ).isoformat()
        })

        sys.exit(1)
