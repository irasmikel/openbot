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

from . import apifootball, auditor, config, espn, estratega_llm, mensajero, scout
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
            out.append(f"✅ {html.escape(name)}: {html.escape(str(msg))[:700]} ({time.time() - t:.1f}s)")
        except Exception as e:
            out.append(f"❌ {html.escape(name)}: {html.escape(str(e)[:250])}")

    espn_live = []
    check("ESPN programados hoy", lambda: f"{len(espn.scheduled_today())} partidos de las 7 competiciones")
    check("ESPN en vivo", lambda: f"{len(espn_live.extend(espn.live_events()) or espn_live)} partidos objetivo en juego")
    def espn_fresh():
        now = time.time()
        started = [e for e in espn.scheduled_today() if e.get("start_ts") and e["start_ts"] < now - 15 * 60]
        stale = [e for e in started if e["status_code"] == 0]
        sample = "; ".join(f"{e['home']}-{e['away']} {e['home_score']}-{e['away_score']} cod={e['status_code']} min={e['minute']}"
                           for e in started[:4])
        if started and len(stale) == len(started):
            raise RuntimeError(f"NO actualiza: {len(started)} partidos ya empezados siguen 'programados'. {sample}")
        return f"{len(started) - len(stale)}/{len(started)} empezados con datos en vivo. {sample}"
    check("ESPN ¿actualiza en directo?", espn_fresh)
    if config.FOOTBALL_DATA_TOKEN:
        def fd():
            from .http import get_json
            d = get_json("https://api.football-data.org/v4/matches", headers={"X-Auth-Token": config.FOOTBALL_DATA_TOKEN}) or {}
            ms = d.get("matches", [])
            live = [m for m in ms if m.get("status") in ("IN_PLAY", "PAUSED", "LIVE")]
            keys = sorted(ms[0].keys()) if ms else []
            return (f"{len(ms)} partidos hoy, {len(live)} en juego · campos: {', '.join(keys)[:200]} · "
                    + "; ".join(f"{m['homeTeam'].get('shortName')}-{m['awayTeam'].get('shortName')} {m['status']} "
                                f"{(m.get('score') or {}).get('fullTime')}" for m in (live or ms)[:3]))
        check("football-data.org", fd)
    else:
        out.append("➖ football-data.org: sin token (FOOTBALL_DATA_TOKEN)")
    check("ESPN cuotas en el listado", lambda: f"{len(espn._ODDS_FIRST)}/{len(espn._EVENTS)} partidos con cuota 1X2")
    ids = [e["match_id"] for e in espn_live] or list(espn._EVENTS)
    if ids:
        mid = ids[0]
        check(f"ESPN stats partido {mid}", lambda: json.dumps(espn.statistics(mid)["home"]) + " (vacías si no ha empezado)")
        check("ESPN cuotas (summary)", lambda: json.dumps({k: v["initial"] for k, v in espn.odds(mid)["1x2"].items()}) or "{}")
    if apifootball.enabled():
        def af():
            live = apifootball.live_fixtures_with_reds()
            fx = apifootball.fixtures_for_date()
            return (f"{len(fx)} partidos hoy · {len(live)} en juego "
                    + "; ".join(f"{f['home']}-{f['away']} min {f['minute']} rojas {len(f['reds'])}" for f in live[:3])
                    + f" · quedan {apifootball.remaining()} peticiones")
        check("API-Football", af)
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
            res = " · ".join(f"{m['role']}({m['provider']})={m['rating']}" for m in v["panel"]) + f" → {v['rating']}"
            if v.get("failed"):
                res += " | fallos: " + " / ".join(v["failed"])
            return res
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
