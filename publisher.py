import json
import os
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE = Path(__file__).parent
DATA = BASE / "dados"
TMP = BASE / "tmp"
POSTED = BASE / "posted.json"
HISTORY = BASE / "historico.json"
PHOTO = BASE / "foto.png"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _int(nome, padrao):
    # Variável vazia ou ausente no GitHub = usa o valor padrão.
    return int(os.getenv(nome) or padrao)


MIN_VIEWS = _int("MIN_CLIP_VIEWS", 100)
MIN_SCORE = _int("MIN_IA_SCORE", 60)
MAX_PER_RUN = _int("MAX_POSTS_PER_RUN", 1)
MAX_PER_DAY = _int("MAX_POR_DIA", 30)
MINUTOS_MESMO_CANAL = _int("MINUTOS_MESMO_CANAL", 90)
MAX_IDADE_HORAS = _int("MAX_IDADE_HORAS", 36)
AUTO_PUBLISH = (os.getenv("AUTO_PUBLISH") or "false").lower() == "true"
PRIVATE_TEST = (os.getenv("PRIVATE_TEST") or "false").lower() == "true"

# public = aparece para todos | unlisted = só quem tem o link | private = só você
PRIVACY = (os.getenv("PRIVACIDADE") or "public").lower()
if PRIVACY not in ("public", "unlisted", "private"):
    PRIVACY = "public"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_time(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


# ==========================================
# REGRAS DE PUBLICAÇÃO
# ==========================================
# posted.json guarda: id do clipe -> [quando postou, quando o clipe foi criado, id do canal]
# "quando postou" vazio = reservado (foi escolhido, mesmo que o envio tenha falhado).

def posted_since(posted, delta):
    limit = datetime.now(timezone.utc) - delta
    count = 0
    for when, _, _ in posted.values():
        moment = parse_time(when) if when else None
        if moment and moment > limit:
            count += 1
    return count


def channel_posted_recently(posted, channel_id):
    if not channel_id:
        return False
    limit = datetime.now(timezone.utc) - timedelta(minutes=MINUTOS_MESMO_CANAL)
    for when, _, channel in posted.values():
        moment = parse_time(when) if when else None
        if channel == channel_id and moment and moment > limit:
            return True
    return False


def same_moment(clip, posted):
    """Espectadores costumam clipar o mesmo momento várias vezes: publica só um."""
    created = parse_time(clip.get("created_at") or clip.get("criado_em") or "")
    channel_id = clip.get("broadcaster_id") or clip.get("canal_id")
    if not created or not channel_id:
        return False
    for _, other_created, other_channel in posted.values():
        other = parse_time(other_created) if other_created else None
        if other_channel == channel_id and other and abs((created - other).total_seconds()) < 120:
            return True
    return False


# ==========================================
# SALVAR NO GITHUB (para nunca repetir um vídeo)
# ==========================================

def save_state_to_github(required=False):
    if not os.getenv("GITHUB_ACTIONS"):
        return

    try:
        for command in (
            ["git", "config", "user.name", "cortes-ai"],
            ["git", "config", "user.email", "cortes-ai@users.noreply.github.com"],
            ["git", "add", "posted.json", "historico.json"],
        ):
            subprocess.run(command, check=True, cwd=BASE)

        nothing_new = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=BASE
        ).returncode == 0
        if nothing_new:
            return

        subprocess.run(["git", "commit", "-q", "-m", "Registra clipe postado"], check=True, cwd=BASE)

        if subprocess.run(["git", "push", "-q"], cwd=BASE).returncode != 0:
            subprocess.run(["git", "pull", "--rebase", "--autostash", "-q"], check=True, cwd=BASE)
            subprocess.run(["git", "push", "-q"], check=True, cwd=BASE)

    except subprocess.CalledProcessError as error:
        if required:
            raise RuntimeError(
                "Não consegui salvar a lista de vídeos postados no GitHub. "
                "Confira em Settings > Actions > General > Workflow permissions "
                "se está em 'Read and write permissions'. "
                "Por segurança, nada foi enviado ao YouTube."
            ) from error
        print("Aviso: não consegui salvar no GitHub agora:", error)


# ==========================================
# EDIÇÃO
# ==========================================

def make_card(title, creator, output):
    image = Image.new("RGBA", (1080, 420), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    large = ImageFont.truetype(FONT, 62)
    small = ImageFont.truetype(FONT, 38)
    lines = "\n".join(textwrap.wrap(title.upper(), 23))
    draw.multiline_text((540, 8), lines, font=large, fill="white",
                        anchor="ma", align="center", spacing=12,
                        stroke_width=6, stroke_fill="black")
    draw.text((540, 360), f"Criador: {creator}", font=small, fill="white",
              anchor="ma", stroke_width=4, stroke_fill="black")
    image.save(output)


def edit_video(source, photo, card, output):
    """Vertical 1080x1920: fundo desfocado, clipe no meio, sua foto no topo, título embaixo."""
    filt = (
        "[0:v]split=2[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "scale=108:192,boxblur=3:2,scale=1080:1920[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=0:(H-h)/2[v1];"
        "[1:v]scale=w=1000:h=380:force_original_aspect_ratio=decrease[foto];"
        "[v1][foto]overlay=(W-w)/2:150[v2];"
        "[2:v]scale=1080:380:force_original_aspect_ratio=decrease[card];"
        "[v2][card]overlay=(W-w)/2:1330[v]"
    )
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source), "-i", str(photo), "-i", str(card),
        "-filter_complex", filt, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(output)
    ], check=True)


def download_clip(clip, raw):
    # O passo da IA já baixa o clipe; se o arquivo existir, reaproveita.
    if raw.exists():
        return
    subprocess.run([
        "yt-dlp", "-q", "--no-playlist", "--force-overwrites",
        "-f", "best[height<=1080]/best", "-o", str(raw), clip["url"]
    ], check=True)


# ==========================================
# YOUTUBE
# ==========================================

def upload(video, title, clip, privacy):
    credentials = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    youtube = build("youtube", "v3", credentials=credentials)
    creator = clip.get("broadcaster_name") or clip.get("canal") or "Criador"
    original = clip.get("title") or clip.get("titulo") or ""
    url = clip.get("url", "")
    body = {
        "snippet": {
            "title": (title + " #Shorts")[:100],
            "description": f"Clipe de {creator} na Twitch: {url}\nTítulo original: {original}\n\n#Shorts #Cortes #Twitch #Gaming",
            "categoryId": "20",
            "tags": ["shorts", "cortes", "twitch", "gaming", creator[:30]],
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        "paidProductPlacementDetails": {"hasPaidProductPlacement": False},
    }
    request = youtube.videos().insert(
        part="snippet,status,paidProductPlacementDetails",
        body=body,
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


# ==========================================
# PRINCIPAL
# ==========================================

def main():
    if not PRIVATE_TEST and not AUTO_PUBLISH:
        print("Publicação automática desativada. Defina a variável AUTO_PUBLISH=true após validar o teste privado.")
        return

    required = ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Secrets do YouTube ausentes: " + ", ".join(missing))
    if not PHOTO.exists():
        raise RuntimeError("O arquivo foto.png não foi encontrado na raiz do repositório.")

    if not POSTED.exists():
        save_json(POSTED, {})
    if not HISTORY.exists():
        save_json(HISTORY, [])

    clips = read_json(DATA / "analise_ia.json", [])
    posted = read_json(POSTED, {})
    history = read_json(HISTORY, [])
    if isinstance(posted, list):
        posted = {clip_id: ["", "", ""] for clip_id in posted}

    if not PRIVATE_TEST and posted_since(posted, timedelta(hours=24)) >= MAX_PER_DAY:
        print(f"Limite de {MAX_PER_DAY} vídeos nas últimas 24 horas já foi atingido. Volto no próximo ciclo.")
        return

    eligible = []
    for clip in clips:
        analysis = clip.get("analise_ia") or {}
        clip_id = clip.get("id")
        if not clip_id or clip_id in posted:
            continue
        if analysis.get("erro") or analysis.get("conteudo_improprio") is True:
            continue
        try:
            score = float(analysis.get("potencial", 0))
            views = int(clip.get("view_count", clip.get("visualizacoes", 0)))
            duration = float(clip.get("duration", clip.get("duracao", 0)))
        except (TypeError, ValueError):
            continue
        if score < MIN_SCORE or views < MIN_VIEWS or duration < 10:
            continue
        created_at = parse_time(clip.get("created_at") or clip.get("criado_em") or "")
        if not created_at or (datetime.now(timezone.utc) - created_at) > timedelta(hours=MAX_IDADE_HORAS):
            continue  # clipe velho demais
        if not PRIVATE_TEST:
            channel_id = clip.get("broadcaster_id") or clip.get("canal_id")
            if channel_posted_recently(posted, channel_id) or same_moment(clip, posted):
                continue
        eligible.append((score, views, clip))

    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not eligible:
        print("Nenhum clipe novo passou pelos critérios de publicação.")
        return

    TMP.mkdir(exist_ok=True)
    limit = 1 if PRIVATE_TEST else MAX_PER_RUN
    privacy = "private" if PRIVATE_TEST else PRIVACY
    sent = 0

    for score, views, clip in eligible:
        if sent >= limit:
            break
        clip_id = clip["id"]
        analysis = clip.get("analise_ia") or {}
        creator = clip.get("broadcaster_name") or clip.get("canal") or "Criador"
        original = (clip.get("title") or clip.get("titulo") or "Clipe")[:60]
        title = (analysis.get("titulo") or f"{creator}: {original}")[:85]
        created = clip.get("created_at") or clip.get("criado_em") or ""
        channel_id = clip.get("broadcaster_id") or clip.get("canal_id") or ""
        raw = TMP / f"{clip_id}.mp4"
        card = TMP / f"{clip_id}-card.png"
        final = TMP / f"{clip_id}-final.mp4"

        if not PRIVATE_TEST:
            # Confere de novo (outro clipe do mesmo momento pode ter acabado de sair).
            if channel_posted_recently(posted, channel_id) or same_moment(clip, posted):
                continue
            # Reserva o clipe ANTES de enviar e grava no GitHub. Se não der para
            # gravar, para tudo: assim nunca existe vídeo repetido.
            posted[clip_id] = ["", created, channel_id]
            save_json(POSTED, posted)
            save_state_to_github(required=True)

        try:
            print(f"Preparando {creator} | {title} | {views} views | IA {score}")
            download_clip(clip, raw)
            make_card(title, creator, card)
            edit_video(raw, PHOTO, card, final)
            video_id = upload(final, title, clip, privacy)
        except Exception as error:
            print(f"Falha ao publicar {clip_id}: {error}")
            if PRIVATE_TEST:
                raise
            continue

        now = datetime.now(timezone.utc).isoformat()
        if not PRIVATE_TEST:
            posted[clip_id] = [now, created, channel_id]
        history.append({
            "quando": now,
            "streamer": creator,
            "jogo": clip.get("categoria", ""),
            "titulo": title,
            "titulo_original": original,
            "views": views,
            "youtube": f"https://youtube.com/shorts/{video_id}",
            "twitch": clip.get("url", ""),
            "privacidade": privacy,
            "pontuacao_ia": score,
        })
        save_json(POSTED, posted)
        save_json(HISTORY, history[-500:])
        save_state_to_github()
        print(f"Publicado ({privacy}): https://youtube.com/shorts/{video_id}")
        sent += 1

    print(f"Finalizado. Vídeos enviados nesta execução: {sent}")


if __name__ == "__main__":
    main()
