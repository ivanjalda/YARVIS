# Tu motor propio — guía paso a paso

Qué hace:
- Cada mañana te manda ideas por Telegram para ganar más dinero.
- Le escribes por Telegram lo que quieras (crear una web, mejorar otra…).
- Antes de hacer nada te pregunta con dos botones: ✅ Sí / ❌ No.
- Encuentra solo todas tus webs de GitHub (no hay que apuntarlas).
- Coste: 0 €.

Necesitas 4 llaves. Ve guardándolas en una nota del móvil.

---

## Paso 1 — Crear tu bot de Telegram (llave 1)

1. En Telegram, busca **@BotFather** (tiene un tic azul) y entra.
2. Escribe: `/newbot`
3. Te pide un nombre: escribe por ejemplo `Mi Motor`
4. Te pide un usuario que termine en "bot": por ejemplo `ivan_motor_bot`
5. Te contesta con un texto largo tipo `123456789:AAH...` → **eso es la llave 1** (TELEGRAM_BOT_TOKEN).
6. Pulsa el enlace de tu bot y dale a **Iniciar**.

## Paso 2 — Tu número de Telegram (llave 2)

1. Busca **@userinfobot** y dale a **Iniciar**.
2. Te responde con un número en "Id" → **eso es la llave 2** (TELEGRAM_CHAT_ID).

## Paso 3 — La llave de Gemini (llave 3)

1. Entra en **aistudio.google.com** con tu cuenta de Google.
2. Pulsa **Get API key** (u "Obtener clave de API") → **Create API key**.
3. Copia la clave (empieza por `AIza...`) → **llave 3** (GEMINI_API_KEY).

## Paso 4 — La llave de GitHub (llave 4)

1. Entra en **github.com/settings/tokens/new**
2. En "Note" escribe: `motor`
3. En "Expiration" elige: **No expiration**
4. Marca la casilla **repo** (la primera).
5. Abajo del todo: **Generate token**.
6. Copia el texto que empieza por `ghp_...` → **llave 4** (GH_TOKEN).

## Paso 5 — Subir el motor a GitHub

Ya está hecho: el motor está en **github.com/ivanjalda/motor**.

## Paso 6 — Guardar las 4 llaves

1. En el repositorio `motor`: **Settings → Secrets and variables → Actions**.
2. Pulsa **New repository secret** 4 veces, una por llave:

| Name (cópialo igual) | Secret |
|---|---|
| `TELEGRAM_BOT_TOKEN` | llave 1 |
| `TELEGRAM_CHAT_ID` | llave 2 |
| `GEMINI_API_KEY` | llave 3 |
| `GH_TOKEN` | llave 4 |

Las llaves quedan escondidas: nadie las ve, aunque el repositorio sea público.

## Paso 7 — Encenderlo

1. Pestaña **Actions** → si sale un botón verde, púlsalo para activarlas.
2. A la izquierda, **Motor** → **Run workflow** → **Run workflow**.
3. Escríbele `/ayuda` a tu bot. En 10 minutos como mucho te contesta.

¡Listo! Ya trabaja solo, día y noche, aunque tengas el ordenador apagado.

---

## Cosas útiles

- Tus conversaciones y la memoria del motor se guardan en un repositorio **privado**
  que él mismo crea: `motor-memoria`.
- ¿No te gusta un cambio? Escribe `/deshacer`.
- ¿Quieres ideas ya? Escribe `/ideas`.
- Más o menos ideas al día: cambia el número en `config.json`.
