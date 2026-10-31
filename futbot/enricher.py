"""Construye el trigger_payload (contrato Scout -> Estratega) con stats y cuotas.

Tipos de disparo:
  - roja_temprana:   trigger = {"type", "red", "reds_home", "reds_away"}
  - favorito_pierde: trigger = {"type", "fav_is_home", "p_fav_pre", "goal_minute"}
En ambos, `target_team` es el equipo al que se analizaría respaldar."""
import logging

from . import apifootball, sofascore
from .storage import now_iso

log = logging.getLogger(__name__)


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as e:
        log.warning("%s falló: %s", getattr(fn, "__name__", fn), e)
        return default


def build_payload(match, trigger, source="sofascore", af_fixture_id=None):
    ttype = trigger["type"]
    red_card, reds_count, adv = None, None, None
    if ttype == "roja_temprana":
        red = trigger["red"]
        rh, ra = trigger["reds_home"], trigger["reds_away"]
        target_is_home = ra > rh
        red_card = {**red, "sent_off_team": match["home"] if red["is_home"] else match["away"]}
        reds_count = {"home": rh, "away": ra}
        adv = {"name": match["home"] if target_is_home else match["away"],
               "is_home": target_is_home, "player_diff": abs(rh - ra)}
        event_minute = red["minute"]
    else:
        target_is_home = trigger["fav_is_home"]
        event_minute = trigger.get("goal_minute") or match.get("minute")
    target = {"name": match["home"] if target_is_home else match["away"], "is_home": target_is_home}

    # --- estadísticas en el momento de detectar
    stats = None
    if source == "sofascore":
        stats = _safe(sofascore.statistics, match["match_id"])
    if (not stats or stats["home"].get("shots_total") is None) and apifootball.enabled():
        af_fixture_id = af_fixture_id or (_safe(apifootball.find_fixture, match["home"], match["away"]) or {}).get("fixture_id")
        if af_fixture_id:
            stats = _safe(apifootball.statistics, af_fixture_id) or stats

    # --- cuotas: pre-partido Bwin (API-Football) y referencia en vivo (SofaScore)
    odds_pre, odds_live = None, None
    if apifootball.enabled():
        if not af_fixture_id:
            af_fixture_id = (_safe(apifootball.find_fixture, match["home"], match["away"]) or {}).get("fixture_id")
        if af_fixture_id:
            af = _safe(apifootball.prematch_odds, af_fixture_id)
            if af and af["1x2"]:
                odds_pre = {"source": f"API-Football · {af['bookmaker']}", **{k: af[k] for k in ("1x2", "dc", "ou")}}
    if source == "sofascore":
        ss = _safe(sofascore.odds, match["match_id"])
        if ss and ss["1x2"]:
            pick = lambda d, f: {k: v[f] for k, v in d.items() if v.get(f)}
            if not odds_pre:
                odds_pre = {"source": "SofaScore (apertura)", "1x2": pick(ss["1x2"], "initial"),
                            "dc": pick(ss["dc"], "initial"),
                            "ou": {l: pick(v, "initial") for l, v in ss["ou"].items()}}
            odds_live = {"source": "SofaScore (actual)", "1x2": pick(ss["1x2"], "current"),
                         "dc": pick(ss["dc"], "current"),
                         "ou": {l: pick(v, "current") for l, v in ss["ou"].items()}}

    has_stats = bool(stats and stats["home"].get("shots_total") is not None)
    payload = {
        "schema": "trigger_payload/v2",
        "trigger_type": ttype,
        "detected_at": now_iso(),
        "source": source,
        "match_id": match.get("match_id"),
        "af_fixture_id": af_fixture_id,
        "competition_id": match.get("competition_id"),
        "competition": match.get("competition"),
        "home": match["home"],
        "away": match["away"],
        "score": {"home": match["home_score"], "away": match["away_score"]},
        "minute_now": match.get("minute"),
        "event_minute": event_minute,
        "target_team": target,
        "red_card": red_card,
        "reds_count": reds_count,
        "team_with_advantage": adv,
        "favorite": ({"name": target["name"], "is_home": target_is_home, "p_prematch": trigger.get("p_fav_pre")}
                     if ttype == "favorito_pierde" else None),
        "stats_at_detection": stats,
        "odds_prematch": odds_pre,
        "odds_live": odds_live,
        "data_quality": {
            "has_stats": has_stats,
            "has_xg": bool(stats and stats["home"].get("xg") is not None),
            "has_prematch_odds": bool(odds_pre and len(odds_pre.get("1x2", {})) == 3),
            "has_live_odds": bool(odds_live and odds_live.get("1x2")),
            "detection_delay_min": max(0, (match.get("minute") or event_minute or 0) - (event_minute or 0)),
        },
    }
    return payload
