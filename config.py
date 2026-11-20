
import os
from pathlib import Path

# Pastas do projeto
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "dados"
TMP_DIR = BASE_DIR / "tmp"

DATA_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)

# Plataformas
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "")

KICK_CLIENT_ID = os.getenv("KICK_CLIENT_ID", "")
KICK_CLIENT_SECRET = os.getenv("KICK_CLIENT_SECRET", "")

# Inteligência artificial
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# YouTube
YT_CLIENT_ID = os.getenv("YT_CLIENT_ID", "")
YT_CLIENT_SECRET = os.getenv("YT_CLIENT_SECRET", "")
YT_REFRESH_TOKEN = os.getenv("YT_REFRESH_TOKEN", "")

# Limites de busca
MAX_STREAMS = int(os.getenv("MAX_STREAMS", "100"))
MAX_CLIPS_PER_CHANNEL = int(
    os.getenv("MAX_CLIPS_PER_CHANNEL", "10")
)

# Limites de publicação
MAX_PER_DAY = int(os.getenv("MAX_PER_DAY", "5"))
MIN_CLIP_VIEWS = int(os.getenv("MIN_CLIP_VIEWS", "100"))

# Modos
AUTO_PUBLISH = os.getenv("AUTO_PUBLISH", "false").lower() == "true"

# Arquivos de dados
POSTED_FILE = DATA_DIR / "posted.json"
HISTORY_FILE = DATA_DIR / "historico.json"
STATUS_FILE = DATA_DIR / "status.json"
CANDIDATES_FILE = DATA_DIR / "candidatos.json"
