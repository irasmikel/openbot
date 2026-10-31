"""Modelo matemático (Poisson) para estimar probabilidades tras la expulsión.

1. A partir de las cuotas pre-partido (sin margen) se estiman los goles esperados de cada equipo.
2. Se proyecta el tiempo restante aplicando un multiplicador al equipo con 11 y otro al de 10.
3. Se suman los goles actuales y se calculan 1X2, doble oportunidad y Más/Menos goles.
4. Cuota justa = 1/p. Cuota MÍNIMA para apostar = cuota justa × VALUE_MARGIN.

Los multiplicadores son hipótesis iniciales: el Auditor debe calibrarlos con datos reales."""
from functools import lru_cache
from math import exp, factorial

from . import config

MAXG = 10


@lru_cache(maxsize=None)
def _pois(k, lam):
    return exp(-lam) * lam ** k / factorial(k)


def demargin(odds):
    """{'1':2.0,'X':3.4,'2':3.8} -> probabilidades que suman 1 (None si faltan)."""
    if not odds or any(not odds.get(k) or odds[k] <= 1 for k in odds):
        return None
    inv = {k: 1 / v for k, v in odds.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def _result_probs(lh, la, base_h=0, base_a=0):
    ph = [_pois(i, round(lh, 4)) for i in range(MAXG + 1)]
    pa = [_pois(j, round(la, 4)) for j in range(MAXG + 1)]
    win = draw = loss = 0.0
    totals = {}
    for i, p_i in enumerate(ph):
        for j, p_j in enumerate(pa):
            p = p_i * p_j
            h, a = base_h + i, base_a + j
            if h > a:
                win += p
            elif h == a:
                draw += p
            else:
                loss += p
            totals[h + a] = totals.get(h + a, 0) + p
    return win, draw, loss, totals


def fit_lambdas(p1x2, p_over25=None):
    """Busca (λ_local, λ_visitante) que reproducen las probabilidades pre-partido."""
    if not p1x2:
        return config.DEFAULT_LAMBDA_HOME, config.DEFAULT_LAMBDA_AWAY, False
    best, err_best = None, 9e9
    for i in range(4, 71):          # 0.20 .. 3.50
        lh = i * 0.05
        for j in range(4, 71):
            la = j * 0.05
            w, d, l, tot = _result_probs(lh, la)
            err = (w - p1x2["1"]) ** 2 + (d - p1x2["X"]) ** 2 + (l - p1x2["2"]) ** 2
            if p_over25 is not None:
                over = sum(p for g, p in tot.items() if g >= 3)
                err += (over - p_over25) ** 2
            if err < err_best:
                best, err_best = (lh, la), err
    return best[0], best[1], True


def project(lh, la, minute, home_goals, away_goals, eleven_is_home,
            mult_eleven=None, mult_ten=None):
    mult_eleven = mult_eleven or config.RED_MULT_ELEVEN
    mult_ten = mult_ten or config.RED_MULT_TEN
    rem = max(0.0, (93 - minute) / 90)   # ~3' de descuento total
    rh = lh * rem * (mult_eleven if eleven_is_home else mult_ten)
    ra = la * rem * (mult_ten if eleven_is_home else mult_eleven)
    w, d, l, totals = _result_probs(rh, ra, home_goals, away_goals)
    current_total = home_goals + away_goals
    over = {}
    for line in (0.5, 1.5, 2.5, 3.5):
        if line < current_total:
            continue  # ya ganada, no interesa
        over[str(line)] = sum(p for g, p in totals.items() if g > line)
    # probabilidad de que el equipo con 11 marque al menos un gol más
    lam_eleven = rh if eleven_is_home else ra
    eleven_scores = 1 - exp(-lam_eleven)
    if eleven_is_home:
        dc = {"label": "1X", "p": w + d}
        win = {"label": "1", "p": w}
    else:
        dc = {"label": "X2", "p": l + d}
        win = {"label": "2", "p": l}
    return {
        "lambda_remaining": {"home": round(rh, 3), "away": round(ra, 3)},
        "p_home": round(w, 4), "p_draw": round(d, 4), "p_away": round(l, 4),
        "double_chance": {"label": dc["label"], "p": round(dc["p"], 4)},
        "eleven_win": {"label": win["label"], "p": round(win["p"], 4)},
        "eleven_scores_again": round(eleven_scores, 4),
        "over": {k: round(v, 4) for k, v in over.items()},
    }


def fair_and_min(p):
    if not p or p <= 0:
        return None, None
    fair = 1 / p
    return round(fair, 2), round(fair * config.VALUE_MARGIN, 2)
