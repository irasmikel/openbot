"""AGENTE 3 · Mensajero (Telegram). Envía la alerta en cuanto el Estratega de reglas termina
y después EDITA el mismo mensaje para añadir el análisis del LLM."""
import html
import logging

from . import config
from .http import post_json

log = logging.getLogger(__name__)
RATING_ICON = {"Alta": "🟢", "Media": "🟡", "Descartar": "🔴"}


def _api(method, payload):
    if config.DRY_RUN or not config.TELEGRAM_BOT_TOKEN:
        print(f"\n----- [DRY RUN] Telegram {method} -----\n{payload.get('text', payload)}\n")
        return {"result": {"message_id": 0}}
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    return post_json(url, payload, timeout=15, label=f"Telegram {method}")


def send_text(text):
    return _api("sendMessage", {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True})


def _e(x):
    return html.escape(str(x)) if x is not None else "–"


def _fmt_num(v, pct=False):
    if v is None:
        return "–"
    if pct:
        return f"{v:.0f}%"
    return f"{v:.2f}" if isinstance(v, float) and not v.is_integer() else f"{int(v)}"


def _odds_line(odds):
    if not odds or not odds.get("1x2"):
        return "no disponibles"
    o = odds["1x2"]
    return f"1 <b>{_e(o.get('1'))}</b> · X <b>{_e(o.get('X'))}</b> · 2 <b>{_e(o.get('2'))}</b>"


def _header(payload):
    flag = config.COMP_FLAGS.get(payload.get("competition_id"), "⚽")
    score = (f"<b>{_e(payload['home'])} {payload['score']['home']} - {payload['score']['away']} {_e(payload['away'])}</b>"
             f"  <i>(ahora min {_e(payload.get('minute_now'))})</i>")
    if payload.get("trigger_type") == "favorito_pierde":
        fav = payload["favorite"]
        p = fav.get("p_prematch")
        return [
            f"📉 <b>FAVORITO PERDIENDO · min {_e(payload.get('event_minute'))}'</b>   {flag} {_e(payload['competition'])}",
            score,
            f"⭐ Favorito: <b>{_e(fav['name'])}</b> ({'casa' if fav['is_home'] else 'fuera'})"
            + (f" · {p:.0%} de ganar pre-partido" if p else ""),
        ]
    rc, adv = payload["red_card"], payload["team_with_advantage"]
    added = f"+{rc['added']}" if rc.get("added") else ""
    return [
        f"🟥 <b>ROJA TEMPRANA · min {rc['minute']}{added}'</b>   {flag} {_e(payload['competition'])}",
        score,
        f"❌ {_e(rc.get('player'))} ({_e(rc['sent_off_team'])}) · {rc['card_type'].replace('_', ' ')}",
        f"💪 Con superioridad: <b>{_e(adv['name'])}</b> ({'casa' if adv['is_home'] else 'fuera'})"
        + (f" · <b>+{adv['player_diff']}</b> jugadores" if adv["player_diff"] > 1 else ""),
    ]


def format_alert(payload, rules, llm=None, final_rating=None, llm_pending=False, test=False,
                 stake=None, stop_loss=False):
    st = payload.get("stats_at_detection") or {"home": {}, "away": {}}
    h, a = st.get("home", {}), st.get("away", {})
    rating = final_rating or rules["rating"]

    L = ["🧪 <b>PRUEBA — datos simulados</b>"] if test else []
    L += _header(payload)
    L += [
        "",
        "📊 <b>Estadísticas al detectar</b> <i>(local – visitante)</i>",
        f"Posesión {_fmt_num(h.get('possession'), True)} – {_fmt_num(a.get('possession'), True)}",
        f"Tiros (a puerta) {_fmt_num(h.get('shots_total'))} ({_fmt_num(h.get('shots_on_target'))}) – "
        f"{_fmt_num(a.get('shots_total'))} ({_fmt_num(a.get('shots_on_target'))})",
        f"xG {_fmt_num(h.get('xg'))} – {_fmt_num(a.get('xg'))} · Ocasiones claras {_fmt_num(h.get('big_chances'))} – {_fmt_num(a.get('big_chances'))}",
        f"Córners {_fmt_num(h.get('corners'))} – {_fmt_num(a.get('corners'))}",
    ]
    if rules.get("dominance_index") is not None:
        L.append(f"Índice de dominio de {_e(payload['target_team']['name'])}: <b>{rules['dominance_index']:.0%}</b>")
    pre = payload.get("odds_prematch")
    L += ["", f"💶 <b>Pre-partido</b> ({_e((pre or {}).get('source', '—'))}): {_odds_line(pre)}"]
    if payload.get("odds_live"):
        L.append(f"📈 Ahora (ref. {_e(payload['odds_live']['source'])}): {_odds_line(payload['odds_live'])}")

    L += ["", f"🧠 <b>Veredicto: {RATING_ICON[rating]} {rating.upper()}</b>  ({rules['score_0_100']}/100 reglas)"]
    pm = rules.get("primary_market")
    if pm and rating != "Descartar":
        L.append(f"🎯 <b>{_e(pm['market'])}</b> · prob. modelo {pm['p_model']:.0%} · cuota justa {pm['fair_odds']}")
        L.append(f"👉 <b>Solo si Bwin paga ≥ {pm['min_odds']}</b>"
                 + (f"  (ref. actual {pm['live_odds_ref']})" if pm.get("live_odds_ref") else ""))
        for alt in rules["markets"][1:3]:
            L.append(f"   ↳ alt.: {_e(alt['market'])} ≥ {alt['min_odds']} ({alt['p_model']:.0%})")
        if stake is not None:
            L.append(f"💰 Stake sugerido: <b>{stake:g} €</b>".replace(".", ",") if stake else "💰 Stake sugerido: 0 €")
    for r in rules["reasons"][:3]:
        L.append(f"✅ {_e(r)}")
    for f in rules["red_flags"][:3]:
        L.append(f"⚠️ {_e(f)}")

    if llm:
        L += ["", f"🤖 <b>IA: {RATING_ICON.get(llm['rating'], '')} {_e(llm['rating'])}</b> "
                  f"(confianza {_e(llm.get('confidence'))}%)",
              f"<i>{_e(llm.get('summary'))}</i>"]
        if llm.get("market") and llm["rating"] != "Descartar":
            L.append(f"🎯 IA: {_e(llm['market'])}" + (f" ≥ {_e(llm.get('min_odds'))}" if llm.get("min_odds") else ""))
        for f in (llm.get("red_flags") or [])[:2]:
            L.append(f"⚠️ {_e(f)}")
    elif llm_pending:
        L += ["", "🤖 <i>Analizando con IA…</i>"]
    if stop_loss:
        L.append(f"\n⛔ <b>Stop-loss del mes alcanzado</b> (−{config.STOP_LOSS_MONTH:g} €): solo informativo.")
    L.append("\n<i>Si Bwin no llega a la cuota mínima, no se entra. Pulsa un botón si apuestas.</i>")
    return "\n".join(L)


def send_alert(payload, rules, llm_pending=False, test=False, stake=None, stop_loss=False, reply_markup=None):
    text = format_alert(payload, rules, llm_pending=llm_pending, test=test, stake=stake, stop_loss=stop_loss)
    body = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    res = _api("sendMessage", body)
    return (res.get("result") or {}).get("message_id")


def edit_alert(message_id, payload, rules, llm, final_rating, test=False, stake=None, stop_loss=False, reply_markup=None):
    text = format_alert(payload, rules, llm=llm, final_rating=final_rating, test=test, stake=stake, stop_loss=stop_loss)
    if not message_id:
        return send_text(text)
    body = {"chat_id": config.TELEGRAM_CHAT_ID, "message_id": message_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup:
        body["reply_markup"] = reply_markup
    try:
        return _api("editMessageText", body)
    except Exception as e:
        log.warning("No se pudo editar, envío nuevo: %s", e)
        return send_text(text)
