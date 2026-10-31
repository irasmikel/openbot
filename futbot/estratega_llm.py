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


def _available():
    """Proveedores con clave, en orden de preferencia."""
    if config.LLM_PROVIDER != "auto":
        return [config.LLM_PROVIDER]
    out = []
    if config.OPENAI_COMPAT_API_KEY:
        out.append("openai_compat")
    if config.GEMINI_API_KEY:
        out.append("gemini")
    return out


def enabled():
    if config.LLM_PROVIDER == "auto":
        return bool(_available())
    if config.LLM_PROVIDER == "github":
        return bool(config.GITHUB_TOKEN)
    if config.LLM_PROVIDER == "anthropic":
        return bool(config.ANTHROPIC_API_KEY)
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
    """Clave de Google AI Studio (gratis). Las claves nuevas pueden empezar por 'AIza' o por 'AQ.'.
    Se prueba primero AI Studio y, si no responde, Vertex AI."""
    key = config.GEMINI_API_KEY.strip()
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    errors = []
    for label, url in (
        ("AI Studio", f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"),
        ("Vertex", f"https://aiplatform.googleapis.com/v1/publishers/google/models/{config.GEMINI_MODEL}:generateContent"),
    ):
        try:
            data = post_json(url, body, headers={"x-goog-api-key": key}, timeout=45, label=f"Gemini ({label})")
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except Exception as e:
            errors.append(str(e)[:160])
    raise RuntimeError(" | ".join(errors))


def _call_github(system, user):
    """GitHub Models: gratis con el GITHUB_TOKEN de Actions (permiso models: read).
    Prueba el endpoint nuevo y, si no responde bien, el clásico de Azure."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    headers = {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Content-Type": "application/json"}
    errors = []
    for url, model in (("https://models.github.ai/inference/chat/completions", config.GITHUB_MODEL),
                       ("https://models.inference.ai.azure.com/chat/completions", config.GITHUB_MODEL.split("/")[-1])):
        try:
            data = post_json(url, {"model": model, "temperature": 0.2, "messages": msgs},
                             headers=headers, timeout=45, label="GitHub Models")
            if data.get("choices"):
                return data["choices"][0]["message"]["content"]
            errors.append(f"{url.split('/')[2]}: {str(data)[:150]}")
        except Exception as e:
            errors.append(f"{url.split('/')[2]}: {str(e)[:150]}")
    raise RuntimeError(" | ".join(errors))


def _call_anthropic(system, user):
    body = {"model": config.ANTHROPIC_MODEL, "max_tokens": 1000, "temperature": 0.2,
            "system": system, "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": config.ANTHROPIC_API_KEY.strip(), "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    data = post_json("https://api.anthropic.com/v1/messages", body, headers=headers, timeout=45, label="Claude")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _call_openai_compat(system, user):
    url = config.OPENAI_COMPAT_BASE_URL.rstrip("/") + "/chat/completions"
    body = {"model": config.OPENAI_COMPAT_MODEL, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    headers = {"Authorization": f"Bearer {config.OPENAI_COMPAT_API_KEY}"}
    data = post_json(url, body, headers=headers, timeout=45, label="GLM/compatible")
    if not data.get("choices"):
        raise RuntimeError(str(data)[:200])
    return data["choices"][0]["message"]["content"]


def review(payload, rules):
    """Devuelve el veredicto del LLM o None si falla. Nunca bloquea la alerta."""
    if not enabled():
        return None
    try:
        system, user = _system_prompt(), _user_content(payload, rules)
    except Exception as e:
        log.warning("prompt: %s", e)
        return None
    global LAST_ERROR
    errors = []
    calls = {"github": _call_github, "anthropic": _call_anthropic, "gemini": _call_gemini,
             "openai_compat": _call_openai_compat}
    for prov in _available():
        try:
            v = _extract_json(calls.get(prov, _call_openai_compat)(system, user))
            if v.get("rating") not in ORDER:
                raise ValueError(f"rating inválido: {v.get('rating')}")
            v["by"] = f"llm:{prov}"
            LAST_ERROR = None
            return v
        except Exception as e:
            errors.append(f"{prov}: {str(e)[:200]}")
            log.warning("Estratega LLM (%s) falló: %s", prov, e)
    LAST_ERROR = " | ".join(errors) or "sin proveedor con clave"
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
