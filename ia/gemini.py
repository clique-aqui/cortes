
import os
import json
from google import genai

API_KEY = os.getenv("GEMINI_API_KEY")

def analisar_clipe(clipe):
    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada.")

    cliente = genai.Client(api_key=API_KEY)

    prompt = f"""
Analise este clipe de streaming.

Título: {clipe.get("titulo")}
Visualizações: {clipe.get("visualizacoes")}
Duração: {clipe.get("duracao")} segundos
Criador: {clipe.get("canal")}

Avalie o título e os dados disponíveis.
Não invente informações sobre imagens ou áudio que não foram fornecidos.

Retorne somente JSON neste formato:
{{
  "potencial": 0,
  "categoria": "engraçado, surpreendente, gameplay ou outro",
  "conteudo_improprio": false,
  "motivo": "explicação curta"
}}

Potencial deve ser um número de 0 a 100.
Marque conteúdo impróprio quando o título indicar conteúdo sexual explícito,
discurso de ódio ou violência extrema.
"""

    resposta = cliente.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt
    )

    texto = resposta.text.strip()
    texto = texto.replace("```json", "").replace("```", "").strip()

    return json.loads(texto)
