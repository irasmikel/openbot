# futbot · Alertas de rojas tempranas (7 competiciones)

Sistema de 4 agentes que corre **gratis en GitHub Actions** (no necesita tu PC encendido) y te avisa
por Telegram cuando hay una expulsión antes del minuto 30 en LaLiga, Premier, Serie A, Bundesliga,
Ligue 1, Champions o Europa League, con un veredicto conservador y la **cuota mínima** a la que
merece la pena entrar.

```
GitHub Actions (cada 20', 10:00-23:00)
  └─ ¿hay partidos de las 7 competiciones en los próximos 40'?  ── no ─► termina (≈20 s)
        │ sí
        ▼
  AGENTE 1 · Scout ── SofaScore cada 30 s, solo partidos en su primera media hora
        │ roja ≤ min 30 y superioridad real
        ▼
  Enriquecedor ── stats (posesión, tiros, xG, córners, ocasiones) + cuotas Bwin pre-partido + cuotas actuales
        ▼
  AGENTE 2a · Estratega-reglas ── puntuación 0-100 + modelo Poisson → mercado y cuota mínima
        ▼
  AGENTE 3 · Mensajero ── Telegram (≈ 20-60 s desde la roja)
        ▼
  AGENTE 2b · Estratega-IA (Gemini/GLM) ── revisa y EDITA el mismo mensaje (+5-15 s)
        ▼
  data/alerts.jsonl  ──►  AGENTE 4 · Auditor (lunes) ── resultado, acierto, ROI en papel → Telegram
                          + agente "Auditor-Coach" en OpenBot (por chat, cuando quieras)
```

## 1. Credenciales (15 minutos)

| Qué | Dónde | Nombre del secret |
|---|---|---|
| Token del bot | Telegram → @BotFather → `/newbot` | `TELEGRAM_BOT_TOKEN` |
| Tu chat id | Escribe a tu bot y abre `https://api.telegram.org/bot<TOKEN>/getUpdates` → `chat.id` | `TELEGRAM_CHAT_ID` |
| Clave Gemini (gratis) | https://aistudio.google.com/apikey | `GEMINI_API_KEY` |
| Clave API-Football (Free, opcional) | https://dashboard.api-football.com | `APIFOOTBALL_KEY` |

**¿GLM en vez de Gemini?** Crea una API key en Z.ai y en GitHub añade la *variable* `LLM_PROVIDER=openai_compat`
y el *secret* `OPENAI_COMPAT_API_KEY`. Sirve cualquier API compatible con OpenAI (OpenRouter, etc.).
**Consumo**: cada alerta son ~3.000 tokens; con 10-15 alertas al mes son ~50.000 tokens/mes → la capa gratuita
de Gemini sobra. No uses tu suscripción de Claude para esto.

Sin `APIFOOTBALL_KEY` funciona igual, pero las cuotas pre-partido serán las de apertura de SofaScore en vez de las de Bwin.

## 2. Subirlo a GitHub (repositorio **público**)

Público porque los minutos de Actions son **ilimitados** en repos públicos (en privados hay 2.000 min/mes y
este sistema necesita ~4.000-5.000 en temporada). Las claves van en *Secrets* cifrados y nunca se ven.
Lo único visible es el código y `data/` (tu registro de alertas).

En PowerShell, dentro de esta carpeta (el primer comando mueve los workflows a donde GitHub los busca):
```powershell
New-Item -ItemType Directory -Force .github\workflows | Out-Null; Move-Item workflows\*.yml .github\workflows\ -Force; Remove-Item workflows
git init -b main
git add .
git commit -m "futbot inicial"
git remote add origin https://github.com/<tu-usuario>/futbot.git
git push -u origin main
```

Luego, en GitHub → **Settings → Secrets and variables → Actions**:
- *Secrets*: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `APIFOOTBALL_KEY`
- *Variables* (opcionales): `LLM_PROVIDER`, `GEMINI_MODEL`, `OPENAI_COMPAT_MODEL`

## 3. Probar

GitHub → **Actions → "Scout (rojas tempranas)" → Run workflow**:
1. `diagnose` → te llega a Telegram un ✅/❌ de SofaScore, API-Football, el LLM y Telegram.
   **Importante**: confirma que SofaScore responde desde GitHub. Si sale ❌ 403, el Scout pasará solo a modo
   respaldo con API-Football (más lento: consulta cada 2,5 min).
2. `test-alert` → te llega una alerta 🧪 simulada y, segundos después, se edita con el análisis de IA.

Desde ese momento el horario automático queda activo. Tests locales: `python -m pytest -q`.

## 4. Cómo leer la alerta

- **🟢 Alta / 🟡 Media / 🔴 Descartar**: lo más prudente entre las reglas y la IA (la IA solo puede subir un nivel
  si da 2 razones concretas, y nunca rescata un "Descartar" de las reglas).
- **👉 Solo si Bwin paga ≥ X**: la cuota justa del modelo × 1,08. **Si Bwin no llega a esa cuota, no se entra.** Esta es la regla
  que de verdad protege tu dinero.
- La "ref. actual" es la cuota de SofaScore, no la de Bwin: compruébala siempre en Bwin.
- Los mercados con cuota mínima < 1,25 se descartan solos (1 € a 1,10 no compensa el riesgo).

## 5. Ajustes (`futbot/config.py` o variables de entorno)

| Parámetro | Por defecto | Qué hace |
|---|---|---|
| `MAX_RED_MINUTE` | 30 | Minuto límite de la roja |
| `POLL_SECONDS` | 30 | Frecuencia del Scout |
| `RED_MULT_ELEVEN` / `RED_MULT_TEN` | 1.30 / 0.70 | Cuánto cambia el ritmo goleador tras la roja (**hipótesis**: el Auditor las calibrará) |
| `VALUE_MARGIN` | 1.08 | Margen exigido sobre la cuota justa |

## 6. Estrategias

| Estrategia | Se dispara cuando | Se respalda a |
|---|---|---|
| 🟥 Roja temprana | Expulsión ≤ min 30 que deja superioridad | Equipo con 11 |
| 📉 Favorito perdiendo | Favorito ≥ 60% pre-partido (cuota ≈ ≤ 1,55) pierde antes del min 30 | Favorito |

Ninguna es "segura": un 85% de probabilidad sigue perdiendo 3 de cada 20 veces. Por eso manda la **cuota mínima**.

## 7. Dinero real y objetivo de 20 €

- **Stake sugerido**: Media 0,5 € · Alta 1 € · Alta con cuota de valor confirmada 2 € (`STAKE_*` en config).
- **Botones** en cada alerta: ✅ 0,5 € / 1 € / 2 € o ❌ No apuesto. Después **responde al mensaje con la cuota real** (ej. `1,85`).
  Si no lo haces, se usa la cuota mínima (estimación prudente).
- **/estado** en el chat del bot → progreso del mes hacia 20 €, aciertos y ROI.
- **Stop-loss**: si en el mes pierdes 15 €, las alertas siguen llegando pero con stake 0 hasta el mes siguiente.
- Los botones y /estado se procesan en la siguiente ejecución (como mucho 20' en horario de partidos; de madrugada, a la mañana siguiente).
- Con 10-25 alertas al mes y stakes de 0,5-2 €, llegar a 20 €/mes exige un ROI muy alto; el informe del lunes te dirá con datos si va por buen camino.

## 8. OpenBot

Como OpenBot funciona por chat y tu PC no está siempre encendido, la parte en tiempo real vive en GitHub.
En OpenBot crea el agente **Auditor-Coach** con `prompts/openbot_auditor.md`: tras un `git pull`, lee
`data/alerts.jsonl` y te propone ajustes de parámetros. `prompts/estratega_system.md` es el prompt del
Estratega-IA. También puedes pegarlo en un agente de OpenBot para analizar a mano cualquier partido.

## Estructura
```
futbot/        scout.py · enricher.py · estratega_reglas.py · estratega_llm.py · mensajero.py · auditor.py
               sofascore.py · apifootball.py · model.py · config.py · storage.py · http.py
prompts/       estratega_system.md · openbot_auditor.md
schemas/       trigger_payload.schema.json · verdict.schema.json
tests/         test_core.py · sample_payload.json
workflows/     scout.yml · auditor.yml   (→ muévelos a .github/workflows antes de subir)
data/          alerts.jsonl · informe_semanal.md (se generan solos)
```
