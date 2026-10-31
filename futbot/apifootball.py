"""Fuente secundaria: API-Football (api-sports.io), plan Free = 100 peticiones/día.
Uso: cuotas pre-partido de BWIN (y otras casas) + respaldo si SofaScore cae.
Lleva un contador diario para no pasarse del presupuesto."""
import difflib
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone

from . import config
from .http import get_json

log = logging.getLogger(__name__)
BASE = "https://v3.football.api-sports.io"
_BUDGET_FILE = config.DATA_DIR / "apifootball_budget.json"


def enabled():
    return bool(config.APIFOOTBALL_KEY)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _budget():
    try:
        b = json.loads(_BUDGET_FILE.read_text())
    except Exception:
        b = {}
    if b.get("date") != _today():
        b = {"date": _today(), "used": 0}
    return b


def remaining():
    return config.APIFOOTBALL_DAILY_BUDGET - _budget()["used"]


def _call(path, params):
    if not enabled():
        return None
    b = _budget()
    if b["used"] >= config.APIFOOTBALL_DAILY_BUDGET:
        log.warning("API-Football: presupuesto diario agotado (%s)", b["used"])
        return None
    data = get_json(f"{BASE}/{path}", headers={"x-apisports-key": config.APIFOOTBALL_KEY},
                    params=params, timeout=12)
    b["used"] += 1
    _BUDGET_FILE.write_text(json.dumps(b))
    if data and data.get("errors"):
        log.warning("API-Football errores: %s", data["errors"])
    return (data or {}).get("response")


# ------------------------------------------------------------------ emparejar equipos
def _norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(fc|cf|ac|as|ss|sc|afc|cd|ud|rc|rcd|sd|ssc|vfb|vfl|tsg|1\.|fsv|sv|bv|ogc|stade|club|de|calcio)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def fixtures_for_date(date=None):
    """1 petición al día (cacheada en disco) con todos los partidos de las 7 competiciones."""
    date = date or _today()
    cache = config.DATA_DIR / f"af_fixtures_{date}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    resp = _call("fixtures", {"date": date}) or []
    leagues = {v[1] for v in config.COMPETITIONS.values()}
    fx = [{
        "fixture_id": f["fixture"]["id"],
        "league_id": f["league"]["id"],
        "home": f["teams"]["home"]["name"],
        "away": f["teams"]["away"]["name"],
    } for f in resp if f.get("league", {}).get("id") in leagues]
    if resp:
        cache.write_text(json.dumps(fx))
    return fx


def find_fixture(home, away, date=None):
    best, score = None, 0.0
    for f in fixtures_for_date(date):
        s = (_sim(home, f["home"]) + _sim(away, f["away"])) / 2
        if s > score:
            best, score = f, s
    return best if score >= 0.6 else None


# ------------------------------------------------------------------ cuotas
def prematch_odds(fixture_id, prefer="bwin"):
    """Cuotas pre-partido. Prioriza Bwin; si no está, la primera casa disponible.
    Devuelve {'bookmaker', '1x2': {'1','X','2'}, 'dc': {'1X','12','X2'}, 'ou': {'0.5': {'Over','Under'}, ...}}"""
    resp = _call("odds", {"fixture": fixture_id})
    if not resp:
        return None
    books = resp[0].get("bookmakers", [])
    if not books:
        return None
    book = next((b for b in books if prefer in b.get("name", "").lower()), books[0])
    out = {"bookmaker": book.get("name"), "1x2": {}, "dc": {}, "ou": {}}
    dc_map = {"Home/Draw": "1X", "Home/Away": "12", "Draw/Away": "X2"}
    for bet in book.get("bets", []):
        name = bet.get("name")
        for v in bet.get("values", []):
            val, odd = str(v.get("value")), float(v.get("odd"))
            if name == "Match Winner":
                out["1x2"][{"Home": "1", "Draw": "X", "Away": "2"}.get(val, val)] = odd
            elif name == "Double Chance":
                out["dc"][dc_map.get(val, val)] = odd
            elif name == "Goals Over/Under":
                side, _, line = val.partition(" ")
                out["ou"].setdefault(line, {})[side] = odd
    return out


# ------------------------------------------------------------------ respaldo en vivo
def live_fixtures_with_reds():
    """Respaldo si SofaScore está bloqueado: 1 petición cubre las 7 competiciones.
    Devuelve [{fixture_id, home, away, minute, home_score, away_score, reds: [...]}]"""
    ids = "-".join(str(v[1]) for v in config.COMPETITIONS.values())
    resp = _call("fixtures", {"live": ids}) or []
    out = []
    for f in resp:
        home_id = f["teams"]["home"]["id"]
        reds = [{
            "minute": (e.get("time") or {}).get("elapsed"),
            "added": (e.get("time") or {}).get("extra") or 0,
            "is_home": (e.get("team") or {}).get("id") == home_id,
            "card_type": "roja_directa" if e.get("detail") == "Red Card" else "doble_amarilla",
            "player": (e.get("player") or {}).get("name"),
        } for e in f.get("events") or []
            if e.get("type") == "Card" and e.get("detail") in ("Red Card", "Second Yellow card")]
        out.append({
            "fixture_id": f["fixture"]["id"],
            "league_id": f["league"]["id"],
            "home": f["teams"]["home"]["name"],
            "away": f["teams"]["away"]["name"],
            "minute": (f["fixture"].get("status") or {}).get("elapsed"),
            "home_score": f["goals"]["home"] or 0,
            "away_score": f["goals"]["away"] or 0,
            "reds": reds,
        })
    return out


def statistics(fixture_id):
    resp = _call("fixtures/statistics", {"fixture": fixture_id}) or []
    keymap = {"Ball Possession": "possession", "expected_goals": "xg", "Total Shots": "shots_total",
              "Shots on Goal": "shots_on_target", "Corner Kicks": "corners"}
    res = {"home": {}, "away": {}}
    for side, block in zip(("home", "away"), resp[:2]):
        for s in block.get("statistics", []):
            k = keymap.get(s.get("type"))
            if k:
                v = s.get("value")
                try:
                    res[side][k] = float(str(v).replace("%", "")) if v is not None else None
                except ValueError:
                    res[side][k] = None
    return res
