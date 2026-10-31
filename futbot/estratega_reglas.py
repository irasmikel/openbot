"""AGENTE 2a · Estratega de reglas (determinista, instantáneo).
Puntúa la oportunidad 0-100 y calcula, con el modelo Poisson, la cuota MÍNIMA a la que
tiene sentido entrar en cada mercado. Su veredicto sale en la primera alerta.

Estrategias:
  - roja_temprana:   expulsión antes del min 30 → respaldar al equipo con 11.
  - favorito_pierde: favorito claro (≥60% pre-partido) pierde antes del min 30 → respaldar al favorito."""
from . import config, model

W_DOMINANCE = {"xg": 0.35, "shots_on_target": 0.25, "shots_total": 0.15, "possession": 0.15, "corners": 0.10}


def dominance_share(stats, target_is_home):
    """Índice 0-1: cuánto dominaba el equipo analizado (0.5 = igualado)."""
    if not stats:
        return None
    me, rival = ("home", "away") if target_is_home else ("away", "home")
    total_w, acc = 0.0, 0.0
    for k, w in W_DOMINANCE.items():
        a, b = stats[me].get(k), stats[rival].get(k)
        if a is None or b is None:
            continue
        share = 0.5 if a + b == 0 else a / (a + b)
        acc += w * share
        total_w += w
    return round(acc / total_w, 3) if total_w >= 0.4 else None


def _live_odd(payload, market, line=None, label=None):
    lo = payload.get("odds_live") or {}
    if market in ("dc", "1x2"):
        return (lo.get(market) or {}).get(label)
    if market == "ou":
        return ((lo.get("ou") or {}).get(line) or {}).get("Over")
    return None


def suggest_stake(rating, primary):
    """Stake por nivel: Descartar 0 · Media 0,5 € · Alta 1 € · Alta con valor confirmado 2 €."""
    if rating == "Descartar" or not primary:
        return 0.0
    if rating == "Alta":
        return config.STAKE_ALTA_VALUE if primary.get("value") is True else config.STAKE_ALTA
    return config.STAKE_MEDIA


def _prematch(payload):
    pre = payload.get("odds_prematch") or {}
    p1x2 = model.demargin(pre.get("1x2")) if payload["data_quality"].get("has_prematch_odds") else None
    p_over25 = None
    ou25 = (pre.get("ou") or {}).get("2.5") or {}
    if ou25.get("Over") and ou25.get("Under"):
        p_over25 = model.demargin({"Over": ou25["Over"], "Under": ou25["Under"]})["Over"]
    return p1x2, p_over25


def _markets(payload, target_is_home, target_name, p1x2, p_over25, mult_t, mult_r):
    sh, sa = payload["score"]["home"], payload["score"]["away"]
    now_min = payload.get("minute_now") or payload.get("event_minute") or 0
    lh, la, fitted = model.fit_lambdas(p1x2, p_over25)
    proj = model.project(lh, la, now_min, sh, sa, target_is_home, mult_eleven=mult_t, mult_ten=mult_r)
    cands = []
    dc, win = proj["double_chance"], proj["eleven_win"]
    cands.append(("Doble oportunidad " + dc["label"], dc["p"], _live_odd(payload, "dc", label=dc["label"])))
    cands.append((f"Gana {target_name} ({win['label']})", win["p"], _live_odd(payload, "1x2", label=win["label"])))
    for line, p in proj["over"].items():
        cands.append((f"Más de {line} goles (partido)", p, _live_odd(payload, "ou", line=line)))
    markets = []
    for name, p, live in cands:
        fair, min_odds = model.fair_and_min(p)
        if not min_odds or min_odds < config.MIN_USEFUL_ODDS or p < 0.5:
            continue
        markets.append({"market": name, "p_model": round(p, 3), "fair_odds": fair, "min_odds": min_odds,
                        "live_odds_ref": live, "value": None if not live else live >= min_odds,
                        "edge": None if not live else round(live * p - 1, 3)})
    markets.sort(key=lambda m: (m["value"] is True, m["edge"] or 0, m["p_model"]), reverse=True)
    proj_out = {"lambda_prematch": {"home": lh, "away": la, "fitted": fitted}, **proj,
                "mult_target": mult_t, "mult_rival": mult_r}
    return markets, proj_out


def _finish(pts, reasons, flags, markets, proj, dom, p_pre, p1x2, extra_cap=False):
    if markets and markets[0]["value"] is True:
        pts += 8; reasons.append(f"Cuota actual por encima de la mínima en '{markets[0]['market']}'")
    elif markets and markets[0]["value"] is False:
        flags.append("A la cuota de referencia actual no hay valor: comprueba Bwin")
    if not markets:
        flags.append("Ningún mercado con cuota útil (≥ %.2f) y probabilidad > 50%%" % config.MIN_USEFUL_ODDS)
    pts = max(0, min(100, pts))
    rating = "Alta" if pts >= 70 else "Media" if pts >= 55 else "Descartar"
    if rating == "Alta" and (not p1x2 or dom is None or extra_cap or (markets and markets[0]["value"] is False)):
        rating = "Media"
    if not markets:
        rating = "Descartar"
    primary = markets[0] if markets else None
    return {
        "schema": "verdict/v2", "by": "reglas", "rating": rating, "score_0_100": pts,
        "dominance_index": dom, "p_target_prematch": p_pre,
        "markets": markets[:3], "primary_market": primary,
        "stake_suggested": suggest_stake(rating, primary),
        "reasons": reasons, "red_flags": flags, "model": proj,
    }


# ------------------------------------------------------------------ roja temprana
def evaluate_red(payload):
    adv = payload["team_with_advantage"]
    eleven_home = adv["is_home"]
    minute = payload["red_card"]["minute"]
    sh, sa = payload["score"]["home"], payload["score"]["away"]
    diff = (sh - sa) if eleven_home else (sa - sh)
    dq = payload["data_quality"]
    reasons, flags, pts = [], [], 50

    if minute <= 15:
        pts += 12; reasons.append(f"Roja muy temprana (min {minute}): ~{93 - minute}' con ventaja")
    elif minute <= 22:
        pts += 8; reasons.append(f"Roja temprana (min {minute})")
    else:
        pts += 4

    p1x2, p_over25 = _prematch(payload)
    p_pre = (p1x2["1"] if eleven_home else p1x2["2"]) if p1x2 else None
    if p_pre is not None:
        if p_pre >= 0.55:
            pts += 12; reasons.append(f"Era favorito claro ({p_pre:.0%} pre-partido)")
        elif p_pre >= 0.40:
            pts += 6; reasons.append(f"Era ligeramente favorito/parejo ({p_pre:.0%})")
        elif p_pre < 0.25:
            pts -= 12; flags.append(f"Era claro no favorito ({p_pre:.0%}): la roja no compensa toda la diferencia")
    else:
        pts -= 8; flags.append("Sin cuotas pre-partido: modelo con valores genéricos")

    dom = dominance_share(payload.get("stats_at_detection"), eleven_home)
    if dom is not None:
        if dom >= 0.60:
            pts += 12; reasons.append(f"Ya dominaba antes de la roja (índice {dom:.0%})")
        elif dom >= 0.52:
            pts += 6; reasons.append(f"Algo mejor antes de la roja (índice {dom:.0%})")
        elif dom < 0.45:
            pts -= 10; flags.append(f"Estaba siendo dominado antes de la roja (índice {dom:.0%})")
    else:
        pts -= 8; flags.append("Sin estadísticas fiables del partido")

    if eleven_home:
        pts += 4; reasons.append("Juega en casa")
    if diff <= -2:
        pts -= 15; flags.append("Pierde por 2 o más")
    elif diff == -1:
        pts -= 6; flags.append("Va perdiendo por 1")
    elif diff == 1:
        pts += 4
    elif diff >= 2:
        pts -= 4; flags.append("Ya gana por 2+: poco valor en el resultado")
    if adv["player_diff"] >= 2:
        pts += 6; reasons.append("Ventaja de 2 jugadores")
    if dq.get("detection_delay_min", 0) >= 4:
        flags.append(f"Detectada {dq['detection_delay_min']}' tarde: las cuotas ya se habrán ajustado")

    markets, proj = _markets(payload, eleven_home, adv["name"], p1x2, p_over25,
                             config.RED_MULT_ELEVEN, config.RED_MULT_TEN)
    return _finish(pts, reasons, flags, markets, proj, dom, p_pre, p1x2)


# ------------------------------------------------------------------ favorito pierde
def evaluate_fav(payload):
    fav = payload["favorite"]
    fav_home = fav["is_home"]
    minute = payload.get("event_minute") or payload.get("minute_now") or 30
    sh, sa = payload["score"]["home"], payload["score"]["away"]
    deficit = (sa - sh) if fav_home else (sh - sa)
    dq = payload["data_quality"]
    reasons, flags, pts = [], [], 50

    p1x2, p_over25 = _prematch(payload)
    p_pre = (p1x2["1"] if fav_home else p1x2["2"]) if p1x2 else fav.get("p_prematch")
    if p_pre is None:
        pts -= 20; flags.append("Sin cuotas pre-partido: no se puede confirmar que sea favorito")
    elif p_pre >= 0.75:
        pts += 15; reasons.append(f"Favoritísimo ({p_pre:.0%} pre-partido)")
    elif p_pre >= 0.65:
        pts += 10; reasons.append(f"Favorito claro ({p_pre:.0%})")
    else:
        pts += 4; reasons.append(f"Favorito ({p_pre:.0%})")

    if minute <= 15:
        pts += 10; reasons.append(f"Gol en contra muy pronto (min {minute}): mucho tiempo para remontar")
    elif minute <= 25:
        pts += 6; reasons.append(f"Gol en contra temprano (min {minute})")
    else:
        pts += 2

    dom = dominance_share(payload.get("stats_at_detection"), fav_home)
    if dom is not None:
        if dom >= 0.60:
            pts += 12; reasons.append(f"Domina pese al marcador (índice {dom:.0%}): gol a contracorriente")
        elif dom >= 0.52:
            pts += 5; reasons.append(f"Algo mejor en el juego (índice {dom:.0%})")
        elif dom < 0.45:
            pts -= 12; flags.append(f"El rival le está superando (índice {dom:.0%}): la ventaja puede ser merecida")
    else:
        pts -= 8; flags.append("Sin estadísticas fiables del partido")

    if fav_home:
        pts += 4; reasons.append("Juega en casa")
    if deficit >= 2:
        pts -= 12; flags.append("Pierde por 2 o más")
    rc = payload.get("reds_count") or {}
    if rc and (rc.get("home" if fav_home else "away", 0) > rc.get("away" if fav_home else "home", 0)):
        pts -= 25; flags.append("El favorito tiene un expulsado")
    if dq.get("detection_delay_min", 0) >= 5:
        flags.append(f"Detectado {dq['detection_delay_min']}' después del gol: cuotas ya ajustadas")
    if payload["competition_id"] in (7, 679):
        flags.append("Competición europea: revisa si es ida/vuelta o si al favorito le vale el empate")

    markets, proj = _markets(payload, fav_home, fav["name"], p1x2, p_over25, 1.0, 1.0)
    return _finish(pts, reasons, flags, markets, proj, dom, p_pre, p1x2)


def evaluate(payload):
    if payload.get("trigger_type", "roja_temprana") == "favorito_pierde":
        return evaluate_fav(payload)
    return evaluate_red(payload)
