"""Tests sin red: python -m pytest -q"""
import json
import os
import time

os.environ["DRY_RUN"] = "1"
os.environ["LLM_PROVIDER"] = "none"
os.environ["APIFOOTBALL_KEY"] = ""

from futbot import auditor, config, estratega_llm, estratega_reglas, model, scout, sofascore, storage  # noqa: E402

SAMPLE = json.loads((config.ROOT / "tests" / "sample_payload.json").read_text(encoding="utf-8"))


def test_frac_to_decimal():
    assert sofascore.frac_to_decimal("5/2") == 3.5
    assert sofascore.frac_to_decimal("1/1") == 2.0
    assert sofascore.frac_to_decimal("2.10") == 2.1
    assert sofascore.frac_to_decimal(None) is None


def test_fit_lambdas_reproduces_odds():
    p = model.demargin({"1": 1.95, "X": 3.5, "2": 4.1})
    lh, la, fitted = model.fit_lambdas(p)
    w, d, l, _ = model._result_probs(lh, la)
    assert fitted and abs(w - p["1"]) < 0.03 and abs(l - p["2"]) < 0.03


def test_red_card_increases_eleven_win_prob():
    lh, la, _ = model.fit_lambdas(model.demargin({"1": 2.5, "X": 3.2, "2": 2.9}))
    with_red = model.project(lh, la, 20, 0, 0, eleven_is_home=True)
    neutral = model.project(lh, la, 20, 0, 0, True, mult_eleven=1.0, mult_ten=1.0)
    assert with_red["p_home"] > neutral["p_home"]


def test_rules_sample():
    v = estratega_reglas.evaluate(SAMPLE)
    assert v["rating"] in ("Alta", "Media", "Descartar")
    assert all(m["min_odds"] >= config.MIN_USEFUL_ODDS for m in v["markets"])


def test_rules_trap_underdog_dominated():
    p = json.loads(json.dumps(SAMPLE))
    p["odds_prematch"]["1x2"] = {"1": 6.0, "X": 4.2, "2": 1.55}       # el de 11 era muy inferior
    p["stats_at_detection"]["home"].update(xg=0.1, shots_total=1, shots_on_target=0, possession=35, corners=0)
    p["stats_at_detection"]["away"].update(xg=0.9, shots_total=8, shots_on_target=4, possession=65, corners=5)
    p["score"] = {"home": 0, "away": 1}
    assert estratega_reglas.evaluate(p)["rating"] == "Descartar"


def test_combine_conservative():
    rules = {"rating": "Media"}
    assert estratega_llm.combine(rules, {"rating": "Descartar"}) == "Descartar"
    assert estratega_llm.combine(rules, {"rating": "Alta", "reasons": ["a"]}) == "Media"
    assert estratega_llm.combine(rules, {"rating": "Alta", "reasons": ["a", "b"]}) == "Alta"
    assert estratega_llm.combine({"rating": "Descartar"}, {"rating": "Alta", "reasons": ["a", "b"]}) == "Descartar"


def test_market_won():
    assert auditor.market_won("Doble oportunidad 1X", 1, 1)
    assert not auditor.market_won("Doble oportunidad X2", 2, 1)
    assert auditor.market_won("Gana Real Sociedad (1)", 2, 0)
    assert auditor.market_won("Más de 1.5 goles (partido)", 1, 1)
    assert not auditor.market_won("Más de 2.5 goles (partido)", 1, 1)


def test_statistics_parser(monkeypatch):
    fake = {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [
        {"key": "ballPossession", "name": "Ball possession", "home": "60%", "away": "40%", "homeValue": 60, "awayValue": 40},
        {"key": "expectedGoals", "name": "Expected goals", "home": "0.81", "away": "0.20", "homeValue": 0.81, "awayValue": 0.2},
        {"key": "totalShotsOnGoal", "name": "Total shots", "homeValue": 7, "awayValue": 2},
        {"key": "shotsOnGoal", "name": "Shots on target", "homeValue": 3, "awayValue": 0},
        {"key": "cornerKicks", "name": "Corner kicks", "homeValue": 4, "awayValue": 1}]}]}]}
    monkeypatch.setattr(sofascore, "get_json", lambda *a, **k: fake)
    s = sofascore.statistics(1)
    assert s["home"]["possession"] == 60 and s["away"]["xg"] == 0.2 and s["home"]["shots_on_target"] == 3


def test_scout_loop_end_to_end(monkeypatch, tmp_path):
    """Simula un partido en ventana con una roja al min 17 y comprueba que sale UNA alerta."""
    monkeypatch.setattr(storage, "ALERTS", tmp_path / "alerts.jsonl")
    monkeypatch.setattr(storage, "STATE", tmp_path / "keys.json")
    now = time.time()
    ev = {"match_id": 99, "competition_id": 17, "competition": "Premier League", "home": "Local FC",
          "away": "Visitante FC", "home_score": 0, "away_score": 0, "status_code": 6,
          "start_ts": now - 18 * 60, "minute": 18, "home_reds": None, "away_reds": 1}
    monkeypatch.setattr(sofascore, "scheduled_today", lambda: [ev])
    monkeypatch.setattr(sofascore, "live_events", lambda: [ev])
    monkeypatch.setattr(sofascore, "red_cards", lambda mid: [
        {"minute": 17, "added": 0, "is_home": False, "card_type": "doble_amarilla", "player": "X"}])
    monkeypatch.setattr(sofascore, "statistics", lambda mid: SAMPLE["stats_at_detection"])
    monkeypatch.setattr(sofascore, "odds", lambda mid: {
        "1x2": {k: {"initial": v, "current": v} for k, v in SAMPLE["odds_prematch"]["1x2"].items()},
        "dc": {}, "ou": {}})
    monkeypatch.setattr(config, "POLL_SECONDS", 0)
    sent = []
    from futbot import mensajero
    monkeypatch.setattr(mensajero, "_api", lambda m, b: sent.append(b.get("text", "")) or {"result": {"message_id": 1}})
    scout._watch_sofascore(deadline=time.time() + 0.5)
    assert len(sent) == 1 and "ROJA TEMPRANA" in sent[0]
    alerts = storage.load_alerts()
    assert len(alerts) == 1 and alerts[0]["payload"]["team_with_advantage"]["name"] == "Local FC"


def test_equal_reds_no_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "STATE", tmp_path / "keys.json")
    called = []
    monkeypatch.setattr(scout, "process_red", lambda *a, **k: called.append(1))
    match = {"match_id": 5, "home": "A", "away": "B"}
    reds = [{"minute": 10, "added": 0, "is_home": True}, {"minute": 10, "added": 0, "is_home": False}]
    scout._handle_new_reds(match, reds, "sofascore")
    assert called == []  # una roja para cada uno en el mismo minuto: 10 vs 10, sin ventaja


def _fav_env(monkeypatch, tmp_path, score=(0, 1), minute=14):
    monkeypatch.setattr(storage, "ALERTS", tmp_path / "alerts.jsonl")
    monkeypatch.setattr(storage, "STATE", tmp_path / "keys.json")
    now = time.time()
    ev = {"match_id": 7, "competition_id": 8, "competition": "LaLiga", "home": "Grande CF",
          "away": "Pequeño CF", "home_score": score[0], "away_score": score[1], "status_code": 6,
          "start_ts": now - minute * 60, "minute": minute, "home_reds": None, "away_reds": None}
    odds = {"1": 1.35, "X": 5.0, "2": 9.0}
    monkeypatch.setattr(sofascore, "scheduled_today", lambda: [ev])
    monkeypatch.setattr(sofascore, "live_events", lambda: [ev])
    monkeypatch.setattr(sofascore, "red_cards", lambda mid: [])
    monkeypatch.setattr(sofascore, "goals", lambda mid: [{"minute": 9, "is_home": False}])
    monkeypatch.setattr(sofascore, "statistics", lambda mid: SAMPLE["stats_at_detection"])
    monkeypatch.setattr(sofascore, "odds", lambda mid: {
        "1x2": {k: {"initial": v, "current": {"1": 2.1, "X": 3.6, "2": 3.8}[k]} for k, v in odds.items()},
        "dc": {"1X": {"initial": 1.05, "current": 1.33}}, "ou": {}})
    monkeypatch.setattr(config, "POLL_SECONDS", 0)
    sent = []
    from futbot import mensajero
    monkeypatch.setattr(mensajero, "_api", lambda m, b: sent.append(b) or {"result": {"message_id": 5}})
    scout._PRE_PROBS.clear()
    return sent


def test_favorite_losing_triggers(monkeypatch, tmp_path):
    sent = _fav_env(monkeypatch, tmp_path)
    scout._watch_sofascore(deadline=time.time() + 0.5)
    texts = [b["text"] for b in sent if "text" in b]
    assert len(texts) == 1 and "FAVORITO PERDIENDO" in texts[0]
    a = storage.load_alerts()[0]
    assert a["payload"]["trigger_type"] == "favorito_pierde"
    assert a["payload"]["favorite"]["name"] == "Grande CF"
    assert a["payload"]["event_minute"] == 9


def test_favorite_winning_no_trigger(monkeypatch, tmp_path):
    sent = _fav_env(monkeypatch, tmp_path, score=(1, 0))
    scout._watch_sofascore(deadline=time.time() + 0.5)
    assert not [b for b in sent if "text" in b]


def test_real_bets_flow(monkeypatch, tmp_path):
    from futbot import bets, mensajero
    monkeypatch.setattr(storage, "ALERTS", tmp_path / "alerts.jsonl")
    monkeypatch.setattr(bets, "BETS", tmp_path / "bets.jsonl")
    monkeypatch.setattr(bets, "OFFSET", tmp_path / "off.json")
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    rules = estratega_reglas.evaluate(SAMPLE)
    storage.append_alert({"id": "abc", "created_at": storage.now_iso(), "payload": SAMPLE, "rules": rules,
                          "final_rating": "Media", "telegram_message_id": 99, "result": None})
    updates = {"result": [
        {"update_id": 1, "callback_query": {"id": "c", "data": "b:abc:1", "message": {"chat": {"id": 42}}}},
        {"update_id": 2, "message": {"chat": {"id": 42}, "text": "1,85", "reply_to_message": {"message_id": 99}}},
    ]}
    calls = []
    monkeypatch.setattr(mensajero, "_api", lambda m, b: calls.append(m) or (updates if m == "getUpdates" else {"result": {}}))
    assert bets.sync() == 2
    b = bets.load()[0]
    assert b["stake"] == 1 and b["odds"] == 1.85 and b["odds_source"] == "real"
    # liquidar: gana el local 2-0
    storage.update_alert("abc", result={"final": "2-0", "market": b["market"]})
    bets.settle_bets()
    s = bets.month_summary()
    assert s["closed"] == 1 and s["profit"] == round(0.85, 2) or s["profit"] == -1
    assert "Objetivo del mes" in bets.progress_text(s)


def test_espn_parsing(monkeypatch):
    from futbot import espn
    ev = {"id": "401", "date": "2026-10-31T15:00Z", "competitions": [{
        "status": {"displayClock": "21'", "period": 1, "type": {"state": "in", "name": "STATUS_FIRST_HALF"}},
        "competitors": [{"homeAway": "home", "score": "0", "team": {"id": "83", "displayName": "Barcelona"}},
                        {"homeAway": "away", "score": "1", "team": {"id": "99", "displayName": "Racing"}}],
        "details": [{"type": {"text": "Goal"}, "scoringPlay": True, "clock": {"displayValue": "12'"}, "team": {"id": "99"}},
                    {"type": {"text": "Red Card"}, "redCard": True, "clock": {"displayValue": "19'"},
                     "team": {"id": "99"}, "athletesInvolved": [{"displayName": "Defensa"}]}],
        "odds": [{"provider": {"name": "ESPN BET"}, "homeTeamOdds": {"moneyLine": -400},
                  "awayTeamOdds": {"moneyLine": 900}, "drawOdds": {"moneyLine": 450}}]}]}
    monkeypatch.setattr(espn, "get_json", lambda url, **k: {"events": [ev]} if "esp.1" in url else {"events": []})
    live = espn.live_events()
    assert len(live) == 1
    m = live[0]
    assert m["minute"] == 21 and m["status_code"] == espn.FIRST_HALF and m["away_reds"] == 1
    r = espn.red_cards(401)[0]
    assert r["minute"] == 19 and r["is_home"] is False and r["player"] == "Defensa"
    assert espn.goals(401) == [{"minute": 12, "is_home": False}]
    o = espn.odds(401)["1x2"]
    assert o["1"]["initial"] == 1.25 and o["2"]["initial"] == 10.0


def test_llm_panel(monkeypatch):
    from futbot import estratega_llm as L
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "GROQ_API_KEY", "q")
    monkeypatch.setattr(config, "CEREBRAS_API_KEY", "c")
    answers = {"gemini": "Media", "groq": "Alta", "cerebras": "Descartar"}

    def fake_ask(prov, system, user):
        assert "TU PAPEL" in system
        return {"rating": answers[prov], "confidence": 60, "summary": prov, "reasons": [prov], "red_flags": []}, "m"
    monkeypatch.setattr(L, "ask", fake_ask)
    v = L.review(SAMPLE, estratega_reglas.evaluate(SAMPLE))
    assert v["rating"] == "Media" and len(v["panel"]) == 3
    assert [m["role"] for m in v["panel"]] == ["Matemático", "Táctico", "Abogado del diablo"]
    answers.update(gemini="Descartar")
    assert L.review(SAMPLE, estratega_reglas.evaluate(SAMPLE))["rating"] == "Descartar"


def test_llm_panel_partial_failure(monkeypatch):
    from futbot import estratega_llm as L
    monkeypatch.setattr(config, "LLM_PROVIDER", "auto")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "g")
    monkeypatch.setattr(config, "GROQ_API_KEY", "q")
    monkeypatch.setattr(config, "CEREBRAS_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_COMPAT_API_KEY", "")

    def fake_ask(prov, system, user):
        if prov == "gemini":
            raise RuntimeError("HTTP 503 saturado")
        return {"rating": "Alta", "confidence": 70, "summary": "ok", "reasons": ["a", "b"], "red_flags": []}, "m"
    monkeypatch.setattr(L, "ask", fake_ask)
    v = L.review(SAMPLE, estratega_reglas.evaluate(SAMPLE))
    assert v["rating"] == "Alta" and len(v["panel"]) == 1 and v["failed"]
