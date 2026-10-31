"""Cliente HTTP. Usa curl_cffi (huella de navegador real) si está disponible: SofaScore bloquea
clientes que no parecen un navegador. Si no, cae a requests."""
import logging
import time

log = logging.getLogger(__name__)

try:
    from curl_cffi import requests as _cffi
    _SESSION = _cffi.Session(impersonate="chrome")
    BACKEND = "curl_cffi"
except Exception:  # pragma: no cover
    import requests as _req
    _SESSION = _req.Session()
    BACKEND = "requests"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
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
