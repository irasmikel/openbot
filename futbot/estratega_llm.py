"""AGENTE 2b · Estratega LLM. Revisa el payload + veredicto de reglas con el system prompt
conservador (prompts/estratega_system.md). Proveedores: Gemini (gratis) o cualquier API
compatible con OpenAI (GLM de Z.ai, OpenRouter, Ollama, LM Studio...)."""
import json
import logging
import re

from . import config
from .http import post_json

log = logging.getLogger(__name__)
ORDER = {"Descartar": 0, "Media": 1, "Alta": 2}
LAST_ERROR = None


def enabled():
    if config.LLM_PROVIDER == "gemini":
        return bool(config.GEMINI_API_KEY)
    if config.LLM_PROVIDER == "openai_compat":
        return bool(config.OPENAI_COMPAT_API_KEY) or "localhost" in config.OPENAI_COMPAT_BASE_URL
    return False


def _system_prompt():
    return (config.PROMPTS_DIR / "estratega_system.md").read_text(encoding="utf-8")


def _user_content(payload, rules):
    slim_rules = {k: v for k, v in rules.items() if k != "model"}
    slim_rules["model_summary"] = {k: rules["model"].get(k) for k in
                                   ("lambda_prematch", "lambda_remaining", "p_home", "p_draw", "p_away", "over")}
    return json.dumps({"trigger_payload": payload, "rules_verdict": slim_rules}, ensure_ascii=False)


def _extract_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    return json.loads(m.group(0) if m else text)


def _call_gemini(system, user):
    """Claves 'AIza…' = Google AI Studio. Claves 'AQ.…' = Vertex AI (modo express)."""
    key = config.GEMINI_API_KEY.strip()
    if key.startswith("AQ."):
        url = (f"https://aiplatform.googleapis.com/v1/publishers/google/models/"
               f"{config.GEMINI_MODEL}:generateContent")
        label = "Gemini (Vertex)"
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
        label = "Gemini (AI Studio)"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    data = post_json(url, body, headers={"x-goog-api-key": key}, timeout=45, label=label)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _call_openai_compat(system, user):
    url = config.OPENAI_COMPAT_BASE_URL.rstrip("/") + "/chat/completions"
    body = {"model": config.OPENAI_COMPAT_MODEL, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    headers = {"Authorization": f"Bearer {config.OPENAI_COMPAT_API_KEY}"}
    data = post_json(url, body, headers=headers, timeout=45, label="LLM")
    return data["choices"][0]["message"]["content"]


def review(payload, rules):
    """Devuelve el veredicto del LLM o None si falla. Nunca bloquea la alerta."""
    if not enabled():
        return None
    try:
        system, user = _system_prompt(), _user_content(payload, rules)
        raw = _call_gemini(system, user) if config.LLM_PROVIDER == "gemini" else _call_openai_compat(system, user)
        v = _extract_json(raw)
        if v.get("rating") not in ORDER:
            raise ValueError(f"rating inválido: {v.get('rating')}")
        v["by"] = f"llm:{config.LLM_PROVIDER}"
        return v
    except Exception as e:
        global LAST_ERROR
        LAST_ERROR = str(e)[:300]
        log.warning("Estratega LLM falló: %s", e)
        return None


def combine(rules, llm):
    """Política conservadora: manda el más prudente, salvo que el LLM suba un nivel
    citando razones (lo exige su prompt) y las reglas no lo hayan descartado."""
    if not llm:
        return rules["rating"]
    r, l = ORDER[rules["rating"]], ORDER[llm["rating"]]
    if l <= r:
        return llm["rating"]
    if r == 0:
        return "Descartar"
    return llm["rating"] if (l - r == 1 and len(llm.get("reasons", [])) >= 2) else rules["rating"]
