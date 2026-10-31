"""AGENTE 2b · Panel de analistas IA (todos GRATIS).

Cada proveedor con clave configurada es un analista con un papel distinto. Analizan en paralelo
el payload + veredicto de reglas con el system prompt conservador (prompts/estratega_system.md)
y el veredicto del panel es la MEDIANA PRUDENTE de sus opiniones (2 de 3 "Descartar" → Descartar).

Proveedores (secret de GitHub → proveedor):
  GEMINI_API_KEY        → Google AI Studio (Gemini Flash)
  GROQ_API_KEY          → Groq (Llama 3.3 70B / gpt-oss)
  CEREBRAS_API_KEY      → Cerebras (Llama / gpt-oss)
  OPENROUTER_API_KEY    → OpenRouter (modelos ':free')
  OPENAI_COMPAT_API_KEY → GLM de Z.ai (u otra API compatible con OpenAI)"""
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .http import post_json

log = logging.getLogger(__name__)
ORDER = {"Descartar": 0, "Media": 1, "Alta": 2}
LAST_ERROR = None

# nombre -> (base_url, atributo de clave en config, atributo de modelos en config, etiqueta)
OPENAI_LIKE = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODELS", "Groq"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "CEREBRAS_MODELS", "Cerebras"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODELS", "OpenRouter"),
    "glm": (None, "OPENAI_COMPAT_API_KEY", "OPENAI_COMPAT_MODEL", "GLM"),
}
PREFERENCE = ["groq", "glm", "gemini", "cerebras", "openrouter"]  # rápidos primero; Gemini a veces va lento

ROLES = [
    ("🧮", "Matemático",
     "TU PAPEL: analista MATEMÁTICO. Céntrate en probabilidades, cuota mínima y valor esperado. "
     "¿El precio compensa el riesgo? Si no hay cuota en vivo, sé condicional."),
    ("⚽", "Táctico",
     "TU PAPEL: analista TÁCTICO. Céntrate en el juego: quién dominaba, si el dominio es real "
     "(tiros a puerta, ocasiones) o solo posesión, contexto local/visitante y marcador."),
    ("😈", "Abogado del diablo",
     "TU PAPEL: ABOGADO DEL DIABLO. Tu trabajo es encontrar motivos para NO apostar: trampas, "
     "datos que faltan, muestras pequeñas, cuotas ya ajustadas. Solo da 'Media' o 'Alta' si no "
     "encuentras ninguna objeción seria."),
]


# ------------------------------------------------------------------ proveedores disponibles
def _key(prov):
    if prov == "gemini":
        return config.GEMINI_API_KEY
    return getattr(config, OPENAI_LIKE[prov][1], "")


def available():
    """Todos los proveedores con clave (titulares primero, luego suplentes)."""
    if config.LLM_PROVIDER not in ("auto", "panel"):
        prov = {"openai_compat": "glm"}.get(config.LLM_PROVIDER, config.LLM_PROVIDER)
        return [prov] if prov in PREFERENCE and _key(prov) else []
    return [p for p in PREFERENCE if _key(p)]


def enabled():
    return bool(available())


# ------------------------------------------------------------------ prompts
def _system_prompt(role_text=None):
    base = (config.PROMPTS_DIR / "estratega_system.md").read_text(encoding="utf-8")
    return base + ("\n\n" + role_text if role_text else "")


def _user_content(payload, rules):
    slim_rules = {k: v for k, v in rules.items() if k != "model"}
    slim_rules["model_summary"] = {k: rules["model"].get(k) for k in
                                   ("lambda_prematch", "lambda_remaining", "p_home", "p_draw", "p_away", "over")}
    return json.dumps({"trigger_payload": payload, "rules_verdict": slim_rules}, ensure_ascii=False)


def _extract_json(text):
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # modelos que "piensan en voz alta"
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    return json.loads(m.group(0) if m else text)


def _busy(msg):
    return any(c in msg for c in ("HTTP 503", "HTTP 429", "HTTP 500", "HTTP 502"))


# ------------------------------------------------------------------ Gemini
_GEMINI_MODEL_OK = None


def _gemini_models(key):
    """Modelos Flash disponibles para esta clave, ordenados de mejor a peor."""
    from .http import get_json
    data = get_json("https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=15) or {}
    names = [m["name"].split("/")[-1] for m in data.get("models", [])
             if "generateContent" in (m.get("supportedGenerationMethods") or [])]

    def score(n):
        v = re.search(r"gemini-(\d+(?:\.\d+)?)-flash", n)
        ver = float(v.group(1)) if v else 0
        stable = not re.search(r"preview|exp|tts|image|audio|live|thinking", n)
        return (stable, n == "gemini-flash-latest", ver, "lite" not in n)
    return sorted((n for n in names if "flash" in n and not re.search(r"tts|image|audio|live", n)),
                  key=score, reverse=True)


def _call_gemini(system, user):
    global _GEMINI_MODEL_OK
    key = config.GEMINI_API_KEY.strip()
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}}
    url = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
    queue = [_GEMINI_MODEL_OK or config.GEMINI_MODEL]
    tried, errors, listed = set(), [], False
    while queue and len(tried) < 4:
        model = queue.pop(0)
        if model in tried:
            continue
        tried.add(model)
        for attempt in range(2):
            try:
                data = post_json(url.format(model), body, headers={"x-goog-api-key": key},
                                 timeout=25, label=f"Gemini {model}")
                parts = data["candidates"][0]["content"]["parts"]
                _GEMINI_MODEL_OK = model
                return "".join(p.get("text", "") for p in parts if not p.get("thought")), model
            except Exception as e:
                msg = str(e)
                if _busy(msg) and attempt == 0:
                    time.sleep(3)
                    continue
                errors.append(msg[:140])
                if (_busy(msg) or "HTTP 404" in msg or "HTTP 400" in msg) and not listed:
                    listed = True
                    try:
                        queue += [m for m in _gemini_models(key) if m not in tried]
                    except Exception as le:
                        errors.append(f"listado: {str(le)[:80]}")
                break
    raise RuntimeError(" | ".join(errors[-2:]))


# ------------------------------------------------------------------ APIs compatibles con OpenAI
_OR_FREE = None


def _openrouter_free_models():
    """Lista actual de modelos gratuitos de OpenRouter (cambian a menudo)."""
    global _OR_FREE
    if _OR_FREE is None:
        from .http import get_json
        data = get_json("https://openrouter.ai/api/v1/models", timeout=15) or {}
        free = [m["id"] for m in data.get("data", [])
                if m.get("id", "").endswith(":free")
                and str((m.get("pricing") or {}).get("prompt", "1")) in ("0", "0.0")]
        pref = ("gpt-oss-120b", "llama-3.3-70b", "deepseek", "qwen3", "gemma", "mistral")

        def score(i):
            return next((n for n, p in enumerate(pref) if p in i), len(pref))
        _OR_FREE = sorted(free, key=score)[:4]
    return _OR_FREE


def _models_for(prov, models_attr):
    configured = [m.strip() for m in getattr(config, models_attr).split(",") if m.strip()]
    if prov == "openrouter" and (not configured or configured == ["auto"]):
        try:
            return _openrouter_free_models()
        except Exception as e:
            log.warning("OpenRouter listado: %s", e)
            return []
    return configured


def _call_openai_like(prov, system, user):
    base, key_attr, models_attr, label = OPENAI_LIKE[prov]
    base = base or config.OPENAI_COMPAT_BASE_URL
    key = getattr(config, key_attr).strip()
    headers = {"Authorization": f"Bearer {key}"}
    if prov == "openrouter":
        headers.update({"HTTP-Referer": "https://github.com/irasmikel/openbot", "X-Title": "futbot"})
    errors = []
    models = _models_for(prov, models_attr)
    if not models:
        raise RuntimeError("sin modelos disponibles")
    for model in models:
        for attempt in range(2):
            try:
                body = {"model": model, "temperature": 0.2, "max_tokens": 2500,
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
                if prov == "glm":
                    body["thinking"] = {"type": "disabled"}  # si no, gasta los tokens pensando
                if prov in ("groq", "cerebras", "openrouter") and "gpt-oss" in model:
                    body["reasoning_effort"] = "low"
                data = post_json(base.rstrip("/") + "/chat/completions", body, headers=headers,
                                 timeout=45, label=f"{label} {model}")
                if not data.get("choices"):
                    raise RuntimeError(str(data)[:150])
                content = data["choices"][0]["message"].get("content") or ""
                if not content.strip():
                    raise RuntimeError(f"respuesta vacía (finish={data['choices'][0].get('finish_reason')})")
                return content, model
            except Exception as e:
                msg = str(e)
                if attempt == 0 and _busy(msg):
                    time.sleep(3)
                    continue
                errors.append(msg[:140])
                break
    raise RuntimeError(" | ".join(errors[-2:]))


def ask(prov, system, user):
    """Pregunta a un proveedor. Devuelve (veredicto_dict, modelo)."""
    raw, model = _call_gemini(system, user) if prov == "gemini" else _call_openai_like(prov, system, user)
    if not (raw or "").strip():
        raise ValueError("respuesta vacía (¿se quedó sin tokens pensando?)")
    v = _extract_json(raw)
    v["rating"] = _norm_rating(v.get("rating"))
    return v, model


def _norm_rating(r):
    t = str(r or "").strip().lower()
    t = t.replace("á", "a").replace("é", "e")
    if t.startswith("alt") or t == "high":
        return "Alta"
    if t.startswith("med") or t == "medium":
        return "Media"
    if t.startswith("desc") or t in ("discard", "low", "baja", "no"):
        return "Descartar"
    raise ValueError(f"rating inválido: {r!r}")


# ------------------------------------------------------------------ panel
def _panel_rating(ratings):
    """Mediana prudente: ordena de más prudente a menos y coge la posición central (baja)."""
    s = sorted(ratings, key=lambda r: ORDER[r])
    return s[(len(s) - 1) // 2]


def review(payload, rules):
    """Consulta al panel en paralelo. Devuelve un veredicto agregado o None si nadie responde.
    Nunca bloquea la alerta: el Mensajero ya la ha enviado antes."""
    global LAST_ERROR
    provs = available()
    if not provs:
        return None
    try:
        user = _user_content(payload, rules)
    except Exception as e:
        log.warning("prompt: %s", e)
        return None
    size = min(config.LLM_PANEL_SIZE, len(provs))
    roles = ROLES[:size] if size > 1 else [("🤖", "Analista", None)]
    starters, bench = provs[:len(roles)], provs[len(roles):]
    members, errors = [], []

    def run(pairs):
        """pairs: [(prov, role_tuple)] -> lista de roles que fallaron"""
        failed = []
        with ThreadPoolExecutor(max_workers=len(pairs)) as ex:
            jobs = {ex.submit(ask, prov, _system_prompt(r[2]), user): (prov, r) for prov, r in pairs}
            try:
                for fut in as_completed(jobs, timeout=config.LLM_TIMEOUT_S + 30):
                    prov, r = jobs[fut]
                    try:
                        v, model = fut.result()
                        members.append({**v, "provider": prov, "model": model, "icon": r[0], "role": r[1]})
                    except Exception as e:
                        errors.append(f"{prov}: {str(e)[:160]}")
                        log.warning("Analista %s (%s) falló: %s", r[1], prov, e)
                        failed.append(r)
            except Exception as e:  # timeout global
                errors.append(f"timeout: {e}")
                done = {(m["provider"]) for m in members}
                failed += [r for f, (prov, r) in jobs.items() if prov not in done and r not in failed]
        return failed

    failed_roles = run(list(zip(starters, roles)))
    # suplentes ocupan los papeles que han fallado
    while failed_roles and bench:
        pairs = list(zip(bench, failed_roles))
        bench = bench[len(pairs):]
        failed_roles = failed_roles[len(pairs):] + run(pairs)
    if not members:
        LAST_ERROR = " | ".join(errors) or "sin respuesta"
        return None
    LAST_ERROR = " | ".join(errors) or None
    # orden estable según el papel
    names = [r[1] for r in ROLES]
    members.sort(key=lambda m: names.index(m["role"]) if m["role"] in names else 0)
    final = _panel_rating([m["rating"] for m in members])
    lead = next((m for m in members if m["rating"] == final), members[0])

    def uniq(key, n):
        out = []
        for m in members:
            for x in m.get(key) or []:
                if x and x not in out:
                    out.append(x)
        return out[:n]
    return {
        "rating": final,
        "confidence": round(sum(int(m.get("confidence") or 0) for m in members) / len(members)),
        "market": lead.get("market"),
        "min_odds": lead.get("min_odds"),
        "summary": lead.get("summary"),
        "reasons": uniq("reasons", 3),
        "red_flags": uniq("red_flags", 3),
        "panel": [{"icon": m["icon"], "role": m["role"], "rating": m["rating"],
                   "provider": m["provider"], "model": m["model"], "summary": m.get("summary")}
                  for m in members],
        "failed": errors,
        "by": "panel:" + "+".join(m["provider"] for m in members),
    }


def ping_all():
    """Diagnóstico: prueba cada proveedor por separado con una pregunta mínima."""
    out = []
    sysmsg = 'Responde SOLO con este JSON: {"rating":"Media","confidence":50,"summary":"ok","reasons":[],"red_flags":[]}'
    for prov in [p for p in PREFERENCE if _key(p)]:
        t = time.time()
        try:
            v, model = ask(prov, sysmsg, "ping")
            out.append((True, prov, f"{model} ({time.time() - t:.1f}s)"))
        except Exception as e:
            out.append((False, prov, str(e)[:200]))
    return out


def combine(rules, llm):
    """Política conservadora: manda el más prudente, salvo que el panel suba un nivel
    citando razones y las reglas no lo hayan descartado."""
    if not llm:
        return rules["rating"]
    r, l = ORDER[rules["rating"]], ORDER[llm["rating"]]
    if l <= r:
        return llm["rating"]
    if r == 0:
        return "Descartar"
    return llm["rating"] if (l - r == 1 and len(llm.get("reasons", [])) >= 2) else rules["rating"]
