"""
Cortes de GTA de graça: ao vivo, históricos e melhores momentos.

O que o bot faz a cada execução (a cada ~5 min, pelo GitHub Actions):
  1. Se algum streamer da lista está AO VIVO em GTA: fica de olho nos clipes novos e posta os que bombam.
  2. Se ninguém está ao vivo: posta os melhores clipes HISTÓRICOS (últimos dias), alternando entre
     streamers (brasileiros e internacionais) para não repetir sempre o mesmo.
  3. Nos históricos, tenta pegar o MELHOR MOMENTO: um trecho maior da live (VOD) em volta do clipe.
     Se o VOD não estiver disponível, usa o clipe normal.
Tudo vira Short vertical com a sua foto e vai para o YouTube.

  python main.py checar     -> decide o que fazer agora (live, historico ou nada)
  python main.py live       -> fica de olho na live e vai postando
  python main.py historico  -> posta 1 clipe histórico (o melhor ainda não postado)
  python main.py teste      -> posta 1 clipe das últimas 24h como PRIVADO para testar

Arquivos que o bot mantém no repositório (o painel index.html lê estes):
  posted.json     -> clipes já postados (para nunca repetir)
  historico.json  -> lista de vídeos enviados, com título, views, origem e link
  status.json     -> pausa automática quando a cota do YouTube acaba
"""
import base64
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------- AJUSTES ----------
SO_GTA = True        # só clipes de jogos "Grand Theft Auto" (pega GTA V e GTA VI)
VIEWS_MIN = 100      # ao vivo: só posta clipe com pelo menos X views
DURACAO_MIN = 10     # segundos
MAX_POR_HORA = 6     # limite de Shorts por hora (ao vivo + histórico)
MAX_POR_DIA = 20     # limite de Shorts por dia (a cota do YouTube costuma parar antes disso)
JANELA_MIN = 25      # quanto tempo cada execução ao vivo fica de olho na live
SONDAGEM = 60        # segundos entre uma olhada e outra na Twitch
PAUSA_COTA_H = 6     # quando a cota do YouTube acaba, o bot pausa por este tempo

HIST_ATIVO = True    # posta clipes antigos quando ninguém está ao vivo
DIAS_HIST = 7        # até quantos dias atrás procurar clipes históricos
VIEWS_MIN_HIST = 300 # histórico: só clipes com pelo menos X views (os "melhores momentos")
HIST_POR_HORA = 2    # máximo de históricos por hora (o ao vivo tem prioridade)
USAR_VOD = True      # no histórico, tenta pegar um trecho maior da live em volta do clipe
VOD_ANTES = 8        # segundos antes do clipe
VOD_DEPOIS = 12      # segundos depois do clipe

MODELOS = ["gemini-3.8-flash", "gemini-3.6-flash"]  # Gemini grátis, por ordem de preferência
FONTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
POSTADOS = Path("posted.json")
HISTORICO = Path("historico.json")
STATUS = Path("status.json")
TMP = Path("tmp")
STREAMERS = [s.strip().lower() for s in os.environ.get("STREAMERS", "").split(",") if s.strip()]

# Hashtags comuns de GTA (o YouTube mostra as 3 primeiras acima do título)
HASHTAGS_GTA6 = ["#GTA6", "#GTAVI", "#ViceCity", "#GrandTheftAutoVI", "#Rockstar"]
HASHTAGS_GTA5 = ["#GTAV", "#GTA5", "#GTAOnline", "#GTARP"]
HASHTAGS_BASE = ["#GTA", "#Shorts", "#Gaming", "#Twitch", "#Clips", "#Cortes"]


def eh_gta(nome_do_jogo):
    return "grand theft auto" in (nome_do_jogo or "").lower()


def eh_gta6(nome_do_jogo):
    return bool(re.search(r"\bVI\b|\b6\b", nome_do_jogo or ""))


def montar_hashtags(jogo, streamer):
    jogo_tags = HASHTAGS_GTA6 if eh_gta6(jogo) else HASHTAGS_GTA5
    tag_streamer = "#" + re.sub(r"\W", "", streamer or "")
    tags = jogo_tags[:3] + ["#GTA", "#Shorts"] + ([tag_streamer] if len(tag_streamer) > 1 else []) \
        + jogo_tags[3:] + HASHTAGS_BASE[2:]
    vistas, saida = set(), []
    for t in tags:
        if t.lower() not in vistas:
            vistas.add(t.lower())
            saida.append(t)
    return saida[:14]


# ---------- tempo e estado ----------
def agora():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ler_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def carregar_estado():
    """{id_do_clipe: [quando_postou, quando_foi_criado, id_do_canal]}"""
    if not POSTADOS.exists():
        return {}
    dados = json.loads(POSTADOS.read_text())
    if isinstance(dados, list):  # formato antigo
        return {i: ["", "", ""] for i in dados}
    return dados


def salvar_estado(estado):
    POSTADOS.write_text(json.dumps(estado, indent=1))
    for arq, vazio in ((HISTORICO, "[]"), (STATUS, "{}")):
        if not arq.exists():
            arq.write_text(vazio)
    if os.environ.get("GITHUB_ACTIONS"):
        subprocess.run(
            "git config user.name cortes-bot && git config user.email cortes-bot@users.noreply.github.com"
            " && git add posted.json historico.json status.json && (git diff --cached --quiet"
            " || (git commit -qm 'clipes postados' && git pull -q --rebase && git push -q))",
            shell=True)


def carregar_historico():
    if HISTORICO.exists():
        try:
            return json.loads(HISTORICO.read_text())
        except Exception:
            return []
    return []


def registrar(c, titulo, video_id, privacidade, origem):
    historico = carregar_historico()
    historico.append({
        "quando": iso(agora()),
        "streamer": c["broadcaster_name"],
        "jogo": c.get("_jogo", ""),
        "titulo": titulo,
        "titulo_original": c["title"],
        "views": c["view_count"],
        "youtube": "https://youtube.com/shorts/" + video_id,
        "twitch": c["url"],
        "privacidade": privacidade,
        "origem": origem,
    })
    HISTORICO.write_text(json.dumps(historico[-500:], ensure_ascii=False, indent=1))


def hist_na_ultima_hora():
    corte = agora() - timedelta(hours=1)
    return sum(1 for v in carregar_historico()
               if v.get("origem") == "historico" and v.get("privacidade") == "public"
               and ler_iso(v["quando"]) > corte)


def pausado():
    try:
        return ler_iso(json.loads(STATUS.read_text())["pausado_ate"]) > agora()
    except Exception:
        return False


def pausar(estado, motivo):
    STATUS.write_text(json.dumps(
        {"pausado_ate": iso(agora() + timedelta(hours=PAUSA_COTA_H)), "motivo": motivo}, ensure_ascii=False))
    salvar_estado(estado)


def despausar(estado):
    if STATUS.exists() and STATUS.read_text().strip() not in ("", "{}") and not pausado():
        STATUS.write_text("{}")
        salvar_estado(estado)


def tratar_erro_youtube(e, estado):
    """Devolve True se foi cota esgotada (e já pausa o bot)."""
    print("Erro ao postar:", e)
    if "quota" in str(e).lower():
        print(f"Cota do YouTube esgotada, pausando por {PAUSA_COTA_H}h.")
        pausar(estado, "Cota do YouTube esgotada")
        return True
    return False


# ---------- Twitch ----------
class Twitch:
    def __init__(self):
        self.cid = os.environ["TWITCH_CLIENT_ID"]
        self.segredo = os.environ["TWITCH_CLIENT_SECRET"]
        self.token = None

    def get(self, caminho, **params):
        for _ in range(2):
            if not self.token:
                r = requests.post("https://id.twitch.tv/oauth2/token", timeout=30, data={
                    "client_id": self.cid, "client_secret": self.segredo,
                    "grant_type": "client_credentials"})
                r.raise_for_status()
                self.token = r.json()["access_token"]
            r = requests.get("https://api.twitch.tv/helix/" + caminho, params=params, timeout=30,
                             headers={"Client-Id": self.cid, "Authorization": f"Bearer {self.token}"})
            if r.status_code == 401:  # token vencido: pega outro e tenta de novo
                self.token = None
                continue
            r.raise_for_status()
            return r.json()["data"]
        raise RuntimeError("A Twitch recusou o token")


def ao_vivo(tw):
    """Canais da lista que estão ao vivo (a Twitch só devolve quem está ao vivo)."""
    vivos = []
    for i in range(0, len(STREAMERS), 100):  # a Twitch aceita até 100 canais por pedido
        vivos += tw.get("streams", user_login=STREAMERS[i:i + 100], first=100)
    return [v for v in vivos if v["type"] == "live" and (not SO_GTA or eh_gta(v["game_name"]))]


def so_gta(tw, clipes):
    """Fica só com os clipes cujo jogo é Grand Theft Auto (V ou VI) e anota o nome do jogo."""
    ids = list({c["game_id"] for c in clipes if c["game_id"]})
    nomes = {}
    for i in range(0, len(ids), 100):
        for g in tw.get("games", id=ids[i:i + 100]):
            nomes[g["id"]] = g["name"]
    saida = []
    for c in clipes:
        nome = nomes.get(c["game_id"], "")
        if eh_gta(nome):
            c["_jogo"] = nome
            saida.append(c)
    return saida


def repetido(c, estado):
    """Espectadores costumam clipar o mesmo momento várias vezes: posta só um."""
    criado = ler_iso(c["created_at"])
    for postado, criado_outro, canal in estado.values():
        if postado and criado_outro and canal == c["broadcaster_id"] and \
                abs((criado - ler_iso(criado_outro)).total_seconds()) < 120:
            return True
    return False


def escolher_clipe(tw, vivos, estado):
    """Ao vivo: o clipe novo (últimas 2 h) com mais views."""
    inicio = iso(agora() - timedelta(hours=2))
    candidatos = []
    for v in vivos:
        for c in tw.get("clips", broadcaster_id=v["user_id"], started_at=inicio, first=30):
            if c["id"] in estado or c["duration"] < DURACAO_MIN or c["view_count"] < VIEWS_MIN:
                continue
            if SO_GTA and c["game_id"] != v["game_id"]:
                continue
            if repetido(c, estado):
                continue
            c["_jogo"] = v["game_name"]
            candidatos.append(c)
    return max(candidatos, key=lambda c: c["view_count"], default=None)


def escolher_historico(tw, estado):
    """Histórico: os melhores clipes dos últimos dias, alternando entre streamers.
    A nota é views dividido por (1 + 2 x vezes que o streamer já saiu nas últimas 24 h),
    assim brasileiros e internacionais se revezam em vez de só aparecer o canal mais famoso."""
    inicio = iso(agora() - timedelta(days=DIAS_HIST))
    candidatos = []
    for i in range(0, len(STREAMERS), 100):
        for u in tw.get("users", login=STREAMERS[i:i + 100]):
            candidatos += tw.get("clips", broadcaster_id=u["id"], started_at=inicio, first=30)
    candidatos = [c for c in candidatos
                  if c["duration"] >= DURACAO_MIN and c["view_count"] >= VIEWS_MIN_HIST
                  and c["id"] not in estado and not repetido(c, estado)]
    if SO_GTA:
        candidatos = so_gta(tw, candidatos)
    if not candidatos:
        return None
    corte = agora() - timedelta(hours=24)
    recentes = Counter(v["streamer"].lower() for v in carregar_historico()
                       if v.get("privacidade") == "public" and ler_iso(v["quando"]) > corte)
    return max(candidatos, key=lambda c: c["view_count"] / (1 + 2 * recentes[c["broadcaster_name"].lower()]))


def postados_desde(estado, horas):
    corte = agora() - timedelta(hours=horas)
    return sum(1 for postado, _, _ in estado.values() if postado and ler_iso(postado) > corte)


def decidir(tw, estado):
    """Devolve ('live' | 'historico' | None, motivo)."""
    if pausado():
        return None, "pausado (cota do YouTube esgotada)"
    if postados_desde(estado, 24) >= MAX_POR_DIA:
        return None, "limite diário atingido"
    if postados_desde(estado, 1) >= MAX_POR_HORA:
        return None, "limite por hora atingido"
    vivos = ao_vivo(tw)
    if vivos:
        return "live", "ao vivo: " + ", ".join(v["user_login"] for v in vivos)
    if HIST_ATIVO and hist_na_ultima_hora() < HIST_POR_HORA and escolher_historico(tw, estado):
        return "historico", "ninguém ao vivo, mas há bons clipes dos últimos dias"
    return None, "nada a fazer agora"


# ---------- vídeo ----------
def baixar(url, saida):
    subprocess.run(["yt-dlp", "-q", "--no-playlist", "--force-overwrites",
                    "-f", "best[height<=1080]/best", "-o", str(saida), url], check=True)


def baixar_vod(c, saida):
    """Tenta baixar um trecho maior da live (VOD) em volta do clipe.
    Devolve o segundo, dentro do arquivo, onde fica o meio do clipe, ou None se não deu."""
    if not (USAR_VOD and c.get("video_id") and c.get("vod_offset") is not None):
        return None
    inicio = max(0, int(c["vod_offset"]) - VOD_ANTES)
    fim = int(c["vod_offset"]) + int(c["duration"]) + VOD_DEPOIS
    try:
        subprocess.run(["yt-dlp", "-q", "--no-playlist", "--force-overwrites",
                        "-f", "best[height<=1080]/best", "--download-sections", f"*{inicio}-{fim}",
                        "-o", str(saida), f"https://www.twitch.tv/videos/{c['video_id']}"],
                       check=True, timeout=600)
        if not Path(saida).exists() or Path(saida).stat().st_size < 10000:
            return None
        return (int(c["vod_offset"]) - inicio) + c["duration"] / 2
    except Exception as e:
        print("VOD indisponível, usando o clipe normal:", e)
        return None


def extrair_frame(video, saida, segundo):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(segundo), "-i", str(video),
                    "-frames:v", "1", "-vf", "scale=768:-2", str(saida)], check=True)


def titulo_ia(frame, titulo_original, streamer, jogo):
    """Gemini (grátis) olha uma cena e escreve o título. Sem chave ou com erro: usa o título do clipe."""
    reserva = f"{streamer}: {titulo_original}"[:60]
    chave = os.environ.get("GEMINI_API_KEY")
    if not chave:
        return reserva
    imagem = base64.b64encode(Path(frame).read_bytes()).decode()
    nome_jogo = "GTA 6" if eh_gta6(jogo) else "GTA"
    pedido = {"contents": [{"parts": [
        {"inline_data": {"mime_type": "image/jpeg", "data": imagem}},
        {"text": (
            f"Esta é uma cena de um clipe de {nome_jogo} do streamer {streamer}. "
            f"O título original do clipe é: {titulo_original!r}. "
            "Escreva UM título curto e chamativo em português do Brasil, com no máximo 60 caracteres, "
            "que gere curiosidade e diga o que acontece nessa cena, no estilo de cortes do YouTube. "
            "Não invente fatos que a imagem e o título original não mostrem. "
            "Sem emojis, sem aspas, sem hashtags. Responda só com o título.")},
    ]}]}
    for modelo in MODELOS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                headers={"x-goog-api-key": chave}, json=pedido, timeout=60)
            r.raise_for_status()
            partes = r.json()["candidates"][0]["content"]["parts"]
            texto = "".join(p.get("text", "") for p in partes).strip()
            titulo = texto.splitlines()[0].strip().strip('"“”*')[:60]
            if titulo:
                return titulo
        except Exception as e:
            print("Gemini falhou em", modelo, "->", e)
    return reserva


def cartao_texto(titulo, streamer, saida):
    """PNG transparente com o título e o crédito do streamer."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (1080, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    grande = ImageFont.truetype(FONTE, 64)
    pequena = ImageFont.truetype(FONTE, 38)
    linhas = "\n".join(textwrap.wrap(titulo.upper(), 22))
    d.multiline_text((540, 0), linhas, font=grande, fill="white", anchor="ma", align="center",
                     spacing=14, stroke_width=6, stroke_fill="black")
    d.text((540, 360), f"Clipe: {streamer}", font=pequena, fill="white", anchor="ma",
           stroke_width=4, stroke_fill="black")
    img.save(saida)


def editar(clip, foto, cartao, saida):
    """Vertical 1080x1920: fundo desfocado, clipe no meio, sua foto no topo, título embaixo."""
    filtro = (
        "[0:v]split=2[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "scale=108:192,boxblur=3:2,scale=1080:1920[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=0:(H-h)/2[v1];"
        "[1:v]scale=w=1000:h=380:force_original_aspect_ratio=decrease[foto];"
        "[v1][foto]overlay=(W-w)/2:150[v2];"
        "[v2][2:v]overlay=0:1330[v]"
    )
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-i", str(foto), "-i", str(cartao),
                    "-filter_complex", filtro, "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(saida)], check=True)


# ---------- YouTube ----------
def enviar(video, titulo, clipe, privacidade="public"):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    cred = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    yt = build("youtube", "v3", credentials=cred)
    tags = montar_hashtags(clipe.get("_jogo", ""), clipe["broadcaster_name"])
    corpo = {
        "snippet": {
            "title": f"{titulo} #Shorts"[:100],
            "description": (f"Clipe de {clipe['broadcaster_name']} na Twitch: {clipe['url']}\n"
                            f"Título original: {clipe['title']}\n\n" + " ".join(tags)),
            "categoryId": "20",
            "tags": [t.lstrip("#").lower() for t in tags],
        },
        "status": {"privacyStatus": privacidade, "selfDeclaredMadeForKids": False},
        "paidProductPlacementDetails": {"hasPaidProductPlacement": False},
    }
    req = yt.videos().insert(
        part="snippet,status,paidProductPlacementDetails",
        body=corpo,
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True),
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


# ---------- fluxo ----------
def achar_foto():
    foto = next(Path(".").glob("foto.*"), None)
    if not foto:
        raise SystemExit("Falta a sua foto no repositório, com o nome foto.png")
    return foto


def publicar(c, foto, estado, teste=False, origem="live", vod=False):
    bruto, frame, card, final = (TMP / n for n in ("bruto.mp4", "frame.jpg", "card.png", "final.mp4"))
    TMP.mkdir(exist_ok=True)
    print(f"Clipe ({origem}):", c["id"], "|", c["broadcaster_name"], "|", c["title"], "|", c["view_count"], "views")
    try:
        meio = baixar_vod(c, bruto) if vod else None
        if meio is None:
            baixar(c["url"], bruto)
            meio = c["duration"] / 2
        else:
            print("Usando o trecho maior da live (VOD).")
        extrair_frame(bruto, frame, meio)
        titulo = titulo_ia(frame, c["title"], c["broadcaster_name"], c.get("_jogo", ""))
        cartao_texto(titulo, c["broadcaster_name"], card)
        editar(bruto, foto, card, final)
    except Exception as e:  # clipe com problema: marca e segue para o próximo
        print("Falhou ao preparar, pulando:", e)
        if teste:
            raise
        estado[c["id"]] = ["", c["created_at"], c["broadcaster_id"]]
        salvar_estado(estado)
        return
    print("Título:", titulo)
    privacidade = "private" if teste else "public"
    video_id = enviar(final, ("[TESTE] " if teste else "") + titulo, c, privacidade)
    print("Enviado: https://youtube.com/shorts/" + video_id)
    # anota sempre (inclusive no teste), para nunca enviar o mesmo clipe duas vezes
    estado[c["id"]] = [iso(agora()), c["created_at"], c["broadcaster_id"]]
    registrar(c, ("[TESTE] " if teste else "") + titulo, video_id, privacidade, origem)
    salvar_estado(estado)


def modo_live():
    if not STREAMERS:
        raise SystemExit("Crie a variável STREAMERS com o(s) canal(is) da Twitch, ex.: canal1,canal2")
    foto = achar_foto()
    tw = Twitch()
    estado = carregar_estado()
    if pausado():
        print("Pausado até a cota do YouTube voltar.")
        return
    despausar(estado)
    fim = time.time() + JANELA_MIN * 60
    sem_live = 0
    while time.time() < fim:
        try:
            vivos = ao_vivo(tw)
            if not vivos:
                sem_live += 1
                if sem_live >= 3:
                    print("A live acabou.")
                    break
            else:
                sem_live = 0
                if postados_desde(estado, 24) >= MAX_POR_DIA:
                    print("Limite diário atingido, parando por hoje.")
                    break
                if postados_desde(estado, 1) < MAX_POR_HORA:
                    c = escolher_clipe(tw, vivos, estado)
                    if c:
                        publicar(c, foto, estado, origem="live")
        except requests.RequestException as e:  # Twitch ou Gemini com soluço: tenta de novo no próximo ciclo
            print("Erro de rede, tentando de novo:", e)
        except Exception as e:  # erro do YouTube (cota, etc.): não derruba tudo
            if tratar_erro_youtube(e, estado):
                break
        time.sleep(SONDAGEM)


def modo_historico():
    if not STREAMERS:
        raise SystemExit("Crie a variável STREAMERS com o(s) canal(is) da Twitch, ex.: canal1,canal2")
    foto = achar_foto()
    tw = Twitch()
    estado = carregar_estado()
    if pausado():
        print("Pausado até a cota do YouTube voltar.")
        return
    despausar(estado)
    try:
        c = escolher_historico(tw, estado)
        if not c:
            print("Nenhum clipe histórico novo com views suficientes.")
            return
        publicar(c, foto, estado, origem="historico", vod=USAR_VOD)
    except requests.RequestException as e:
        print("Erro de rede:", e)
    except Exception as e:
        tratar_erro_youtube(e, estado)


def checar():
    """Decide o que fazer e avisa o workflow (saídas: rodar, modo, aovivo)."""
    modo, motivo = None, "falta a variável STREAMERS"
    if STREAMERS:
        modo, motivo = decidir(Twitch(), carregar_estado())
    saida = os.environ.get("GITHUB_OUTPUT")
    if saida:
        with open(saida, "a") as f:
            f.write(f"rodar={'true' if modo else 'false'}\n")
            f.write(f"modo={modo or 'nada'}\n")
            f.write(f"aovivo={'true' if modo == 'live' else 'false'}\n")
    print("Decisão:", modo or "nada", "|", motivo)


def teste():
    """Pega o clipe de GTA mais visto das últimas 24h que ainda não foi postado e envia como privado."""
    if not STREAMERS:
        raise SystemExit("Crie a variável STREAMERS com o(s) canal(is) da Twitch, ex.: canal1,canal2")
    tw = Twitch()
    foto = achar_foto()
    estado = carregar_estado()
    inicio = iso(agora() - timedelta(hours=24))
    candidatos = []
    for i in range(0, len(STREAMERS), 100):
        for u in tw.get("users", login=STREAMERS[i:i + 100]):
            candidatos += tw.get("clips", broadcaster_id=u["id"], started_at=inicio, first=50)
    candidatos = [c for c in candidatos
                  if c["duration"] >= DURACAO_MIN and c["id"] not in estado and not repetido(c, estado)]
    if SO_GTA:
        candidatos = so_gta(tw, candidatos)
    if not candidatos:
        raise SystemExit("Nenhum clipe novo de GTA nas últimas 24h desses canais.")
    publicar(max(candidatos, key=lambda c: c["view_count"]), foto, estado, teste=True, origem="teste")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "live"
    {"checar": checar, "teste": teste, "live": modo_live, "historico": modo_historico}[modo]()
