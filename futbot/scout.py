"""AGENTE 1 · Scout. Solo trabaja cuando hay partidos de las 7 competiciones en su ventana
(3' antes del inicio hasta ~45' reales después). Si no hay nada en los próximos 40 minutos,
termina en segundos: un lunes a las 10:00 la ejecución dura ~20 s y no consume nada."""
import logging
import time
import uuid

from . import (apifootball, auditor, bets, config, enricher, espn, estratega_llm, estratega_reglas, mensajero,
               model, sofascore, storage)

PROVIDERS = {"sofascore": sofascore, "espn": espn}
from .http import HttpError

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ pipeline común
def process_trigger(match, trigger, source, af_fixture_id=None, test=False, payload=None):
    t0 = time.time()
    payload = payload or enricher.build_payload(match, trigger, source, af_fixture_id)
    rules = estratega_reglas.evaluate(payload)
    stop_loss = False
    if not test:
        try:
            stop_loss = bets.month_summary()["stop_loss_hit"]
        except Exception:
            log.exception("month_summary")
    stake = 0.0 if stop_loss else rules["stake_suggested"]
    alert_id = "test" if test else uuid.uuid4().hex[:10]
    kb = bets.keyboard(alert_id) if (not test and rules["rating"] != "Descartar") else None
    llm_on = estratega_llm.enabled()
    msg_id = mensajero.send_alert(payload, rules, llm_pending=llm_on, test=test, stake=stake,
                                  stop_loss=stop_loss, reply_markup=kb)
    log.info("Alerta enviada en %.1fs: %s vs %s (%s)", time.time() - t0, payload["home"], payload["away"], rules["rating"])
    if not test:
        storage.append_alert({"id": alert_id, "created_at": storage.now_iso(), "payload": payload,
                              "rules": rules, "llm": None, "final_rating": rules["rating"],
                              "stake_suggested": stake, "telegram_message_id": msg_id, "result": None})
    llm = estratega_llm.review(payload, rules) if llm_on else None
    final = estratega_llm.combine(rules, llm)
    if llm_on:
        if final != rules["rating"]:
            stake = 0.0 if (stop_loss or final == "Descartar") else estratega_reglas.suggest_stake(final, rules["primary_market"])
            kb = bets.keyboard(alert_id) if (not test and final != "Descartar") else None
        mensajero.edit_alert(msg_id, payload, rules, llm, final, test=test, stake=stake,
                             stop_loss=stop_loss, reply_markup=kb)
        if not test:
            storage.update_alert(alert_id, llm=llm, final_rating=final, stake_suggested=stake)
    return payload, rules, llm, final


def process_red(match, red, reds_home, reds_away, source, af_fixture_id=None, test=False):
    trig = {"type": "roja_temprana", "red": red, "reds_home": reds_home, "reds_away": reds_away}
    return process_trigger(match, trig, source, af_fixture_id, test=test, payload=match if test else None)


def _split_reds(reds, upto_minute=None):
    """Cuenta rojas por bando (opcionalmente solo hasta un minuto)."""
    sel = [r for r in reds if upto_minute is None or (r["minute"] or 0) <= upto_minute]
    return sum(r["is_home"] for r in sel), sum(not r["is_home"] for r in sel)


def _handle_new_reds(match, reds, source, af_fixture_id=None):
    for red in sorted(reds, key=lambda r: (r["minute"] or 0, r["added"])):
        if red["minute"] is None or red["minute"] > config.MAX_RED_MINUTE:
            continue
        key = f"{source}:{match.get('match_id') or af_fixture_id}:{red['minute']}:{red['added']}:{int(red['is_home'])}"
        if storage.already_alerted(key):
            continue
        storage.mark_alerted(key)
        rh, ra = _split_reds(reds, red["minute"])
        if rh == ra:
            log.info("Rojas a ambos lados (%s-%s) en %s vs %s: igualdad numérica, sin alerta",
                     rh, ra, match["home"], match["away"])
            continue
        try:
            process_red(match, red, rh, ra, source, af_fixture_id)
        except Exception:
            log.exception("Error procesando roja en %s vs %s", match["home"], match["away"])


# ------------------------------------------------------------------ favorito pierde
_PRE_PROBS = {}  # match_id -> {"1","X","2"} probabilidades pre-partido (1 petición por partido)


def _prematch_probs(match_id, src=sofascore):
    if match_id not in _PRE_PROBS:
        try:
            o = src.odds(match_id)
            _PRE_PROBS[match_id] = model.demargin({k: v["initial"] for k, v in o["1x2"].items()}) \
                if len(o["1x2"]) == 3 else None
        except Exception as e:
            log.warning("odds pre-partido %s: %s", match_id, e)
            _PRE_PROBS[match_id] = None
    return _PRE_PROBS[match_id]


def _check_favorite_losing(ev, src=sofascore, source="sofascore"):
    if not config.FAV_ENABLED or not ev.get("minute") or ev["minute"] > config.FAV_MAX_MINUTE + 3:
        return
    if ev["home_score"] == ev["away_score"]:
        return
    key = f"fav:{ev['match_id']}"
    if storage.already_alerted(key):
        return
    probs = _prematch_probs(ev["match_id"], src)
    if not probs:
        return
    fav_home = probs["1"] >= probs["2"]
    p_fav = probs["1"] if fav_home else probs["2"]
    losing = (ev["home_score"] < ev["away_score"]) if fav_home else (ev["away_score"] < ev["home_score"])
    if p_fav < config.FAV_MIN_PROB or not losing:
        return
    goal_minute = None
    try:
        goals = [g for g in src.goals(ev["match_id"]) if g["is_home"] != fav_home]
        goal_minute = goals[0]["minute"] if goals else None
    except Exception as e:
        log.warning("goles %s: %s", ev["match_id"], e)
    if goal_minute and goal_minute > config.FAV_MAX_MINUTE:
        return
    storage.mark_alerted(key)
    trig = {"type": "favorito_pierde", "fav_is_home": fav_home, "p_fav_pre": round(p_fav, 3),
            "goal_minute": goal_minute}
    try:
        process_trigger(ev, trig, source)
    except Exception:
        log.exception("Error procesando favorito pierde en %s vs %s", ev["home"], ev["away"])


# ------------------------------------------------------------------ ventanas
def _in_window(start_ts, now):
    return start_ts - config.WINDOW_BEFORE_KICKOFF_MIN * 60 <= now <= start_ts + config.WINDOW_AFTER_KICKOFF_MIN * 60


def _next_window_in(schedule, now):
    future = [m["start_ts"] - config.WINDOW_BEFORE_KICKOFF_MIN * 60 for m in schedule
              if m.get("start_ts") and m["start_ts"] - config.WINDOW_BEFORE_KICKOFF_MIN * 60 > now]
    return (min(future) - now) if future else None


def _plan_summary(schedule):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Madrid")
    rows = sorted(schedule, key=lambda m: m["start_ts"] or 0)
    return "\n".join(f"  {datetime.fromtimestamp(m['start_ts'], tz):%H:%M} {m['competition']}: {m['home']} - {m['away']}"
                     for m in rows if m.get("start_ts"))


# ------------------------------------------------------------------ bucle principal
# ------------------------------------------------------------------ parte diario
def daily_summary(src, source):
    """Un mensaje al día (al acabar la vigilancia) para saber que el bot ha trabajado.
    Además se autocomprueba: si hubo una roja antes del 30 sin alerta, lo marca."""
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d")
    marker = config.DATA_DIR / "daily_summary_sent.json"
    try:
        if json.loads(marker.read_text()).get("date") == today:
            return
    except Exception:
        pass
    schedule = src.scheduled_today()
    alerts = [a for a in storage.load_alerts() if a.get("created_at", "")[:10] == datetime.utcnow().strftime("%Y-%m-%d")]
    early, late, missed = [], [], []
    alerted_pairs = {(a["payload"].get("home"), a["payload"].get("away")) for a in alerts}
    for m in schedule:
        for r in src.red_cards(m["match_id"]) if hasattr(src, "red_cards") else []:
            txt = f"{m['home']}–{m['away']} ({r['minute']}')"
            if (r["minute"] or 99) <= config.MAX_RED_MINUTE:
                early.append(txt)
                if (m["home"], m["away"]) not in alerted_pairs:
                    missed.append(txt)
            else:
                late.append(txt)
    fav = sum(1 for a in alerts if a["payload"].get("trigger_type") == "favorito_pierde")
    L = [f"📋 <b>Parte del día</b> · {today}"]
    if not schedule:
        L.append("Hoy no había partidos de las 7 competiciones. Bot en reposo ✅")
    else:
        L.append(f"👀 Partidos vigilados: <b>{len(schedule)}</b>")
        L.append(f"🔔 Alertas enviadas: <b>{len(alerts)}</b>"
                 + (f" (🟥 {len(alerts) - fav} rojas · 📉 {fav} favorito perdiendo)" if alerts else ""))
        if early:
            L.append("🟥 Rojas antes del 30': " + ", ".join(early))
        if late:
            L.append("🟨 Rojas después del 30' (no se avisan): " + ", ".join(late))
        if not early and not late:
            L.append("Sin expulsiones hoy.")
        L.append("⚠️ <b>Rojas tempranas SIN alerta:</b> " + ", ".join(missed) + " — revisa el registro de Actions"
                 if missed else "✅ Todo en orden")
    try:
        from . import bets
        if alerts or bets.load():
            L += ["", bets.progress_text()]
    except Exception:
        log.exception("progreso")
    mensajero.send_text("\n".join(L))
    marker.write_text(json.dumps({"date": today}))


def _watch(src, source, deadline):
    schedule = src.scheduled_today()
    schedule_ts = time.time()
    log.info("[%s] Partidos hoy en las 7 competiciones: %d\n%s", source, len(schedule), _plan_summary(schedule))
    last_sweep = 0.0
    while time.time() < deadline:
        now = time.time()
        if now - schedule_ts > 3600:  # refresca aplazamientos/cambios
            schedule, schedule_ts = src.scheduled_today() or schedule, now
        active_ids = {m["match_id"] for m in schedule if m.get("start_ts") and _in_window(m["start_ts"], now)}
        if not active_ids:
            wait = _next_window_in(schedule, now)
            if wait is None or wait > config.LOOKAHEAD_MIN * 60:
                log.info("No quedan partidos hoy en las 7 competiciones. Fin.")
                try:
                    daily_summary(src, source)
                except Exception:
                    log.exception("parte diario")
                return False
            if now + wait > deadline:
                log.info("La próxima ventana empieza después del límite de esta ejecución: relevo.")
                return True
            log.info("Próxima ventana en %.0f min; esperando…", wait / 60)
            time.sleep(min(wait, 300))
            bets.sync()
            continue

        live = [e for e in src.live_events() if e["match_id"] in active_ids]
        sweep_due = now - last_sweep >= config.INCIDENT_SWEEP_SECONDS
        if sweep_due:  # latido cada ~3 min para ver en el registro qué está vigilando
            log.info("Vigilando %d partido(s): %s", len(live), "; ".join(
                f"{e['home']} {e['home_score']}-{e['away_score']} {e['away']} ({e['minute']}', "
                f"rojas {e.get('home_reds') or 0}-{e.get('away_reds') or 0})" for e in live) or "esperando el inicio")
        for ev in live:
            if ev["status_code"] != src.FIRST_HALF and not (ev["minute"] and ev["minute"] <= 45):
                continue
            has_red_flag = bool(ev.get("home_reds") or ev.get("away_reds"))
            # pocos partidos -> incidencias en cada vuelta; muchos -> solo si hay señal o toca barrido
            if has_red_flag or sweep_due or len(live) <= 4:
                reds = src.red_cards(ev["match_id"])
                if reds:
                    _handle_new_reds(ev, reds, source)
            _check_favorite_losing(ev, src, source)
        if sweep_due:
            last_sweep = now
            bets.sync()
        time.sleep(config.POLL_SECONDS)
    log.info("Tiempo máximo de ejecución alcanzado: relevo.")
    return True


def _watch_sofascore(deadline):
    return _watch(sofascore, "sofascore", deadline)


# ------------------------------------------------------------------ modo respaldo API-Football
def _watch_apifootball(deadline):
    """Respaldo degradado: 1 petición cubre las 7 competiciones, pero el plan Free tiene 100/día,
    así que se consulta cada 2-3 minutos y se reserva presupuesto para el enriquecimiento."""
    log.warning("MODO RESPALDO API-Football (SofaScore no disponible)")
    fixtures = apifootball.fixtures_for_date()
    if not fixtures:
        log.info("Sin partidos hoy (o sin clave API-Football). Fin.")
        return
    while time.time() < deadline and apifootball.remaining() > 10:
        live = apifootball.live_fixtures_with_reds()
        in_window = [f for f in live if f["minute"] is not None and f["minute"] <= 40]
        for f in in_window:
            if f["reds"]:
                match = {"match_id": None, "competition_id": next(
                    (k for k, v in config.COMPETITIONS.items() if v[1] == f["league_id"]), None),
                    "home": f["home"], "away": f["away"], "home_score": f["home_score"],
                    "away_score": f["away_score"], "minute": f["minute"]}
                match["competition"] = config.COMPETITIONS.get(match["competition_id"], ("?",))[0]
                _handle_new_reds(match, f["reds"], "apifootball", af_fixture_id=f["fixture_id"])
        if not in_window:
            log.info("Ningún partido en vivo en su primera media hora. Fin (la próxima ejecución reintenta SofaScore).")
            return
        time.sleep(150)


def housekeeping():
    """Al arrancar cada ejecución: botones de Telegram pendientes y liquidación de resultados."""
    for fn in (bets.sync, auditor.settle, bets.settle_bets):
        try:
            fn()
        except Exception:
            log.exception("housekeeping %s", fn.__name__)


def _relaunch():
    """Lanza una nueva ejecución de este workflow (GitHub permite workflow_dispatch con GITHUB_TOKEN)."""
    import os
    from .http import post_json
    repo, token = os.getenv("GITHUB_REPOSITORY"), os.getenv("GITHUB_TOKEN")
    if not (repo and token):
        log.info("Sin GITHUB_TOKEN: no puedo relanzarme (¿ejecución local?)")
        return
    url = f"https://api.github.com/repos/{repo}/actions/workflows/scout.yml/dispatches"
    post_json(url, {"ref": "main", "inputs": {"mode": "watch"}},
              headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
              label="relanzar workflow")
    log.info("Relevo lanzado.")


def watch():
    """Vigila todo el día. Si llega al límite de ~5h40 y quedan partidos, se relanza a sí mismo."""
    deadline = time.time() + config.MAX_RUNTIME_MIN * 60
    housekeeping()
    if config.DATA_SOURCE == "apifootball":
        _watch_apifootball(deadline)
        return
    try:
        cont = _watch(espn, "espn", deadline)
    except HttpError as e:
        log.error("ESPN falló (%s). Paso a API-Football.", e.status)
        _watch_apifootball(deadline)
        cont = False
    if cont:
        try:
            _relaunch()
        except Exception:
            log.exception("No se pudo relanzar")
