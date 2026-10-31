"""Uso:
  python -m futbot watch        # Scout: vigila si hay partidos en ventana (lo lanza GitHub Actions)
  python -m futbot test-alert   # alerta simulada completa (reglas + IA + Telegram)
  python -m futbot diagnose     # comprueba SofaScore, API-Football, Telegram y el LLM
  python -m futbot audit        # informe del Auditor (lo lanza GitHub Actions los lunes)
"""
import json
import logging
import sys
import time

from . import apifootball, auditor, config, estratega_llm, mensajero, scout, sofascore
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
            out.append(f"✅ {name}: {msg} ({time.time() - t:.1f}s)")
        except Exception as e:
            out.append(f"❌ {name}: {str(e)[:150]}")

    check("SofaScore programados hoy", lambda: f"{len(sofascore.scheduled_today())} partidos de las 7 competiciones")
    live_all = []
    check("SofaScore en vivo", lambda: f"{len(live_all.extend(sofascore.live_events()) or live_all)} partidos objetivo en juego")
    if live_all:
        ev = live_all[0]
        check(f"Stats {ev['home']}-{ev['away']}", lambda: json.dumps(sofascore.statistics(ev["match_id"])["home"]))
        check("Odds SofaScore", lambda: json.dumps(sofascore.odds(ev["match_id"])["1x2"]))
        check("Incidencias", lambda: f"{len(sofascore.red_cards(ev['match_id']))} rojas")
    if apifootball.enabled():
        check("API-Football", lambda: f"{len(apifootball.fixtures_for_date())} partidos hoy · quedan {apifootball.remaining()} peticiones de presupuesto")
    else:
        out.append("➖ API-Football: sin clave (opcional)")
    if estratega_llm.enabled():
        def llm_ping():
            p = sample_payload()
            from . import estratega_reglas
            v = estratega_llm.review(p, estratega_reglas.evaluate(p))
            if not v:
                raise RuntimeError("sin respuesta válida (mira el log)")
            return f"{config.LLM_PROVIDER} → {v['rating']}"
        check("LLM", llm_ping)
    else:
        out.append(f"➖ LLM ({config.LLM_PROVIDER}): sin clave, solo reglas")
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
