
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

    transmissoes = descobrir()

    transmissoes.sort(
        key=lambda item: item.get("espectadores", 0),
        reverse=True
    )

    salvar("transmissoes.json", transmissoes)

    status_dados = carregar("status.json", {})

    status_dados.update({
        "ultima_busca": datetime.now(timezone.utc).isoformat(),
        "plataforma": "Twitch",
        "transmissoes_encontradas": len(transmissoes),
        "situacao": "Busca concluída"
    })

    salvar("status.json", status_dados)

    print("Transmissões encontradas:", len(transmissoes))

    for live in transmissoes[:10]:
        print(
            live["canal"],
            "|",
            live["categoria"],
            "|",
            live["espectadores"],
            "espectadores"
        )

    return transmissoes


def executar_busca_clipes():
    print("Iniciando busca de clipes da Twitch...")

    transmissoes = carregar("transmissoes.json", [])

    if not transmissoes:
        print("Nenhuma transmissão encontrada.")
        return

    twitch = Twitch()
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

    print("Busca de clipes concluída!")
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


def selecionar_melhores_clipes():
    print("Analisando potencial dos clipes...")

    clipes = carregar("clipes.json", [])

    if not clipes:
        print("Nenhum clipe encontrado.")
        return

    agora = datetime.now(timezone.utc)

    maior_visualizacao = max(
        (clipe.get("visualizacoes", 0) for clipe in clipes),
        default=1
    )

    if maior_visualizacao <= 0:
        maior_visualizacao = 1

    melhores = []

    for clipe in clipes:
        visualizacoes = clipe.get("visualizacoes", 0)
        duracao = clipe.get("duracao", 0)

        try:
            criado = datetime.fromisoformat(
                clipe["criado_em"].replace("Z", "+00:00")
            )

            horas = max(
                (agora - criado).total_seconds() / 3600,
                0
            )

        except (ValueError, KeyError, TypeError, AttributeError):
            horas = 72

        pontos_visualizacao = (
            visualizacoes / maior_visualizacao
        ) * 50

        pontos_recencia = max(
            0,
            30 * (1 - horas / 72)
        )

        if 15 <= duracao <= 60:
            pontos_duracao = 20
        elif 10 <= duracao < 15:
            pontos_duracao = 15
        else:
            pontos_duracao = 5

        pontuacao = round(
            pontos_visualizacao +
            pontos_recencia +
            pontos_duracao,
            2
        )

        resultado = clipe.copy()
        resultado["pontuacao"] = pontuacao

        melhores.append(resultado)

    melhores.sort(
        key=lambda item: item["pontuacao"],
        reverse=True
    )

    salvar("melhores_clipes.json", melhores)

    print("Análise concluída!")
    print("Clipes analisados:", len(melhores))

    for clipe in melhores[:10]:
        print(
            clipe["canal"],
            "|",
            clipe["titulo"],
            "| Pontuação:",
            clipe["pontuacao"]
        )


def status():
    dados = carregar("status.json", {})
    print(json.dumps(dados, indent=2, ensure_ascii=False))


def teste():
    print("Executando teste de descoberta...")
    transmissoes = executar_busca()

    if transmissoes:
        print("Teste concluído com sucesso.")
    else:
        print("Nenhuma transmissão encontrada.")


def ajuda():
    print("""
CORTES AI

Comandos disponíveis:
python main.py buscar
python main.py clipes
python main.py melhores
python main.py teste
python main.py status
python main.py ajuda
""")


if __name__ == "__main__":
    comando = sys.argv[1] if len(sys.argv) > 1 else "ajuda"

    comandos = {
        "buscar": executar_busca,
        "clipes": executar_busca_clipes,
        "melhores": selecionar_melhores_clipes,
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
            "quando": datetime.now(timezone.utc).isoformat()
        })

        sys.exit(1)
