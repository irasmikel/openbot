"""Uso:
  python -m futbot watch        # Scout: vigila si hay partidos en ventana (lo lanza GitHub Actions)
  python -m futbot test-alert   # alerta simulada completa (reglas + IA + Telegram)
  python -m futbot diagnose     # comprueba SofaScore, API-Football, Telegram y el LLM
  python -m futbot audit        # informe del Auditor (lo lanza GitHub Actions los lunes)
"""
import html
import json
import logging
import sys
import time

from . import apifootball, auditor, config, espn, estratega_llm, mensajero, scout, sofascore
from .http import BACKEND

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("futbot")


def sample_payload():
    return json.loads((config.ROOT / "tests" / "sample_payload.json").read_text(encoding="utf-8"))


def test_alert():
    p = sample_payload()
    _, rules, llm, final = scout.process_red(p, p["red_card"], 0, 1, "test", test=True)
    print(json.dumps({"rules": {k: rules[k] for k in ("rating", "score_0_100", "primary_market", "reasons", "red_flags")},
                      "llm": llm, "final": final}, ensure_ascii=False, indent=2))


def diagnose():
    out = [f"🩺 <b>Diagnóstico futbot</b> (HTTP: {BACKEND})"]

    def check(name, fn):
        t = time.time()
        try:
            msg = fn()
            out.append(f"✅ {html.escape(name)}: {html.escape(str(msg))[:250]} ({time.time() - t:.1f}s)")
        except Exception as e:
            out.append(f"❌ {html.escape(name)}: {html.escape(str(e)[:250])}")

    check("SofaScore programados hoy", lambda: f"{len(sofascore.scheduled_today())} partidos de las 7 competiciones")
    live_all = []
    check("SofaScore en vivo", lambda: f"{len(live_all.extend(sofascore.live_events()) or live_all)} partidos objetivo en juego")
    if live_all:
        ev = live_all[0]
        check(f"Stats {ev['home']}-{ev['away']}", lambda: json.dumps(sofascore.statistics(ev["match_id"])["home"]))
        check("Odds SofaScore", lambda: json.dumps(sofascore.odds(ev["match_id"])["1x2"]))
        check("Incidencias", lambda: f"{len(sofascore.red_cards(ev['match_id']))} rojas")
    espn_live = []
    check("ESPN programados hoy", lambda: f"{len(espn.scheduled_today())} partidos de las 7 competiciones")
    check("ESPN en vivo", lambda: f"{len(espn_live.extend(espn.live_events()) or espn_live)} partidos objetivo en juego")
    check("ESPN cuotas en el listado", lambda: f"{len(espn._ODDS_FIRST)}/{len(espn._EVENTS)} partidos con cuota 1X2")
    ids = [e["match_id"] for e in espn_live] or list(espn._EVENTS)
    if ids:
        mid = ids[0]
        check(f"ESPN stats partido {mid}", lambda: json.dumps(espn.statistics(mid)["home"]) + " (vacías si no ha empezado)")
        check("ESPN cuotas (summary)", lambda: json.dumps({k: v["initial"] for k, v in espn.odds(mid)["1x2"].items()}) or "{}")
    if apifootball.enabled():
        check("API-Football", lambda: f"{len(apifootball.fixtures_for_date())} partidos hoy · quedan {apifootball.remaining()} peticiones de presupuesto")
    else:
        out.append("➖ API-Football: sin clave (opcional)")
    pings = estratega_llm.ping_all()
    for ok, prov, msg in pings:
        out.append(f"{'✅' if ok else '❌'} IA {html.escape(prov)}: {html.escape(msg)}")
    if not pings:
        out.append("➖ IA: ninguna clave configurada (el bot funciona solo con reglas)")
    elif any(ok for ok, _, _ in pings):
        def panel():
            p = sample_payload()
            from . import estratega_reglas
            v = estratega_llm.review(p, estratega_reglas.evaluate(p))
            if not v:
                raise RuntimeError(estratega_llm.LAST_ERROR or "sin respuesta")
            return " · ".join(f"{m['role']}={m['rating']}" for m in v["panel"]) + f" → {v['rating']}"
        check("Panel IA (alerta de prueba)", panel)
    text = "\n".join(out)
    print(text)
    try:
        mensajero.send_text(text)
        print("✅ Telegram: enviado")
    except Exception as e:
        print(f"❌ Telegram: {e}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "watch"
    {"watch": scout.watch, "test-alert": test_alert, "diagnose": diagnose,
     "audit": auditor.report}.get(cmd, lambda: print(__doc__))()


if __name__ == "__main__":
    main()
