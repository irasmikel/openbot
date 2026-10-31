"""Registro de apuestas REALES desde Telegram.

- Cada alerta lleva botones: ✅ 0,5 € · ✅ 1 € · ✅ 2 € · ❌ No apuesto
- Si respondes al mensaje de la alerta con un número (ej. 1.85), se guarda como la cuota que cogiste.
  Si no lo haces, se usa la cuota mínima recomendada (estimación prudente).
- Comando /estado → progreso del mes hacia el objetivo.

GitHub Actions no está siempre encendido: los botones se procesan en la siguiente ejecución
(cada 20' en horario de partidos). Telegram guarda las pulsaciones 24 h."""
import json
import logging
import re
from datetime import datetime, timezone

from . import auditor, config, mensajero, storage

log = logging.getLogger(__name__)
BETS = config.DATA_DIR / "real_bets.jsonl"
OFFSET = config.DATA_DIR / "tg_offset.json"


def load():
    if not BETS.exists():
        return []
    return [json.loads(l) for l in BETS.read_text(encoding="utf-8").splitlines() if l.strip()]


def save(bets):
    BETS.write_text("".join(json.dumps(b, ensure_ascii=False) + "\n" for b in bets), encoding="utf-8")


def keyboard(alert_id):
    return {"inline_keyboard": [
        [{"text": f"✅ {s:g} €".replace(".", ","), "callback_data": f"b:{alert_id}:{s:g}"} for s in (0.5, 1, 2)],
        [{"text": "❌ No apuesto", "callback_data": f"b:{alert_id}:0"}],
    ]}


# ------------------------------------------------------------------ cálculo
def settle_bets():
    """Liquida apuestas reales cuyas alertas ya tienen resultado."""
    alerts = {a["id"]: a for a in storage.load_alerts()}
    bets, changed = load(), False
    for b in bets:
        if b.get("profit") is not None or b["stake"] <= 0:
            continue
        a = alerts.get(b["alert_id"])
        if not a or not a.get("result") or not a["result"].get("final"):
            continue
        hs, as_ = (int(x) for x in a["result"]["final"].split("-"))
        won = auditor.market_won(b["market"], hs, as_)
        b["won"], b["profit"] = won, round(b["stake"] * (b["odds"] - 1), 2) if won else -b["stake"]
        changed = True
    if changed:
        save(bets)
    return bets


def month_summary(bets=None):
    bets = bets if bets is not None else load()
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    mb = [b for b in bets if b["created_at"].startswith(ym) and b["stake"] > 0]
    closed = [b for b in mb if b.get("profit") is not None]
    profit = round(sum(b["profit"] for b in closed), 2)
    return {"bets": len(mb), "closed": len(closed), "pending": len(mb) - len(closed),
            "hits": sum(1 for b in closed if b["won"]), "staked": round(sum(b["stake"] for b in closed), 2),
            "profit": profit, "target": config.MONTHLY_TARGET,
            "stop_loss_hit": profit <= -config.STOP_LOSS_MONTH}


def progress_text(s=None):
    s = s or month_summary()
    pct = max(0.0, min(1.0, s["profit"] / s["target"])) if s["target"] else 0
    bar = "█" * int(pct * 10) + "░" * (10 - int(pct * 10))
    roi = f"{s['profit'] / s['staked']:+.0%}" if s["staked"] else "–"
    t = (f"🎯 <b>Objetivo del mes: {s['profit']:+.2f} € / {s['target']:.0f} €</b>\n{bar} {pct:.0%}\n"
         f"Apuestas: {s['bets']} ({s['pending']} pendientes) · aciertos {s['hits']}/{s['closed']} · "
         f"apostado {s['staked']:.2f} € · ROI {roi}")
    if s["stop_loss_hit"]:
        t += f"\n⛔ <b>Stop-loss alcanzado (−{config.STOP_LOSS_MONTH:.0f} €)</b>: las alertas seguirán llegando con stake 0 hasta el mes que viene."
    return t


# ------------------------------------------------------------------ Telegram
def _offset():
    try:
        return json.loads(OFFSET.read_text())["offset"]
    except Exception:
        return 0


def sync():
    """Procesa botones, respuestas con cuota y /estado. Seguro de llamar a menudo."""
    if config.DRY_RUN or not config.TELEGRAM_BOT_TOKEN:
        return 0
    try:
        upd = mensajero._api("getUpdates", {"offset": _offset(), "timeout": 0,
                                            "allowed_updates": ["callback_query", "message"]})
    except Exception as e:
        log.warning("getUpdates: %s", e)
        return 0
    results = upd.get("result", [])
    if not results:
        return 0
    alerts = {a["id"]: a for a in storage.load_alerts()}
    by_msg = {a.get("telegram_message_id"): a for a in alerts.values() if a.get("telegram_message_id")}
    bets = load()
    for u in results:
        try:
            if "callback_query" in u:
                _on_button(u["callback_query"], alerts, bets)
            elif "message" in u:
                _on_message(u["message"], by_msg, bets)
        except Exception:
            log.exception("update %s", u.get("update_id"))
    save(bets)
    OFFSET.write_text(json.dumps({"offset": results[-1]["update_id"] + 1}))
    return len(results)


def _on_button(cq, alerts, bets):
    if str(cq.get("message", {}).get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
        return
    _, alert_id, stake = cq["data"].split(":")
    stake = float(stake)
    a = alerts.get(alert_id)
    try:
        mensajero._api("answerCallbackQuery", {"callback_query_id": cq["id"], "text": "Registrado"})
    except Exception:
        pass  # caduca si se procesa tarde; no importa
    if not a:
        return
    pm = a["rules"].get("primary_market") or {}
    existing = next((b for b in bets if b["alert_id"] == alert_id), None)
    rec = existing or {"alert_id": alert_id, "created_at": storage.now_iso(),
                       "match": f"{a['payload']['home']} - {a['payload']['away']}",
                       "market": pm.get("market"), "odds": pm.get("min_odds"), "odds_source": "estimada (cuota mínima)"}
    rec["stake"] = stake
    if not existing:
        bets.append(rec)
    label = f"✅ Apostado {stake:g} € a {rec['odds']} — responde a este mensaje con la cuota real" if stake else "❌ No apostada"
    mensajero._api("editMessageReplyMarkup", {"chat_id": config.TELEGRAM_CHAT_ID,
                                              "message_id": a["telegram_message_id"],
                                              "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]}})


def _on_message(msg, by_msg, bets):
    if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
        return
    text = (msg.get("text") or "").strip()
    if text.startswith("/estado"):
        settle_bets()
        mensajero.send_text(progress_text())
        return
    reply = (msg.get("reply_to_message") or {}).get("message_id")
    m = re.fullmatch(r"(\d+[.,]\d+)", text)
    if reply in by_msg and m:
        alert_id = by_msg[reply]["id"]
        b = next((b for b in bets if b["alert_id"] == alert_id), None)
        if b:
            b["odds"] = float(m.group(1).replace(",", "."))
            b["odds_source"] = "real"
            mensajero.send_text(f"👍 Cuota {b['odds']} guardada para {b['match']}")
