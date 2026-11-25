#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOTOR PROPIO
- Cerebro: Gemini (nivel gratuito de Google).
- Casa: GitHub (se despierta solo cada 10 minutos).
- Mando: tu bot de Telegram. Nada se publica sin tu "Sí".

No hace falta tocar este archivo.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
MOTOR_REPO = os.environ.get("GITHUB_REPOSITORY", "")
NOMBRE_MEMORIA = "motor-memoria"

AQUI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(AQUI, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)


# ---------------------------------------------------------------- conexiones
def http(metodo, url, datos=None, cabeceras=None, espera=120):
    cuerpo = None
    h = dict(cabeceras or {})
    if datos is not None:
        cuerpo = json.dumps(datos).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=cuerpo, headers=h, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=espera) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(txt) if txt.strip() else {})
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, {"error": txt[:500]}
    except Exception as e:  # red caída, tiempo agotado...
        return 0, {"error": str(e)[:300]}


# ---------------------------------------------------------------- GitHub
def gh(metodo, ruta, datos=None):
    return http(metodo, "https://api.github.com" + ruta, datos, {
        "Authorization": "Bearer " + GH_TOKEN,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "motor-propio",
    })


_DUENO = None


def dueno():
    global _DUENO
    if not _DUENO:
        s, d = gh("GET", "/user")
        if s != 200:
            raise RuntimeError("La llave de GitHub (GH_TOKEN) no funciona.")
        _DUENO = d["login"]
    return _DUENO


def repo_completo(nombre):
    nombre = (nombre or "").strip().strip("/")
    return nombre if "/" in nombre else f"{dueno()}/{nombre}"


def leer(repo, ruta):
    s, d = gh("GET", f"/repos/{repo}/contents/{urllib.parse.quote(ruta)}")
    if s != 200 or isinstance(d, list) or not d.get("content"):
        return None, None
    return base64.b64decode(d["content"]).decode("utf-8", "replace"), d["sha"]


def escribir(repo, ruta, contenido, mensaje):
    _, sha = leer(repo, ruta)
    datos = {"message": mensaje,
             "content": base64.b64encode(contenido.encode("utf-8")).decode()}
    if sha:
        datos["sha"] = sha
    s, d = gh("PUT", f"/repos/{repo}/contents/{urllib.parse.quote(ruta)}", datos)
    if s not in (200, 201):
        raise RuntimeError(f"No pude guardar {ruta} en {repo}: {str(d)[:200]}")


def borrar(repo, ruta, mensaje):
    _, sha = leer(repo, ruta)
    if sha:
        gh("DELETE", f"/repos/{repo}/contents/{urllib.parse.quote(ruta)}",
           {"message": mensaje, "sha": sha})


def lista_archivos(repo):
    s, d = gh("GET", f"/repos/{repo}")
    if s != 200:
        return []
    rama = d.get("default_branch", "main")
    s, d = gh("GET", f"/repos/{repo}/git/trees/{rama}?recursive=1")
    if s != 200:
        return []
    return [x["path"] for x in d.get("tree", []) if x.get("type") == "blob"]


def mis_webs():
    """Todas tus webs: cada repositorio tuyo que tenga GitHub Pages."""
    webs, pagina = [], 1
    while pagina < 10:
        s, d = gh("GET", f"/user/repos?per_page=100&page={pagina}&affiliation=owner")
        if s != 200 or not d:
            break
        for r in d:
            if r["name"] in (NOMBRE_MEMORIA, MOTOR_REPO.split("/")[-1]):
                continue
            if r.get("has_pages") and not r.get("archived"):
                webs.append({
                    "repo": r["name"],
                    "descripcion": r.get("description") or "",
                    "url": r.get("homepage") or
                    f"https://{r['owner']['login'].lower()}.github.io/{r['name']}/",
                })
        pagina += 1
    return webs


# ---------------------------------------------------------------- memoria privada
def memoria():
    repo = f"{dueno()}/{NOMBRE_MEMORIA}"
    s, _ = gh("GET", f"/repos/{repo}")
    if s == 404:
        gh("POST", "/user/repos", {"name": NOMBRE_MEMORIA, "private": True,
                                   "auto_init": True,
                                   "description": "Memoria privada del motor"})
        time.sleep(4)
    return repo


def cargar_estado():
    txt, _ = leer(memoria(), "estado.json")
    est = json.loads(txt) if txt else {}
    est.setdefault("offset", 0)
    est.setdefault("pendientes", {})
    est.setdefault("deshacer", [])
    est.setdefault("charla", [])
    est.setdefault("hecho", [])
    return est, (txt or "")


def guardar_estado(est, original):
    est["pendientes"] = dict(list(est["pendientes"].items())[-40:])
    est["deshacer"] = est["deshacer"][-5:]
    est["charla"] = est["charla"][-12:]
    est["hecho"] = est["hecho"][-60:]
    nuevo = json.dumps(est, ensure_ascii=False, indent=1)
    if nuevo != original:
        escribir(memoria(), "estado.json", nuevo, "memoria")


# ---------------------------------------------------------------- Gemini
_MODELO = None
_BUSCADO = False


def buscar_modelo():
    s, d = http("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                None, {"x-goog-api-key": GEMINI_KEY})
    if s != 200:
        return None
    malos = ("lite", "image", "tts", "audio", "live", "embedding", "exp", "robotics")
    buenos = []
    for m in d.get("models", []):
        n = m.get("name", "").replace("models/", "")
        if "generateContent" in m.get("supportedGenerationMethods", []) \
                and "flash" in n and not any(x in n for x in malos):
            buenos.append(n)
    buenos.sort(key=lambda n: ("preview" not in n, n))
    return buenos[-1] if buenos else None


def gemini(texto, solo_json=False, intentos=5):
    global _MODELO, _BUSCADO
    for i in range(intentos):
        modelo = _MODELO or CONFIG.get("modelo") or "gemini-flash-latest"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        datos = {"contents": [{"role": "user", "parts": [{"text": texto}]}],
                 "generationConfig": {"temperature": 0.7}}
        if solo_json:
            datos["generationConfig"]["responseMimeType"] = "application/json"
        s, d = http("POST", url, datos, {"x-goog-api-key": GEMINI_KEY}, espera=300)
        if s == 200:
            try:
                partes = d["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in partes if not p.get("thought"))
            except Exception:
                raise RuntimeError("Gemini no devolvió respuesta. Prueba otra vez.")
        if s in (400, 404) and not _BUSCADO:
            _BUSCADO = True
            _MODELO = buscar_modelo()
            continue
        if s in (0, 429, 500, 503):
            time.sleep(25 * (i + 1))
            continue
        raise RuntimeError(f"Gemini dio un error ({s}). Revisa la clave GEMINI_API_KEY.")
    raise RuntimeError("Gemini está saturado o se acabó el cupo gratis de hoy. "
                       "Lo intentaré en la próxima vuelta si me lo vuelves a pedir.")


def a_json(txt):
    t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", txt.strip())
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"[\{\[].*[\}\]]", t, re.S)
        if not m:
            raise RuntimeError("Gemini respondió algo que no entiendo.")
        return json.loads(m.group(0))


def quitar_vallas(txt):
    t = txt.strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", t, re.S)
    return m.group(1) if m else t


# ---------------------------------------------------------------- Telegram
def tg(metodo, datos):
    return http("POST", f"https://api.telegram.org/bot{TG_TOKEN}/{metodo}", datos, espera=60)[1]


def enviar(texto, botones=None):
    texto = texto or "(vacío)"
    trozos = [texto[i:i + 3800] for i in range(0, len(texto), 3800)]
    for n, trozo in enumerate(trozos):
        datos = {"chat_id": TG_CHAT, "text": trozo, "disable_web_page_preview": True}
        if botones and n == len(trozos) - 1:
            datos["reply_markup"] = botones
        tg("sendMessage", datos)


def pedir_permiso(est, propuesta):
    pid = uuid.uuid4().hex[:10]
    est["pendientes"][pid] = propuesta
    tipo = "🆕 WEB NUEVA" if propuesta["tipo"] == "crear_web" else "🔧 MEJORA"
    donde = f"\n📁 {propuesta.get('repo')}" if propuesta.get("repo") else ""
    enviar(f"{tipo}{donde}\n\n{propuesta.get('resumen', '')}\n\n¿Lo hago?",
           {"inline_keyboard": [[
               {"text": "✅ Sí, hazlo", "callback_data": "si:" + pid},
               {"text": "❌ No", "callback_data": "no:" + pid}]]})


# ---------------------------------------------------------------- el trabajo
def empresa_txt():
    e = CONFIG.get("empresa", {})
    return (f"Empresa propietaria: {e.get('nombre', '')}. Dirección: {e.get('direccion', '')}. "
            f"Correo de contacto: {e.get('correo', '')}.")


REGLAS = """Eres el director digital de un negocio online. Objetivo: facturar más.
Reglas: todo en español sencillo; webs rápidas, bonitas y pensadas primero para móvil;
nunca inventes precios, datos ni opiniones de clientes; nunca pongas contraseñas ni claves
en el código; incluye siempre aviso legal y privacidad en el pie."""


def texto_webs(webs):
    if not webs:
        return "(todavía no hay webs)"
    return "\n".join(f"- {w['repo']}: {w['descripcion']} ({w['url']})" for w in webs)


def nombre_limpio(nombre):
    n = re.sub(r"[^a-z0-9-]+", "-", (nombre or "web-nueva").lower()).strip("-")
    return (n or "web-nueva")[:60]


def crear_web(p):
    nombre = nombre_limpio(p.get("nombre_repo"))
    s, _ = gh("GET", f"/repos/{dueno()}/{nombre}")
    if s == 200:
        nombre = f"{nombre}-{datetime.now().strftime('%m%d%H%M')}"
    pedido = f"""{REGLAS}
{empresa_txt()}

Crea una web completa y lista para publicar en GitHub Pages.
Encargo: {p.get('instrucciones') or p.get('resumen')}

Devuelve SOLO un JSON así:
{{"archivos": [{{"ruta": "index.html", "contenido": "..."}}]}}
Como máximo 6 archivos. Obligatorio index.html. Sin imágenes binarias (usa emojis, SVG o CSS).
Incluye aviso-legal.html y privacidad.html con los datos de la empresa."""
    res = a_json(gemini(pedido, solo_json=True))
    archivos = [a for a in res.get("archivos", [])
                if isinstance(a, dict) and a.get("ruta") and a.get("contenido")]
    if not any(a["ruta"] == "index.html" for a in archivos):
        raise RuntimeError("Gemini no hizo la página principal. Dale a /ideas o pídemelo otra vez.")
    s, d = gh("POST", "/user/repos", {"name": nombre, "private": False, "auto_init": True,
                                      "description": (p.get("resumen") or "")[:300]})
    if s != 201:
        raise RuntimeError(f"No pude crear el repositorio: {str(d)[:200]}")
    repo, rama = d["full_name"], d.get("default_branch", "main")
    time.sleep(3)
    for a in archivos[:6]:
        ruta = a["ruta"].lstrip("/")
        if ".." in ruta or ruta.startswith(".github"):
            continue
        escribir(repo, ruta, a["contenido"], "Creado por el motor")
    for _ in range(4):
        s, _ = gh("POST", f"/repos/{repo}/pages", {"source": {"branch": rama, "path": "/"}})
        if s in (201, 409):
            break
        time.sleep(6)
    url = f"https://{dueno().lower()}.github.io/{nombre}/"
    gh("PATCH", f"/repos/{repo}", {"homepage": url})
    return (f"✅ Web creada: {nombre}\n🌐 {url}\n(tarda 1-2 minutos en verse la primera vez)\n\n"
            "Si tiene dominio propio en Cloudflare, dímelo y lo conectamos.")


def elegir_archivo(repo, instrucciones):
    archivos = [a for a in lista_archivos(repo)
                if a.endswith((".html", ".css", ".js", ".md", ".json")) and not a.startswith(".github")]
    if not archivos:
        return None
    if "index.html" in archivos and len(archivos) == 1:
        return "index.html"
    pedido = (f"Archivos de la web:\n" + "\n".join(archivos[:300]) +
              f"\n\nEncargo: {instrucciones}\n"
              "¿Qué UN archivo hay que cambiar? Responde SOLO un JSON: {\"archivo\": \"ruta\"}")
    elegido = a_json(gemini(pedido, solo_json=True)).get("archivo")
    return elegido if elegido in archivos else ("index.html" if "index.html" in archivos else None)


def mejorar_web(p, est):
    repo = repo_completo(p.get("repo"))
    archivo = p.get("archivo") or elegir_archivo(repo, p.get("instrucciones", ""))
    viejo, _ = leer(repo, archivo) if archivo else (None, None)
    if viejo is None:
        archivo = elegir_archivo(repo, p.get("instrucciones", ""))
        viejo, _ = leer(repo, archivo) if archivo else (None, None)
    if viejo is None:
        raise RuntimeError(f"No encontré qué archivo cambiar en {repo}. Dime el nombre del archivo.")
    if len(viejo) > 180000:
        raise RuntimeError(f"{archivo} es muy grande para cambiarlo entero de una vez. "
                           "Pídeme el cambio por partes (por ejemplo, solo la portada).")
    pedido = f"""{REGLAS}
{empresa_txt()}

Este es el archivo "{archivo}" de la web "{repo}":
=====
{viejo}
=====
Cambio que hay que hacer: {p.get('instrucciones') or p.get('resumen')}

Devuelve SOLO el archivo completo ya cambiado, de principio a fin, sin explicaciones.
No quites nada que funcione y no tenga que ver con el cambio."""
    nuevo = quitar_vallas(gemini(pedido))
    if len(nuevo) < len(viejo) * 0.6:
        raise RuntimeError("El resultado salió cortado, así que no lo he publicado para no romper "
                           "la web. Pídemelo otra vez o con un cambio más pequeño.")
    est["deshacer"].append({"repo": repo, "archivo": archivo, "contenido": viejo})
    escribir(repo, archivo, nuevo, f"Mejora del motor: {(p.get('resumen') or '')[:60]}")
    return f"✅ Hecho en {repo} ({archivo}).\nSi no te gusta, escribe /deshacer"


def deshacer(est):
    if not est["deshacer"]:
        return "No hay nada que deshacer."
    u = est["deshacer"].pop()
    escribir(u["repo"], u["archivo"], u["contenido"], "Deshecho por el motor")
    return f"↩️ Deshecho: {u['repo']} ({u['archivo']}) vuelve a estar como antes."


def ejecutar(p, est):
    if p["tipo"] == "crear_web":
        r = crear_web(p)
    elif p["tipo"] == "mejorar_web":
        r = mejorar_web(p, est)
    else:
        r = "No sé hacer ese tipo de tarea todavía."
    est["hecho"].append({"fecha": datetime.now(timezone.utc).isoformat()[:16],
                         "resumen": p.get("resumen", "")[:200]})
    return r


# ---------------------------------------------------------------- ideas diarias
def ideas(est):
    webs = mis_webs()
    hecho = "\n".join(f"- {h['resumen']}" for h in est["hecho"][-20:]) or "(nada aún)"
    n = int(CONFIG.get("ideas_por_dia", 4))
    pedido = f"""{REGLAS}

Mis webs:
{texto_webs(webs)}

Lo que ya se hizo hace poco (no lo repitas):
{hecho}

Propón {n} ideas concretas para ganar más dinero, ordenadas de más a menos dinero.
Como máximo UNA puede ser una web nueva; el resto, mejoras de webs que ya existen.
Cada idea debe poder hacerse cambiando un archivo o creando una web pequeña.
Devuelve SOLO un JSON: lista de objetos con
"tipo": "mejorar_web" o "crear_web",
"repo": nombre exacto del repositorio (solo si es mejora),
"nombre_repo": nombre corto en minúsculas con guiones (solo si es web nueva),
"resumen": 2-3 frases sencillas: qué es y por qué dará dinero,
"instrucciones": encargo detallado para quien lo programe."""
    lista = a_json(gemini(pedido, solo_json=True))
    if isinstance(lista, dict):
        lista = lista.get("ideas", [])
    nombres = {w["repo"] for w in webs}
    enviar(f"☀️ Buenos días. Hoy te propongo {len(lista[:n])} ideas:")
    for idea in lista[:n]:
        if idea.get("tipo") == "mejorar_web" and idea.get("repo") not in nombres:
            continue
        if idea.get("tipo") in ("mejorar_web", "crear_web"):
            pedir_permiso(est, idea)
    if MOTOR_REPO:  # señal de vida para que GitHub no apague el motor
        escribir(MOTOR_REPO, "latido.txt", datetime.now(timezone.utc).isoformat(), "latido")


# ---------------------------------------------------------------- tus mensajes
AYUDA = """🤖 Tu motor está activo.

Escríbeme normal, por ejemplo:
• "Crea una web de recetas fáciles con afiliados"
• "En sports-edge pon un botón grande de suscribirse"
• "¿Qué mejorarías en mis tiendas?"

Órdenes rápidas:
/ideas – te propongo ideas ahora
/webs – lista de tus webs
/deshacer – quito el último cambio
/ayuda – este mensaje

Reviso tus mensajes cada 10 minutos más o menos.
Nada se publica sin que pulses ✅."""


def entender(texto, est):
    webs = mis_webs()
    charla = "\n".join(f"{c['quien']}: {c['texto']}" for c in est["charla"][-10:])
    pedido = f"""{REGLAS}

Mis webs:
{texto_webs(webs)}

Conversación reciente:
{charla}

Nuevo mensaje del dueño: {texto}

Decide qué hacer y devuelve SOLO un JSON con:
"tipo": "respuesta" (si solo hay que contestar, opinar o dar ideas),
        "crear_web" (si pide crear una web) o "mejorar_web" (si pide cambiar una web suya),
"respuesta": tu contestación en español sencillo (si tipo es respuesta),
"repo": nombre exacto del repositorio de la lista (si es mejora),
"archivo": archivo a cambiar si lo sabes (si no, vacío),
"nombre_repo": nombre corto en minúsculas con guiones (si es web nueva),
"resumen": 2-3 frases sencillas de lo que vas a hacer,
"instrucciones": encargo detallado para quien lo programe."""
    plan = a_json(gemini(pedido, solo_json=True))
    if isinstance(plan, list):
        plan = plan[0] if plan else {}
    tipo = plan.get("tipo")
    if tipo == "mejorar_web" and plan.get("repo") not in {w["repo"] for w in webs}:
        return "¿En cuál de tus webs lo hago? Escribe /webs para ver los nombres."
    if tipo in ("crear_web", "mejorar_web"):
        pedir_permiso(est, plan)
        return None
    return plan.get("respuesta") or "No te he entendido bien. ¿Me lo dices de otra forma?"


def escuchar(est):
    datos = tg("getUpdates", {"offset": est["offset"], "timeout": 0,
                              "allowed_updates": ["message", "callback_query"]})
    for u in datos.get("result", []):
        est["offset"] = u["update_id"] + 1
        try:
            if "callback_query" in u:
                c = u["callback_query"]
                if str(c["message"]["chat"]["id"]) != TG_CHAT:
                    continue
                tg("answerCallbackQuery", {"callback_query_id": c["id"]})
                tg("editMessageReplyMarkup", {"chat_id": TG_CHAT,
                                              "message_id": c["message"]["message_id"],
                                              "reply_markup": {"inline_keyboard": []}})
                orden, _, pid = c.get("data", "").partition(":")
                p = est["pendientes"].pop(pid, None)
                if not p:
                    enviar("Esa propuesta ya no está disponible.")
                elif orden == "si":
                    enviar("⚙️ Manos a la obra. Te aviso al terminar…")
                    enviar(ejecutar(p, est))
                else:
                    enviar("👌 Descartado.")
                continue
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) != TG_CHAT or not m.get("text"):
                continue
            texto = m["text"].strip()
            orden = texto.split()[0].split("@")[0].lower()
            if orden in ("/start", "/ayuda", "/help"):
                enviar(AYUDA)
            elif orden == "/ideas":
                enviar("💡 Pensando ideas…")
                ideas(est)
            elif orden == "/webs":
                enviar("🌐 Tus webs:\n" + texto_webs(mis_webs()))
            elif orden == "/deshacer":
                enviar(deshacer(est))
            else:
                est["charla"].append({"quien": "Dueño", "texto": texto[:1500]})
                r = entender(texto, est)
                if r:
                    est["charla"].append({"quien": "Motor", "texto": r[:1500]})
                    enviar(r)
        except Exception as e:
            enviar(f"⚠️ {e}")


# ---------------------------------------------------------------- arranque
def main():
    faltan = [n for n, v in (("GEMINI_API_KEY", GEMINI_KEY), ("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                             ("TELEGRAM_CHAT_ID", TG_CHAT), ("GH_TOKEN", GH_TOKEN)) if not v]
    if faltan:
        print("Faltan estas claves en Secrets:", ", ".join(faltan))
        sys.exit(1)
    accion = (sys.argv[1] if len(sys.argv) > 1 else "escuchar").strip().lower()
    est, original = cargar_estado()
    try:
        if accion == "ideas":
            ideas(est)
        else:
            escuchar(est)
    except Exception as e:
        enviar(f"⚠️ {e}")
    finally:
        guardar_estado(est, original)
    print("Vuelta terminada.")


if __name__ == "__main__":
    main()
