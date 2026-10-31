"""Fuente para la nube: API pública de ESPN (sin clave). Mismo interfaz que sofascore.py.
SofaScore bloquea las IPs de GitHub Actions (403); ESPN no.

- scoreboard por liga: marcador, reloj, goles y tarjetas (details), cuotas pre-partido (moneyline)
- summary por partido: estadísticas (posesión, tiros, a puerta, córners). ESPN no da xG."""
import logging
import time
from datetime import datetime, timezone

from . import config
from .http import get_json

log = logging.getLogger(__name__)
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# id SofaScore (clave interna de config) -> slug ESPN
LEAGUES = {8: "esp.1", 17: "eng.1", 23: "ita.1", 35: "ger.1", 34: "fra.1", 7: "uefa.champions", 679: "uefa.europa"}
FIRST_HALF, HALFTIME, SECOND_HALF = 6, 31, 7  # mismos códigos que sofascore para el Scout

_EVENTS = {}       # match_id -> evento crudo más reciente
_LEAGUE_OF = {}    # match_id -> comp_id
_ODDS_FIRST = {}   # match_id -> cuotas vistas la primera vez (pre-partido)


def _american_to_decimal(ml):
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return None
    if ml == 0:
        return None
    return round(1 + (ml / 100 if ml > 0 else 100 / -ml), 3)


def _status_code(st):
    name = ((st or {}).get("type") or {}).get("name", "")
    state = ((st or {}).get("type") or {}).get("state")
    if "HALFTIME" in name:
        return HALFTIME
    if state == "in":
        return FIRST_HALF if (st.get("period") or 1) == 1 else SECOND_HALF
    if state == "post":
        return 100
    return 0


def _minute(st):
    disp = (st or {}).get("displayClock") or ""
    digits = "".join(c for c in disp.split("+")[0] if c.isdigit())
    if digits:
        return int(digits)
    clock = (st or {}).get("clock")
    return int(clock // 60) + 1 if clock else None


def _simplify(ev, comp_id):
    comp = (ev.get("competitions") or [{}])[0]
    teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    home, away = teams.get("home", {}), teams.get("away", {})
    st = comp.get("status") or ev.get("status") or {}
    try:
        start_ts = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp()
    except Exception:
        start_ts = None
    mid = int(ev["id"])
    _EVENTS[mid] = ev
    _LEAGUE_OF[mid] = comp_id
    reds = red_cards(mid)
    odds = _parse_odds(comp)
    if odds and mid not in _ODDS_FIRST:
        _ODDS_FIRST[mid] = odds
    return {
        "match_id": mid,
        "competition_id": comp_id,
        "competition": config.COMPETITIONS[comp_id][0],
        "home": (home.get("team") or {}).get("displayName"),
        "away": (away.get("team") or {}).get("displayName"),
        "home_team_id": (home.get("team") or {}).get("id"),
        "home_score": int(home.get("score") or 0),
        "away_score": int(away.get("score") or 0),
        "status_code": _status_code(st),
        "start_ts": start_ts,
        "minute": _minute(st) if _status_code(st) in (FIRST_HALF, SECOND_HALF) else (45 if _status_code(st) == HALFTIME else None),
        "home_reds": sum(r["is_home"] for r in reds),
        "away_reds": sum(not r["is_home"] for r in reds),
    }


def _scoreboard(comp_id, date=None):
    params = {"dates": date} if date else None
    data = get_json(f"{BASE}/{LEAGUES[comp_id]}/scoreboard", params=params, timeout=10) or {}
    return [_simplify(e, comp_id) for e in data.get("events", [])]


def _all(date=None):
    out = []
    for comp_id in LEAGUES:
        try:
            out += _scoreboard(comp_id, date)
        except Exception as e:
            log.warning("ESPN %s: %s", LEAGUES[comp_id], e)
    return out


def scheduled_today(date=None):
    date = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    return _all(date.replace("-", ""))


def live_events():
    return [e for e in _all() if e["status_code"] in (FIRST_HALF, HALFTIME, SECOND_HALF)]


def event_detail(match_id):
    comp_id = _LEAGUE_OF.get(match_id)
    leagues = [comp_id] if comp_id else list(LEAGUES)
    for c in leagues:
        data = get_json(f"{BASE}/{LEAGUES[c]}/summary", params={"event": match_id}) or {}
        comp = ((data.get("header") or {}).get("competitions") or [None])[0]
        if not comp:
            continue
        teams = {t.get("homeAway"): t for t in comp.get("competitors", [])}
        st = comp.get("status") or {}
        return {"match_id": match_id, "status_code": _status_code(st),
                "home_score": int(teams.get("home", {}).get("score") or 0),
                "away_score": int(teams.get("away", {}).get("score") or 0)}
    return None


def _details(match_id):
    ev = _EVENTS.get(match_id) or {}
    comp = (ev.get("competitions") or [{}])[0]
    home_id = next((c.get("team", {}).get("id") for c in comp.get("competitors", []) if c.get("homeAway") == "home"), None)
    return comp.get("details") or [], home_id


def _detail_minute(d):
    disp = ((d.get("clock") or {}).get("displayValue") or "")
    base, _, extra = disp.replace("'", "").partition("+")
    try:
        return int(base), int(extra or 0)
    except ValueError:
        v = (d.get("clock") or {}).get("value")
        return (int(v // 60) + 1 if v else None), 0


def red_cards(match_id):
    dets, home_id = _details(match_id)
    out = []
    for d in dets:
        text = ((d.get("type") or {}).get("text") or "").lower()
        if not (d.get("redCard") or "red card" in text):
            continue
        minute, added = _detail_minute(d)
        players = d.get("athletesInvolved") or []
        out.append({
            "minute": minute, "added": added,
            "is_home": (d.get("team") or {}).get("id") == home_id,
            "card_type": "doble_amarilla" if "yellow" in text else "roja_directa",
            "player": players[0].get("displayName") if players else None,
        })
    return out


def goals(match_id):
    dets, home_id = _details(match_id)
    out = []
    for d in dets:
        if not d.get("scoringPlay"):
            continue
        minute, _ = _detail_minute(d)
        out.append({"minute": minute, "is_home": (d.get("team") or {}).get("id") == home_id})
    return sorted(out, key=lambda g: g["minute"] or 0)


_STAT_MAP = {"possessionPct": "possession", "totalShots": "shots_total", "shotsOnTarget": "shots_on_target",
             "wonCorners": "corners"}


def statistics(match_id):
    res = {"home": {k: None for k in ("possession", "xg", "shots_total", "shots_on_target", "corners",
                                      "big_chances", "dangerous_attacks")}}
    res["away"] = dict(res["home"])
    comp_id = _LEAGUE_OF.get(match_id)
    if not comp_id:
        return res
    data = get_json(f"{BASE}/{LEAGUES[comp_id]}/summary", params={"event": match_id}) or {}
    _, home_id = _details(match_id)
    for t in (data.get("boxscore") or {}).get("teams", []):
        side = "home" if (t.get("team") or {}).get("id") == home_id or t.get("homeAway") == "home" else "away"
        for s in t.get("statistics", []):
            k = _STAT_MAP.get(s.get("name"))
            if k:
                try:
                    res[side][k] = float(str(s.get("displayValue")).replace("%", ""))
                except (TypeError, ValueError):
                    pass
    return res


def _parse_odds(comp):
    o = (comp.get("odds") or [None])[0]
    if not o:
        return None
    one = _american_to_decimal((o.get("homeTeamOdds") or {}).get("moneyLine"))
    two = _american_to_decimal((o.get("awayTeamOdds") or {}).get("moneyLine"))
    x = _american_to_decimal((o.get("drawOdds") or {}).get("moneyLine"))
    if not (one and two and x):
        return None
    return {"1": one, "X": x, "2": two, "provider": (o.get("provider") or {}).get("name")}


def odds(match_id):
    """Mismo formato que sofascore.odds: initial = primera cuota vista (pre-partido), current = la última."""
    ev = _EVENTS.get(match_id)
    cur = _parse_odds((ev.get("competitions") or [{}])[0]) if ev else None
    first = _ODDS_FIRST.get(match_id) or cur
    out = {"1x2": {}, "dc": {}, "ou": {}, "provider": (first or {}).get("provider")}
    if first:
        for k in ("1", "X", "2"):
            out["1x2"][k] = {"initial": first[k], "current": (cur or first)[k]}
    return out
