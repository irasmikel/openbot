"""AGENTE 4 · Auditor. Cierra las alertas con el resultado final y calcula, en MODO PAPEL,
qué habría pasado apostando 1 € al mercado principal SOLO cuando la cuota de referencia
superaba la cuota mínima. Genera data/informe_semanal.md y lo envía a Telegram."""
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import apifootball, config, mensajero, sofascore, storage

log = logging.getLogger(__name__)
FINISHED = {100, 110, 120}  # terminado / tras prórroga / tras penaltis


def _final_score(alert):
    p = alert["payload"]
    if p.get("match_id"):
        try:
            ev = sofascore.event_detail(p["match_id"])
            if ev and ev["status_code"] in FINISHED:
                return ev["home_score"], ev["away_score"]
            return None
        except Exception as e:
            log.warning("SofaScore resultado: %s", e)
    if p.get("af_fixture_id") and apifootball.enabled():
        resp = apifootball._call("fixtures", {"id": p["af_fixture_id"]}) or []
        if resp and resp[0]["fixture"]["status"]["short"] in ("FT", "AET", "PEN"):
            # resultado a los 90' (lo que liquidan las casas)
            ft = resp[0]["score"]["fulltime"]
            return ft["home"], ft["away"]
    return None


def market_won(market, hs, as_):
    m = market.lower()
    if m.startswith("doble oportunidad"):
        lab = market.split()[-1]
        return {"1X": hs >= as_, "X2": as_ >= hs, "12": hs != as_}[lab]
    if m.startswith("gana"):
        lab = re.search(r"\((1|2)\)", market).group(1)
        return hs > as_ if lab == "1" else as_ > hs
    if m.startswith("más de"):
        line = float(re.search(r"más de ([\d.]+)", m).group(1))
        return hs + as_ > line
    raise ValueError(market)


def settle():
    alerts = storage.load_alerts()
    changed = 0
    for a in alerts:
        if a.get("result") or a.get("test"):
            continue
        created = datetime.fromisoformat(a["created_at"])
        if datetime.now(timezone.utc) - created < timedelta(hours=2, minutes=30):
            continue
        score = _final_score(a)
        if not score:
            continue
        hs, as_ = score
        pm = a["rules"].get("primary_market")
        res = {"final": f"{hs}-{as_}", "market": pm["market"] if pm else None}
        if pm:
            won = market_won(pm["market"], hs, as_)
            res["won"] = won
            live = pm.get("live_odds_ref")
            res["paper_bet"] = bool(live and live >= pm["min_odds"] and a["final_rating"] != "Descartar")
            res["paper_odds"] = live
            res["paper_profit"] = (round(live - 1, 2) if won else -1.0) if res["paper_bet"] else 0.0
            res["brier"] = round((pm["p_model"] - (1 if won else 0)) ** 2, 4)
        a["result"] = res
        changed += 1
    if changed:
        storage.save_alerts(alerts)
    return alerts


def report(days=7):
    alerts = settle()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rec = [a for a in alerts if datetime.fromisoformat(a["created_at"]) >= since]
    allc = [a for a in alerts if a.get("result")]

    def block(items, title):
        by = defaultdict(lambda: {"n": 0, "settled": 0, "hits": 0, "bets": 0, "profit": 0.0, "brier": []})
        for a in items:
            r = by[a["final_rating"]]
            r["n"] += 1
            res = a.get("result")
            if res and res.get("market"):
                r["settled"] += 1
                r["hits"] += int(res["won"])
                r["brier"].append(res["brier"])
                if res["paper_bet"]:
                    r["bets"] += 1
                    r["profit"] += res["paper_profit"]
        lines = [f"<b>{title}</b>"]
        tot_profit, tot_bets = 0.0, 0
        for rating in ("Alta", "Media", "Descartar"):
            r = by.get(rating)
            if not r:
                continue
            hit = f"{r['hits'] / r['settled']:.0%}" if r["settled"] else "–"
            br = f"{sum(r['brier']) / len(r['brier']):.3f}" if r["brier"] else "–"
            lines.append(f"{mensajero.RATING_ICON[rating]} {rating}: {r['n']} alertas · acierto mercado {hit} · "
                         f"apuestas papel {r['bets']} · {r['profit']:+.2f} € · Brier {br}")
            tot_profit += r["profit"]; tot_bets += r["bets"]
        roi = f"{tot_profit / tot_bets:+.0%}" if tot_bets else "–"
        lines.append(f"💰 Total papel: {tot_profit:+.2f} € en {tot_bets} apuestas (ROI {roi})")
        return lines

    L = [f"📒 <b>Informe del Auditor</b> · {datetime.now(timezone.utc):%d/%m/%Y}", ""]
    L += block(rec, f"Últimos {days} días") + [""] + block(allc, "Histórico (cerradas)")
    L += ["", "<i>Brier: error de calibración del modelo (0 = perfecto, 0.25 = moneda al aire).</i>",
          f"<i>Multiplicadores actuales: 11 → ×{config.RED_MULT_ELEVEN} · 10 → ×{config.RED_MULT_TEN}</i>"]
    try:
        from . import bets
        bets.settle_bets()
        L += ["", "💶 <b>Dinero real</b>", bets.progress_text()]
    except Exception:
        log.exception("resumen dinero real")
    if len(allc) < 30:
        L.append(f"<i>Solo {len(allc)} alertas cerradas: aún no es una muestra fiable (mínimo ~30-50).</i>")
    text = "\n".join(L)

    md = re.sub(r"</?b>|</?i>", "", text)
    detail = ["", "## Detalle", "| Fecha | Partido | Min | Veredicto | Mercado | p | Mín | Ref | Final | ¿Ganó? | Papel € |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in allc[-60:]:
        p, r, pm = a["payload"], a["result"], a["rules"].get("primary_market") or {}
        detail.append(f"| {a['created_at'][:10]} | {p['home']} - {p['away']} | {p['red_card']['minute']} | "
                      f"{a['final_rating']} | {r.get('market')} | {pm.get('p_model')} | {pm.get('min_odds')} | "
                      f"{r.get('paper_odds')} | {r['final']} | {r.get('won')} | {r.get('paper_profit')} |")
    (config.DATA_DIR / "informe_semanal.md").write_text(md + "\n" + "\n".join(detail) + "\n", encoding="utf-8")
    mensajero.send_text(text)
    return text
