
import os
import json
from google import genai
from google.genai import types


API_KEY = os.getenv("GEMINI_API_KEY")


def analisar_clipe(clipe):

    if not API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY não configurada."
        )

    cliente = genai.Client(
        api_key=API_KEY,
        http_options=types.HttpOptions(
            timeout=30000
        )
    )

    prompt = f"""
Você é um especialista em análise de clipes de streaming.

Analise os dados abaixo:

Criador: {clipe.get("canal")}
Título: {clipe.get("titulo")}
Visualizações: {clipe.get("visualizacoes")}
Duração: {clipe.get("duracao")} segundos.

Avalie somente o título e os dados fornecidos.
Não invente informações sobre imagens ou áudio.
Não considere popularidade como prova de que o vídeo é engraçado.

Retorne somente um JSON válido neste formato:

{{
    "potencial": 0,
    "categoria": "engraçado, surpreendente, gameplay ou outro",
    "conteudo_improprio": false,
    "motivo": "explicação curta"
}}

Regras:
- potencial deve ser um número de 0 a 100.
- conteúdo_improprio deve ser true ou false.
- Considere títulos com conteúdo sexual explícito,
  discurso de ódio ou violência extrema como impróprios.
- Se não houver informação suficiente, seja conservador.
"""

    resposta = cliente.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt
    )

    if not resposta.text:
        raise RuntimeError(
            "O Gemini retornou uma resposta vazia."
        )

    texto = resposta.text.strip()

    if texto.startswith("```"):
        texto = texto.replace("```json", "")
        texto = texto.replace("```", "").strip()

    resultado = json.loads(texto)

    if not isinstance(resultado, dict):
        raise ValueError("Resposta do Gemini não é um objeto JSON.")

    potencial = resultado.get("potencial")

    if not isinstance(potencial, (int, float)):
        raise ValueError("Potencial inválido retornado pelo Gemini.")

    resultado["potencial"] = max(
        0,
        min(100, potencial)
    )

    return resultado
