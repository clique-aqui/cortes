import json
import os
from pathlib import Path
from datetime import datetime, timezone

from google import genai
from google.genai import types


API_KEY = os.getenv("GEMINI_API_KEY")

LIMITE_DIARIO = int(os.getenv("GEMINI_DAILY_LIMIT") or 15)
ARQUIVO_USO = Path(__file__).resolve().parent.parent / "dados" / "gemini_usage.json"


class QuotaDiariaAtingida(RuntimeError):
    """Limite diário de chamadas configurado para o projeto foi atingido."""


def _registrar_solicitacao():
    """Conta cada chamada real à API e impede ultrapassar o orçamento diário."""
    hoje = datetime.now(timezone.utc).date().isoformat()
    ARQUIVO_USO.parent.mkdir(exist_ok=True)
    try:
        uso = json.loads(ARQUIVO_USO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        uso = {}

    if uso.get("data") != hoje:
        uso = {"data": hoje, "solicitacoes": 0}

    if int(uso.get("solicitacoes", 0)) >= LIMITE_DIARIO:
        raise QuotaDiariaAtingida(
            f"Limite interno de {LIMITE_DIARIO} solicitações Gemini no dia atingido."
        )

    uso["solicitacoes"] = int(uso.get("solicitacoes", 0)) + 1
    ARQUIVO_USO.write_text(
        json.dumps(uso, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

# Modelos em ordem de preferência. Se um sair do ar (erro 404) ou estourar a
# cota grátis (erro 429), o sistema tenta o próximo da lista automaticamente.
# O gemini-2.5-flash foi desligado pelo Google, por isso não está aqui.
# Para trocar sem mexer no código: variável GEMINI_MODELS no GitHub.
MODELOS = [
    modelo.strip()
    for modelo in (
        os.getenv("GEMINI_MODELS")
        or "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite"
    ).split(",")
    if modelo.strip()
]


PROMPT = """
Você é editor de cortes para YouTube Shorts, especialista em clipes de lives de games.

{origem}

Dados do clipe:
- Criador: {canal}
- Categoria: {categoria}
- Título original: {titulo}
- Visualizações: {visualizacoes}
- Duração: {duracao} segundos

Avalie se este momento tem potencial de viralizar em Shorts: engraçado, inesperado,
emocionante, grande jogada ou reação forte, com começo e fim claros.
Clipes parados, conversa sem graça, silêncio, tela de menu ou momento confuso
recebem nota baixa. Não use as visualizações como prova de que o clipe é bom.

Marque conteudo_improprio como true se houver nudez, conteúdo sexual explícito,
discurso de ódio, violência real, automutilação ou qualquer coisa que o YouTube
possa remover ou restringir por idade. Violência comum de videogame não é imprópria.
Se não for possível saber, seja conservador (true).

Escreva também um "titulo" em português do Brasil, com no máximo 60 caracteres,
dizendo o que acontece no clipe ou o que o streamer está fazendo.
Sem emojis, sem aspas, sem hashtags e sem o nome do canal.

Responda somente com um JSON válido neste formato:

{{
  "potencial": 0,
  "categoria": "engraçado, surpreendente, jogada, reação ou outro",
  "conteudo_improprio": false,
  "motivo": "explicação curta",
  "titulo": "título do corte"
}}

potencial é um número de 0 a 100.
"""


def _validar(texto):
    if not texto:
        raise RuntimeError("O Gemini retornou uma resposta vazia.")

    texto = texto.strip()

    if texto.startswith("```"):
        texto = texto.replace("```json", "").replace("```", "").strip()

    resultado = json.loads(texto)

    if not isinstance(resultado, dict):
        raise ValueError("Resposta do Gemini não é um objeto JSON.")

    potencial = resultado.get("potencial")

    if not isinstance(potencial, (int, float)) or isinstance(potencial, bool):
        raise ValueError("Potencial inválido retornado pelo Gemini.")

    resultado["potencial"] = max(0, min(100, potencial))

    # Se a IA esquecer de responder, tratamos como impróprio (mais seguro).
    resultado["conteudo_improprio"] = resultado.get("conteudo_improprio") is not False

    titulo = str(resultado.get("titulo") or "").strip().strip('"“”\'')
    resultado["titulo"] = titulo.splitlines()[0][:60] if titulo else ""

    return resultado


def analisar_clipe(clipe, video=None):
    """Analisa o clipe. Com `video`, o Gemini assiste e escuta o vídeo de verdade."""

    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada.")

    cliente = genai.Client(
        api_key=API_KEY,
        http_options=types.HttpOptions(timeout=120000)
    )

    if video:
        origem = "Assista e escute o vídeo anexado antes de avaliar."
    else:
        origem = "Você só tem os dados abaixo. Não invente informações sobre imagem ou áudio."

    prompt = PROMPT.format(
        origem=origem,
        canal=clipe.get("canal"),
        categoria=clipe.get("categoria") or "não informada",
        titulo=clipe.get("titulo"),
        visualizacoes=clipe.get("visualizacoes"),
        duracao=clipe.get("duracao"),
    )

    conteudo = []

    if video:
        conteudo.append(
            types.Part.from_bytes(
                data=Path(video).read_bytes(),
                mime_type="video/mp4"
            )
        )

    conteudo.append(prompt)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.3
    )

    ultimo_erro = None

    for modelo in MODELOS:
        try:
            _registrar_solicitacao()
            resposta = cliente.models.generate_content(
                model=modelo,
                contents=conteudo,
                config=config
            )
            resultado = _validar(resposta.text)
            resultado["modelo"] = modelo
            return resultado

        except QuotaDiariaAtingida:
            raise
        except Exception as erro:
            ultimo_erro = erro
            mensagem = str(erro)
            print(f"  Gemini ({modelo}) falhou: {mensagem[:200]}", flush=True)

            # Cota esgotada: não tenta outros modelos e não desperdiça mais chamadas.
            if "429" in mensagem or "RESOURCE_EXHAUSTED" in mensagem:
                raise QuotaDiariaAtingida(
                    "O Google informou que a cota diária da API Gemini acabou."
                ) from erro

    raise RuntimeError(f"Todos os modelos do Gemini falharam. Último erro: {ultimo_erro}")
