"""Persistencia simple en ficheros JSON dentro de data/ (GitHub Actions los commitea al acabar)."""
import json
from datetime import datetime, timezone

from . import config

ALERTS = config.DATA_DIR / "alerts.jsonl"
STATE = config.DATA_DIR / "alerted_keys.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_keys():
    try:
        return set(json.loads(STATE.read_text()))
    except Exception:
        return set()


def already_alerted(key):
    return key in _load_keys()


def mark_alerted(key):
    keys = _load_keys()
    keys.add(key)
    STATE.write_text(json.dumps(sorted(keys)[-500:]))


def load_alerts():
    if not ALERTS.exists():
        return []
    return [json.loads(l) for l in ALERTS.read_text(encoding="utf-8").splitlines() if l.strip()]


def save_alerts(alerts):
    ALERTS.write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in alerts), encoding="utf-8")


def append_alert(record):
    with ALERTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def update_alert(alert_id, **fields):
    alerts = load_alerts()
    for a in alerts:
        if a["id"] == alert_id:
            a.update(fields)
    save_alerts(alerts)
