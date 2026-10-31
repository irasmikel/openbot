"""Cliente HTTP sencillo (requests). Sin suplantar navegadores."""
import logging
import time

log = logging.getLogger(__name__)

import requests as _req

_SESSION = _req.Session()
BACKEND = "requests"

# Cliente identificado honestamente: solo usamos fuentes que permiten acceso automático.
BROWSER_HEADERS = {
    "User-Agent": "futbot/1.0 (+https://github.com/irasmikel/openbot)",
    "Accept": "application/json",
}


class HttpError(Exception):
    def __init__(self, status, url):
        super().__init__(f"HTTP {status} en {url}")
        self.status = status


def get_json(url, headers=None, params=None, timeout=10, retries=2):
    h = dict(BROWSER_HEADERS)
    if headers:
        h.update(headers)
    last = None
    for attempt in range(retries + 1):
        try:
            r = _SESSION.get(url, headers=h, params=params, timeout=timeout)
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                raise HttpError(r.status_code, url)
            return r.json()
        except HttpError as e:
            last = e
            if e.status in (401, 403):
                raise
        except Exception as e:  # red / timeout / json
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def post_json(url, payload, headers=None, timeout=20, label="POST"):
    """`label` se usa en los errores para no volcar URLs con tokens a los logs."""
    import requests  # Telegram/LLM: requests normal es suficiente
    r = requests.post(url, json=payload, headers=headers or {}, timeout=timeout)
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text}
    if r.status_code >= 400:
        raise RuntimeError(f"{label} -> HTTP {r.status_code}: {str(data)[:300]}")
    return data
