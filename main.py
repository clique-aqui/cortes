import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from plataformas.twitch import Twitch, categorias_configuradas, descobrir


BASE = Path(__file__).parent
DADOS = BASE / "dados"
TMP = BASE / "tmp"
POSTED = BASE / "posted.json"

DADOS.mkdir(exist_ok=True)


def _numero(nome, padrao):
    # Variável vazia ou ausente no GitHub = usa o valor padrão.
    return float(os.getenv(nome) or padrao)


# ==========================================
# AJUSTES (todos podem ser trocados por variáveis no GitHub)
# ==========================================

MAX_CANAIS = int(_numero("MAX_CANAIS", 15))                  # lives que terão clipes buscados
CLIPES_POR_CANAL = int(_numero("MAX_CLIPS_PER_CHANNEL", 10))
DIAS_CLIPES = int(_numero("DIAS_CLIPES", 1))                 # 1 = clipes de hoje
MIN_VIEWS = int(_numero("MIN_CLIP_VIEWS", 100))
NOTA_MINIMA = _numero("MIN_IA_SCORE", 60)                    # nota mínima da IA para publicar
ANALISAR_POR_EXECUCAO = int(_numero("ANALISAR_POR_EXECUCAO", 3))
FILA_MINIMA = int(_numero("FILA_MINIMA", 2))                 # com 2 aprovados esperando, não gasta IA
MAX_CLIPES_POR_CANAL_NA_LISTA = 3
MAX_IDADE_HORAS = _numero("MAX_IDADE_HORAS", 36)             # não publica clipe mais velho que isso

AUTO_PUBLISH = (os.getenv("AUTO_PUBLISH") or "false").lower() == "true"
PRIVATE_TEST = (os.getenv("PRIVATE_TEST") or "false").lower() == "true"


# ==========================================
# ARQUIVOS
# ==========================================

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
        return json.loads(
            caminho.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError):
        return padrao


def carregar_postados_completo():
    """posted.json: id do clipe -> [quando postou, quando foi criado, id do canal]."""
    try:
        dados = json.loads(POSTED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(dados, list):
        return {clip_id: ["", "", ""] for clip_id in dados}

    return dados


def carregar_postados():
    """IDs dos clipes que já foram publicados (ou reservados) pelo publisher."""
    return set(carregar_postados_completo())


def _quando(texto):
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None


def mesmo_momento(clipe, postados):
    """Espectadores clipam o mesmo momento várias vezes: só um deve ir ao ar."""
    criado = _quando(clipe.get("criado_em") or clipe.get("created_at") or "")
    canal = clipe.get("broadcaster_id") or clipe.get("canal_id")

    if not criado or not canal:
        return False

    for _, outro_criado, outro_canal in postados.values():
        outro = _quando(outro_criado) if outro_criado else None

        if outro_canal == canal and outro and abs((criado - outro).total_seconds()) < 120:
            return True

    return False


def recente(clipe):
    criado = _quando(clipe.get("criado_em") or clipe.get("created_at") or "")

    if not criado:
        return False

    idade = (datetime.now(timezone.utc) - criado).total_seconds()
    return idade <= MAX_IDADE_HORAS * 3600


def atualizar_status(**campos):
    dados = carregar("status.json", {})
    dados.update(campos)
    salvar("status.json", dados)


def aprovado(clipe):
    """Mesma regra do publisher: IA aprovou e não é conteúdo impróprio."""
    analise = clipe.get("analise_ia") or {}

    if analise.get("erro") or analise.get("conteudo_improprio") is True:
        return False

    try:
        return float(analise.get("potencial", 0)) >= NOTA_MINIMA
    except (TypeError, ValueError):
        return False


def pendente(clipe, postados):
    """Aprovado pela IA e ainda capaz de ser publicado (não repetido, não velho demais)."""
    return (
        clipe.get("id") not in postados
        and aprovado(clipe)
        and recente(clipe)
        and not mesmo_momento(clipe, postados)
    )


# ==========================================
# DESCOBRIR TRANSMISSOES
# ==========================================

def executar_busca():
    print("Iniciando descoberta de lives...", flush=True)

    transmissoes = descobrir()

    transmissoes.sort(
        key=lambda item: item.get("espectadores", 0),
        reverse=True
    )

    salvar("transmissoes.json", transmissoes)

    atualizar_status(
        ultima_busca=datetime.now(timezone.utc).isoformat(),
        plataforma="Twitch",
        categorias=categorias_configuradas() or "todas",
        transmissoes_encontradas=len(transmissoes),
        espectadores_totais=sum(
            live.get("espectadores", 0)
            for live in transmissoes
        ),
        situacao="Busca concluída"
    )

    print("Transmissões encontradas:", len(transmissoes))

    for live in transmissoes[:10]:
        print(
            live.get("canal"),
            "|",
            live.get("categoria"),
            "|",
            live.get("espectadores", 0),
            "espectadores"
        )

    return transmissoes


# ==========================================
# BUSCAR CLIPES
# ==========================================

def executar_busca_clipes():
    print("Iniciando busca de clipes da Twitch...", flush=True)

    transmissoes = carregar("transmissoes.json", [])

    if not transmissoes:
        print("Nenhuma transmissão encontrada.")
        salvar("clipes.json", [])
        return

    twitch = Twitch()
    postados = carregar_postados()

    # Só aceita clipes das categorias monitoradas (ex.: GTA), mesmo que o
    # streamer tenha mudado de jogo durante a live.
    filtrar_jogo = bool(categorias_configuradas())
    jogos_permitidos = {
        live.get("game_id") for live in transmissoes if live.get("game_id")
    }
    nomes_dos_jogos = {
        live.get("game_id"): live.get("categoria") for live in transmissoes
    }

    todos_clipes = []

    for live in transmissoes[:MAX_CANAIS]:
        canal = live.get("canal", "Desconhecido")
        canal_id = live.get("canal_id")

        if not canal_id:
            continue

        print("Buscando clipes de:", canal, flush=True)

        try:
            clipes = twitch.buscar_clipes(
                broadcaster_id=canal_id,
                dias=DIAS_CLIPES,
                limite=CLIPES_POR_CANAL
            )

            for clipe in clipes:
                if clipe.get("id") in postados:
                    continue

                if filtrar_jogo and clipe.get("game_id") not in jogos_permitidos:
                    continue

                todos_clipes.append({
                    "id": clipe.get("id"),
                    "broadcaster_id": clipe.get("broadcaster_id") or canal_id,
                    "broadcaster_name": clipe.get("broadcaster_name") or canal,
                    "game_id": clipe.get("game_id"),
                    "plataforma": "twitch",
                    "canal": canal,
                    "titulo": clipe.get("title"),
                    "title": clipe.get("title"),
                    "url": clipe.get("url"),
                    "visualizacoes": clipe.get("view_count", 0),
                    "view_count": clipe.get("view_count", 0),
                    "criado_em": clipe.get("created_at"),
                    "created_at": clipe.get("created_at"),
                    "duracao": clipe.get("duration"),
                    "duration": clipe.get("duration"),
                    "idioma": clipe.get("language"),
                    "categoria": nomes_dos_jogos.get(
                        clipe.get("game_id"), live.get("categoria", "")
                    )
                })

        except Exception as erro:
            print(
                "Erro ao buscar clipes de",
                canal,
                ":",
                erro,
                flush=True
            )

    todos_clipes.sort(
        key=lambda item: item.get("visualizacoes", 0),
        reverse=True
    )

    salvar("clipes.json", todos_clipes)

    print("Busca de clipes concluída!")
    print("Total de clipes:", len(todos_clipes))

    for clipe in todos_clipes[:10]:
        print(
            clipe.get("canal"),
            "|",
            clipe.get("titulo"),
            "|",
            clipe.get("visualizacoes", 0),
            "visualizações"
        )


# ==========================================
# SELECIONAR MELHORES CLIPES
# ==========================================

def selecionar_melhores_clipes():
    print("Analisando potencial dos clipes...", flush=True)

    postados_completo = carregar_postados_completo()
    postados = set(postados_completo)

    clipes = [
        clipe for clipe in carregar("clipes.json", [])
        if clipe.get("id")
        and clipe["id"] not in postados
        and (clipe.get("duracao") or 0) >= 10
        and (clipe.get("visualizacoes") or 0) >= MIN_VIEWS
        and not mesmo_momento(clipe, postados_completo)
    ]

    if not clipes:
        print("Nenhum clipe novo com visualizações suficientes.")
        salvar("melhores_clipes.json", [])
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

        resultado = clipe.copy()
        resultado["pontuacao"] = round(
            pontos_visualizacao + pontos_recencia + pontos_duracao,
            2
        )

        melhores.append(resultado)

    melhores.sort(
        key=lambda item: item["pontuacao"],
        reverse=True
    )

    # Variedade: no máximo 3 clipes por canal e só um clipe por momento
    # (se dois espectadores clipam a mesma cena, fica o de maior pontuação).
    contagem = {}
    variados = []

    for clipe in melhores:
        canal = clipe.get("broadcaster_id") or clipe.get("canal")
        criado = _quando(clipe.get("criado_em") or "")

        if criado and any(
            (outro.get("broadcaster_id") or outro.get("canal")) == canal
            and _quando(outro.get("criado_em") or "")
            and abs((criado - _quando(outro["criado_em"])).total_seconds()) < 120
            for outro in variados
        ):
            continue

        contagem[canal] = contagem.get(canal, 0) + 1

        if contagem[canal] <= MAX_CLIPES_POR_CANAL_NA_LISTA:
            variados.append(clipe)

    salvar("melhores_clipes.json", variados)

    print("Análise concluída!")
    print("Clipes na lista:", len(variados))

    for clipe in variados[:10]:
        print(
            clipe.get("canal"),
            "|",
            clipe.get("titulo"),
            "| Pontuação:",
            clipe.get("pontuacao")
        )


# ==========================================
# ANALISAR CLIPES COM GEMINI
# ==========================================

def baixar_clipe(clipe):
    """Baixa o clipe (se ainda não estiver baixado). O publisher reaproveita o arquivo."""
    TMP.mkdir(exist_ok=True)
    caminho = TMP / f"{clipe['id']}.mp4"

    if not caminho.exists():
        subprocess.run([
            "yt-dlp", "-q", "--no-playlist", "--force-overwrites",
            "-f", "best[height<=1080]/best",
            "-o", str(caminho), clipe["url"]
        ], check=True)

    return caminho


def versao_leve(video):
    """Cópia pequena (360p) só para o Gemini assistir: envia menos dados."""
    saida = video.with_name(video.stem + "-ia.mp4")

    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(video),
        "-vf", "scale=-2:360", "-r", "15",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
        "-c:a", "aac", "-b:a", "48k", "-movflags", "+faststart",
        str(saida)
    ], check=True)

    return saida


def analisar_com_ia():
    print("Iniciando análise com Gemini...", flush=True)

    # Se o workflow informa AUTO_PUBLISH (mesmo vazio) e a publicação está desligada,
    # não gasta a cota do Gemini. Se a variável nem existe no ambiente (workflow antigo),
    # analisa normalmente.
    if "AUTO_PUBLISH" in os.environ and not (AUTO_PUBLISH or PRIVATE_TEST):
        print(
            "Publicação desligada (AUTO_PUBLISH não é true). "
            "Pulando a análise para não gastar a cota do Gemini."
        )
        return

    from ia.gemini import analisar_clipe

    melhores = carregar("melhores_clipes.json", [])
    antigas = carregar("analise_ia.json", [])
    postados_completo = carregar_postados_completo()
    postados = set(postados_completo)

    ja_analisados = {item.get("id") for item in antigas}

    na_fila = sum(1 for item in antigas if pendente(item, postados_completo))

    if na_fila >= FILA_MINIMA and not PRIVATE_TEST:
        print(f"Já existem {na_fila} clipes aprovados esperando. Não preciso analisar mais agora.")
        return

    candidatos = [
        clipe for clipe in melhores
        if clipe.get("id")
        and clipe["id"] not in postados
        and clipe["id"] not in ja_analisados
    ][:ANALISAR_POR_EXECUCAO]

    if not candidatos:
        print("Nenhum clipe novo para analisar.")

    novas = []
    falhas_da_ia = 0

    for indice, clipe in enumerate(candidatos, start=1):
        titulo = clipe.get("titulo", "Sem título")

        print(
            f"Analisando clipe {indice}/{len(candidatos)}: {titulo}",
            flush=True
        )

        # 1) Baixar e preparar o vídeo. Se este clipe estiver quebrado,
        #    marcamos para nunca mais tentar.
        try:
            video = versao_leve(baixar_clipe(clipe))
        except Exception as erro:
            print("Não consegui baixar este clipe:", erro, flush=True)
            resultado = clipe.copy()
            resultado["analise_ia"] = {
                "potencial": 0,
                "conteudo_improprio": False,
                "erro": f"download: {str(erro)[:120]}"
            }
            novas.append(resultado)
            continue

        # 2) Gemini assiste ao vídeo. Falha da IA (cota, chave) NÃO marca o clipe:
        #    ele será tentado de novo no próximo ciclo.
        try:
            analise = analisar_clipe(clipe, video)
        except Exception as erro:
            print("Erro ao analisar este clipe:", str(erro), flush=True)
            falhas_da_ia += 1

            if falhas_da_ia >= 2:
                print("A IA falhou duas vezes seguidas. Tento de novo no próximo ciclo.")
                break

            continue

        falhas_da_ia = 0

        resultado = clipe.copy()
        resultado["analise_ia"] = analise
        novas.append(resultado)

        print(
            "Potencial:", analise.get("potencial"),
            "| Impróprio:", analise.get("conteudo_improprio"),
            "| Título:", analise.get("titulo"),
            flush=True
        )

    todas = (antigas + novas)[-300:]
    salvar("analise_ia.json", todas)

    atualizar_status(
        ultima_analise_ia=datetime.now(timezone.utc).isoformat(),
        clipes_analisados_ia=len(novas),
        aprovados_na_fila=sum(1 for item in todas if pendente(item, postados_completo))
    )

    print("Análise com Gemini finalizada!", flush=True)
    print("Analisados agora:", len(novas))


# ==========================================
# STATUS
# ==========================================

def status():
    dados = carregar("status.json", {})

    print(
        json.dumps(
            dados,
            indent=2,
            ensure_ascii=False
        )
    )


# ==========================================
# TESTE
# ==========================================

def teste():
    print("Executando teste de descoberta...")

    transmissoes = executar_busca()

    if transmissoes:
        print("Teste concluído com sucesso.")
    else:
        print("Nenhuma transmissão encontrada.")


# ==========================================
# AJUDA
# ==========================================

def ajuda():
    print("""
CORTES AI

Comandos disponíveis:

python main.py buscar
python main.py clipes
python main.py melhores
python main.py ia
python main.py teste
python main.py status
python main.py ajuda
""")


# ==========================================
# EXECUCAO PRINCIPAL
# ==========================================

if __name__ == "__main__":

    comando = sys.argv[1] if len(sys.argv) > 1 else "ajuda"

    comandos = {
        "buscar": executar_busca,
        "clipes": executar_busca_clipes,
        "melhores": selecionar_melhores_clipes,
        "ia": analisar_com_ia,
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
        print("ERRO:", erro, flush=True)

        atualizar_status(
            situacao="Erro",
            mensagem=str(erro),
            quando=datetime.now(timezone.utc).isoformat()
        )

        sys.exit(1)
