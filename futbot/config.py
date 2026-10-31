"""Configuración central. Todo lo sensible viene de variables de entorno (.env o GitHub Secrets)."""
import os
from pathlib import Path

try:  # .env opcional en local
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------- credenciales
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
APIFOOTBALL_KEY = os.getenv("APIFOOTBALL_KEY", "")

# LLM del Estratega. Por defecto "auto": usa GRATIS lo que tenga clave, en este orden:
#   GLM de Z.ai (OPENAI_COMPAT_API_KEY) -> Gemini de AI Studio (GEMINI_API_KEY, empieza por AIza)
# Otros valores: "openai_compat" | "gemini" | "anthropic" (de pago) | "none"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_MODEL = os.getenv("GITHUB_MODEL", "openai/gpt-4o-mini")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "auto").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
OPENAI_COMPAT_BASE_URL = os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.z.ai/api/paas/v4")
OPENAI_COMPAT_API_KEY = os.getenv("OPENAI_COMPAT_API_KEY", "")
OPENAI_COMPAT_MODEL = os.getenv("OPENAI_COMPAT_MODEL", "glm-4.5-flash")

DRY_RUN = os.getenv("DRY_RUN", "0") == "1"  # no envía a Telegram, imprime por consola

# ---------------------------------------------------------------- competiciones
# id SofaScore (uniqueTournament) -> (nombre, id API-Football)
COMPETITIONS = {
    8: ("LaLiga", 140),
    17: ("Premier League", 39),
    23: ("Serie A", 135),
    35: ("Bundesliga", 78),
    34: ("Ligue 1", 61),
    7: ("Champions League", 2),
    679: ("Europa League", 3),
}
COMP_FLAGS = {8: "🇪🇸", 17: "🏴󠁧󠁢󠁥󠁮󠁧󠁿", 23: "🇮🇹", 35: "🇩🇪", 34: "🇫🇷", 7: "⭐", 679: "🟠"}

# ---------------------------------------------------------------- Scout
DATA_SOURCE = os.getenv("DATA_SOURCE", "auto").lower()  # auto | sofascore | espn | apifootball
MAX_RED_MINUTE = int(os.getenv("MAX_RED_MINUTE", "30"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
INCIDENT_SWEEP_SECONDS = 180          # barrido de seguridad de incidencias
WINDOW_BEFORE_KICKOFF_MIN = 3         # empieza a vigilar 3' antes del inicio
WINDOW_AFTER_KICKOFF_MIN = 45         # 30' de juego + retrasos/descuento
LOOKAHEAD_MIN = int(os.getenv("LOOKAHEAD_MIN", "40"))  # si nada empieza en 40', la ejecución termina
MAX_RUNTIME_MIN = int(os.getenv("MAX_RUNTIME_MIN", "340"))  # < 6h límite de GitHub Actions

# ---------------------------------------------------------------- modelo
# Multiplicadores de ritmo goleador tras la roja. HIPÓTESIS INICIALES conservadoras:
# el Auditor debe recalibrarlas con datos reales.
RED_MULT_ELEVEN = float(os.getenv("RED_MULT_ELEVEN", "1.30"))
RED_MULT_TEN = float(os.getenv("RED_MULT_TEN", "0.70"))
DEFAULT_LAMBDA_HOME = 1.50
DEFAULT_LAMBDA_AWAY = 1.15
VALUE_MARGIN = float(os.getenv("VALUE_MARGIN", "1.08"))  # exige un 8% sobre la cuota justa
MIN_USEFUL_ODDS = 1.25  # por debajo, 1 € no compensa el riesgo

# ---------------------------------------------------------------- estrategia "favorito pierde"
FAV_ENABLED = os.getenv("FAV_ENABLED", "1") == "1"
FAV_MIN_PROB = float(os.getenv("FAV_MIN_PROB", "0.60"))   # ≈ cuota pre-partido ≤ 1.55-1.60
FAV_MAX_MINUTE = int(os.getenv("FAV_MAX_MINUTE", "30"))

# ---------------------------------------------------------------- dinero real
STAKE_MEDIA = float(os.getenv("STAKE_MEDIA", "0.5"))
STAKE_ALTA = float(os.getenv("STAKE_ALTA", "1"))
STAKE_ALTA_VALUE = float(os.getenv("STAKE_ALTA_VALUE", "2"))
MONTHLY_TARGET = float(os.getenv("MONTHLY_TARGET", "20"))
STOP_LOSS_MONTH = float(os.getenv("STOP_LOSS_MONTH", "15"))  # si pierdes esto en el mes, el bot lo avisa

# ---------------------------------------------------------------- API-Football
APIFOOTBALL_DAILY_BUDGET = int(os.getenv("APIFOOTBALL_DAILY_BUDGET", "90"))  # free = 100/día
