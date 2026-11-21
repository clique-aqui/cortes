import os
import json
import time

from google import genai
from google.genai import types


API_KEY = os.getenv("GEMINI_API_KEY")


def analisar_clipe(clipe):

    if not API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada.")

    cliente = genai.Client(
        api_key=API_KEY,
        http_options=types.HttpOptions(
            timeout=120000
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
- conteudo_improprio deve ser true ou false.
- Considere títulos com conteúdo sexual explícito,
  discurso de ódio ou violência extrema como impróprios.
- Se não houver informação suficiente, seja conservador.
"""

    ultima_excecao = None

    for tentativa in range(1, 4):
        try:
            resposta = cliente.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt
            )

            if not resposta.text:
                raise RuntimeError("O Gemini retornou uma resposta vazia.")

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

            resultado["potencial"] = max(0, min(100, potencial))

            return resultado

        except Exception as erro:
            ultima_excecao = erro
            mensagem = str(erro).lower()

            erro_temporario = any(
                termo in mensagem
                for termo in (
                    "504",
                    "deadline_exceeded",
                    "deadline expired",
                    "503",
                    "service unavailable",
                    "429",
                    "resource_exhausted",
                    "timeout",
                    "timed out"
                )
            )

            if not erro_temporario or tentativa == 3:
                raise

            espera = tentativa * 10
            print(
                f"Gemini demorou ou está temporariamente indisponível. "
                f"Tentativa {tentativa}/3 falhou. "
                f"Nova tentativa em {espera} segundos..."
            )
            time.sleep(espera)

    raise RuntimeError(f"Falha ao analisar clipe: {ultima_excecao}")
