import json
import os
import subprocess
import textwrap
from datetime import datetime, timezone
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
MIN_VIEWS = int(os.getenv("MIN_CLIP_VIEWS", "100"))
MAX_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "1"))
AUTO_PUBLISH = os.getenv("AUTO_PUBLISH", "false").lower() == "true"
PRIVATE_TEST = os.getenv("PRIVATE_TEST", "false").lower() == "true"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


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


def edit_video(source, card, output):
    filt = (
        "[0:v]split=2[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "scale=108:192,boxblur=3:2,scale=1080:1920[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=0:(H-h)/2[v1];"
        "[1:v]scale=1080:380:force_original_aspect_ratio=decrease[card];"
        "[v1][card]overlay=(W-w)/2:1330[v]"
    )
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source), "-i", str(card),
        "-filter_complex", filt, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(output)
    ], check=True)


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
    }
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


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

    clips = read_json(DATA / "analise_ia.json", [])
    posted = read_json(POSTED, {})
    history = read_json(HISTORY, [])
    if isinstance(posted, list):
        posted = {clip_id: ["", "", ""] for clip_id in posted}

    eligible = []
    for clip in clips:
        analysis = clip.get("analise_ia") or {}
        clip_id = clip.get("id")
        if not clip_id or clip_id in posted:
            continue
        if analysis.get("conteudo_improprio") is True:
            continue
        try:
            score = float(analysis.get("potencial", 0))
            views = int(clip.get("view_count", clip.get("visualizacoes", 0)))
            duration = float(clip.get("duration", clip.get("duracao", 0)))
        except (TypeError, ValueError):
            continue
        if score < 60 or views < MIN_VIEWS or duration < 10:
            continue
        eligible.append((score, views, clip))

    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not eligible:
        print("Nenhum clipe novo passou pelos critérios de publicação.")
        return

    TMP.mkdir(exist_ok=True)
    limit = 1 if PRIVATE_TEST else MAX_PER_RUN
    privacy = "private" if PRIVATE_TEST else "public"
    sent = 0

    for score, views, clip in eligible:
        if sent >= limit:
            break
        clip_id = clip["id"]
        creator = clip.get("broadcaster_name") or clip.get("canal") or "Criador"
        original = (clip.get("title") or clip.get("titulo") or "Clipe")[:60]
        title = f"{creator}: {original}"[:85]
        raw = TMP / f"{clip_id}.mp4"
        card = TMP / f"{clip_id}-card.png"
        final = TMP / f"{clip_id}-final.mp4"

        try:
            print(f"Preparando {creator} | {original} | {views} views | IA {score}")
            subprocess.run([
                "yt-dlp", "-q", "--no-playlist", "--force-overwrites",
                "-f", "best[height<=1080]/best", "-o", str(raw), clip["url"]
            ], check=True)
            make_card(title, creator, card)
            edit_video(raw, card, final)
            video_id = upload(final, title, clip, privacy)
        except Exception as error:
            print(f"Falha ao publicar {clip_id}: {error}")
            if PRIVATE_TEST:
                raise
            continue

        now = datetime.now(timezone.utc).isoformat()
        posted[clip_id] = [
            now,
            clip.get("created_at") or clip.get("criado_em") or "",
            clip.get("broadcaster_id") or clip.get("canal_id") or ""
        ]
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
        print(f"Publicado ({privacy}): https://youtube.com/shorts/{video_id}")
        sent += 1

    print(f"Finalizado. Vídeos enviados nesta execução: {sent}")


if __name__ == "__main__":
    main()
