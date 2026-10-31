"""Fuente principal (gratuita, no oficial): SofaScore.
Endpoints públicos que usa la propia web. Pueden cambiar sin aviso: por eso todos los parsers
son defensivos y existe `python -m futbot diagnose` para comprobarlos."""
import logging
import time
from datetime import datetime, timezone

from . import config
from .http import get_json

log = logging.getLogger(__name__)
BASE = "https://api.sofascore.com/api/v1"

FIRST_HALF, HALFTIME, SECOND_HALF = 6, 31, 7


# ------------------------------------------------------------------ helpers
def _comp_id(ev):
    return ((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("id")


def is_target(ev):
    return _comp_id(ev) in config.COMPETITIONS


def frac_to_decimal(frac):
    """'5/2' -> 3.5 ; '1/1' -> 2.0 ; '2.10' -> 2.1"""
    if frac is None:
        return None
    s = str(frac).strip()
    try:
        if "/" in s:
            a, b = s.split("/")
            return round(1 + float(a) / float(b), 3)
        return round(float(s), 3)
    except (ValueError, ZeroDivisionError):
        return None


def current_minute(ev, now=None):
    """Minuto de juego aproximado a partir del timestamp de inicio del periodo."""
    now = now or time.time()
    code = (ev.get("status") or {}).get("code")
    t = ev.get("time") or {}
    start = t.get("currentPeriodStartTimestamp")
    if code == HALFTIME:
        return 45
    if not start or code not in (FIRST_HALF, SECOND_HALF):
        return None
    base = 0 if code == FIRST_HALF else 45
    return base + int((now - start) // 60) + 1


def simplify_event(ev):
    comp = _comp_id(ev)
    return {
        "match_id": ev.get("id"),
        "competition_id": comp,
        "competition": config.COMPETITIONS.get(comp, ("?",))[0],
        "home": (ev.get("homeTeam") or {}).get("name"),
        "away": (ev.get("awayTeam") or {}).get("name"),
        "home_score": (ev.get("homeScore") or {}).get("current", 0) or 0,
        "away_score": (ev.get("awayScore") or {}).get("current", 0) or 0,
        "status_code": (ev.get("status") or {}).get("code"),
        "start_ts": ev.get("startTimestamp"),
        "minute": current_minute(ev),
        # SofaScore incluye estos contadores en el listado en vivo cuando hay rojas
        "home_reds": ev.get("homeRedCards"),
        "away_reds": ev.get("awayRedCards"),
    }


# ------------------------------------------------------------------ endpoints
def scheduled_today(date=None):
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = get_json(f"{BASE}/sport/football/scheduled-events/{date}") or {}
    return [simplify_event(e) for e in data.get("events", []) if is_target(e)]


def live_events():
    data = get_json(f"{BASE}/sport/football/events/live") or {}
    return [simplify_event(e) for e in data.get("events", []) if is_target(e)]


def event_detail(match_id):
    data = get_json(f"{BASE}/event/{match_id}") or {}
    ev = data.get("event")
    return simplify_event(ev) if ev else None


def red_cards(match_id):
    """Lista de expulsiones: [{minute, added, is_home, card_type, player}]"""
    data = get_json(f"{BASE}/event/{match_id}/incidents") or {}
    out = []
    for inc in data.get("incidents", []):
        if inc.get("incidentType") != "card":
            continue
        cls = (inc.get("incidentClass") or "").lower()
        if cls not in ("red", "yellowred"):
            continue
        out.append({
            "minute": inc.get("time"),
            "added": inc.get("addedTime") or 0,
            "is_home": bool(inc.get("isHome")),
            "card_type": "roja_directa" if cls == "red" else "doble_amarilla",
            "player": (inc.get("player") or {}).get("name") or inc.get("playerName"),
        })
    return out


def goals(match_id):
    """Goles en orden: [{minute, is_home (equipo que suma el gol)}]"""
    data = get_json(f"{BASE}/event/{match_id}/incidents") or {}
    out = [{"minute": inc.get("time"), "is_home": bool(inc.get("isHome"))}
           for inc in data.get("incidents", []) if inc.get("incidentType") == "goal"]
    return sorted(out, key=lambda g: g["minute"] or 0)


_STAT_KEYS = {
    # clave normalizada: (keys SofaScore, nombres SofaScore)
    "possession": ({"ballPossession"}, {"ball possession"}),
    "xg": ({"expectedGoals"}, {"expected goals"}),
    "shots_total": ({"totalShotsOnGoal", "totalShots"}, {"total shots"}),
    "shots_on_target": ({"shotsOnGoal", "shotsOnTarget"}, {"shots on target"}),
    "corners": ({"cornerKicks"}, {"corner kicks"}),
    "big_chances": ({"bigChanceCreated"}, {"big chances"}),
    "dangerous_attacks": ({"dangerousAttacks"}, {"dangerous attacks"}),
}


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("%", "").strip())
    except ValueError:
        return None


def statistics(match_id):
    """Devuelve {'home': {...}, 'away': {...}} con claves normalizadas (None si no hay dato)."""
    data = get_json(f"{BASE}/event/{match_id}/statistics") or {}
    blocks = data.get("statistics", [])
    block = next((b for b in blocks if b.get("period") == "ALL"), blocks[0] if blocks else None)
    res = {"home": {k: None for k in _STAT_KEYS}, "away": {k: None for k in _STAT_KEYS}}
    if not block:
        return res
    for group in block.get("groups", []):
        for item in group.get("statisticsItems", []):
            key, name = item.get("key"), (item.get("name") or "").lower()
            for norm, (keys, names) in _STAT_KEYS.items():
                if res["home"][norm] is None and (key in keys or name in names):
                    res["home"][norm] = _num(item.get("homeValue", item.get("home")))
                    res["away"][norm] = _num(item.get("awayValue", item.get("away")))
    return res


def odds(match_id):
    """Cuotas de SofaScore (su bookmaker de referencia).
    'initial' = cuota de apertura (pre-partido), 'current' = cuota actual (en vivo).
    Devuelve {'1x2': {'1':{initial,current},...}, 'dc': {...}, 'ou': {'2.5': {'Over':..}}}"""
    data = get_json(f"{BASE}/event/{match_id}/odds/1/all") or {}
    out = {"1x2": {}, "dc": {}, "ou": {}}
    for m in data.get("markets", []):
        name = (m.get("marketName") or "").lower()
        group = m.get("choiceGroup")
        for c in m.get("choices", []):
            pair = {"initial": frac_to_decimal(c.get("initialFractionalValue")),
                    "current": frac_to_decimal(c.get("fractionalValue"))}
            if pair["initial"] is None:
                pair["initial"] = pair["current"]
            cname = c.get("name")
            if name in ("full time", "1x2", "match winner"):
                out["1x2"][cname] = pair
            elif name == "double chance":
                out["dc"][cname] = pair
            elif name in ("match goals", "total goals", "over/under") and group:
                out["ou"].setdefault(str(group), {})[cname] = pair
    return out
