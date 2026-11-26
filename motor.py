#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YARVIS  (versión 3)
- Cerebro: Gemini.  Casa: GitHub.  Mando: tu bot de Telegram.  Panel: mi-panel.
- Secciones: Negocios, Cuentas, Bienestar, Redes.
- Nada se publica sin tu "Sí".
No hace falta tocar este archivo.
"""
import base64
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("Europe/Madrid")
except Exception:
    ZONA = timezone(timedelta(hours=1))

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
PANEL_CLAVE = os.environ.get("PANEL_CLAVE", "").strip()
STRIPE_KEY = os.environ.get("STRIPE_KEY", "").strip()
MOTOR_REPO = os.environ.get("GITHUB_REPOSITORY", "")
NOMBRE_MEMORIA = "motor-memoria"
NOMBRE_PANEL = "mi-panel"

AQUI = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(AQUI, "config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)


def ahora():
    return datetime.now(ZONA)


def hoy():
    return ahora().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- conexiones
def http(metodo, url, datos=None, cabeceras=None, espera=120, formulario=False):
    cuerpo = None
    h = dict(cabeceras or {})
    if datos is not None:
        if formulario:
            cuerpo = urllib.parse.urlencode(datos).encode("utf-8")
        else:
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
    except Exception as e:
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


def lista_archivos(repo):
    s, d = gh("GET", f"/repos/{repo}")
    if s != 200:
        return []
    rama = d.get("default_branch", "main")
    s, d = gh("GET", f"/repos/{repo}/git/trees/{rama}?recursive=1")
    if s != 200:
        return []
    return [x["path"] for x in d.get("tree", []) if x.get("type") == "blob"]


def activar_pages(repo, rama="main"):
    for _ in range(4):
        s, _ = gh("POST", f"/repos/{repo}/pages", {"source": {"branch": rama, "path": "/"}})
        if s in (201, 409):
            return
        time.sleep(6)


_WEBS = None


def mis_webs():
    """Todas tus webs: cada repositorio tuyo que tenga GitHub Pages."""
    global _WEBS
    if _WEBS is not None:
        return _WEBS
    fuera = {NOMBRE_MEMORIA, NOMBRE_PANEL, MOTOR_REPO.split("/")[-1]}
    webs, pagina = [], 1
    while pagina < 10:
        s, d = gh("GET", f"/user/repos?per_page=100&page={pagina}&affiliation=owner")
        if s != 200 or not d:
            break
        for r in d:
            if r["name"] in fuera or r.get("archived") or not r.get("has_pages"):
                continue
            webs.append({
                "repo": r["name"],
                "descripcion": r.get("description") or "",
                "url": r.get("homepage") or
                f"https://{r['owner']['login'].lower()}.github.io/{r['name']}/",
            })
        pagina += 1
    _WEBS = webs
    return webs


# ---------------------------------------------------------------- memoria privada
def memoria():
    repo = f"{dueno()}/{NOMBRE_MEMORIA}"
    s, _ = gh("GET", f"/repos/{repo}")
    if s == 404:
        gh("POST", "/user/repos", {"name": NOMBRE_MEMORIA, "private": True, "auto_init": True,
                                   "description": "Memoria privada del motor"})
        time.sleep(4)
    return repo


def cargar_estado():
    txt, _ = leer(memoria(), "estado.json")
    est = json.loads(txt) if txt else {}
    for clave, valor in (("offset", 0), ("pendientes", {}), ("deshacer", []), ("charla", []),
                         ("hecho", []), ("apuntes", []), ("objetivo", 0), ("bienestar", []),
                         ("redes", []), ("redes_pend", {}), ("panel_hash", ""), ("aprobadas", {})):
        est.setdefault(clave, valor)
    return est, (txt or "")


def guardar_estado(est, original):
    est["pendientes"] = dict(list(est["pendientes"].items())[-40:])
    est["redes_pend"] = dict(list(est["redes_pend"].items())[-10:])
    est["deshacer"] = est["deshacer"][-5:]
    est["charla"] = est["charla"][-12:]
    est["hecho"] = est["hecho"][-60:]
    est["apuntes"] = est["apuntes"][-1500:]
    est["bienestar"] = est["bienestar"][-120:]
    est["redes"] = est["redes"][-60:]
    nuevo = json.dumps(est, ensure_ascii=False, indent=1)
    if nuevo != original:
        escribir(memoria(), "estado.json", nuevo, "memoria")
    return nuevo


# ---------------------------------------------------------------- Gemini
_MODELO = None
_BUSCADO = False


def buscar_modelo():
    s, d = http("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                None, {"x-goog-api-key": GEMINI_KEY})
    if s != 200:
        return None
    malos = ("lite", "image", "tts", "audio", "live", "embedding", "exp", "robotics")
    buenos = [m.get("name", "").replace("models/", "") for m in d.get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])]
    buenos = [n for n in buenos if "flash" in n and not any(x in n for x in malos)]
    buenos.sort(key=lambda n: ("preview" not in n, n))
    return buenos[-1] if buenos else None


class Ocupado(RuntimeError):
    """Gemini saturado: la tarea se guarda y se reintenta sola."""


MODELOS_RESPALDO = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite",
                    "gemini-2.0-flash-lite", "gemini-flash-lite-latest"]
_MALOS = set()


def lista_modelos():
    base = [_MODELO, CONFIG.get("modelo") or "gemini-flash-latest"] + MODELOS_RESPALDO
    vistos = []
    for m in base:
        if m and m not in vistos and m not in _MALOS:
            vistos.append(m)
    return vistos


def gemini(texto, solo_json=False, rondas=3, extra=None):
    """Prueba varios modelos gratis de Gemini (cada uno tiene su propio cupo diario)."""
    global _MODELO, _BUSCADO
    for ronda in range(rondas):
        for modelo in lista_modelos():
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
            datos = {"contents": [{"role": "user", "parts": (extra or []) + [{"text": texto}]}],
                     "generationConfig": {"temperature": 0.7}}
            if solo_json:
                datos["generationConfig"]["responseMimeType"] = "application/json"
            s, d = http("POST", url, datos, {"x-goog-api-key": GEMINI_KEY}, espera=240)
            if s == 200:
                try:
                    partes = d["candidates"][0]["content"]["parts"]
                    r = "".join(p.get("text", "") for p in partes if not p.get("thought"))
                except Exception:
                    r = ""
                if r.strip():
                    return r
                continue
            print("Aviso de Gemini:", modelo, s, str(d)[:150])
            if s in (401, 403) or (s == 400 and "API key" in str(d)):
                raise RuntimeError("La llave de Gemini (GEMINI_API_KEY) no funciona. Hay que renovarla.")
            if s in (400, 404):
                _MALOS.add(modelo)
                if not _BUSCADO:
                    _BUSCADO = True
                    _MODELO = buscar_modelo()
        time.sleep(30 * (ronda + 1))
    raise Ocupado("Gemini está saturado ahora mismo.")


# ---------------------------------------------------------------- VOZ
def bajar(url, espera=60):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=espera) as r:
        return r.read()


def transcribir(file_id, tipo="audio/ogg"):
    """Convierte tu nota de voz de Telegram en texto."""
    d = tg("getFile", {"file_id": file_id})
    ruta = (d.get("result") or {}).get("file_path")
    if not ruta:
        raise RuntimeError("No pude abrir tu nota de voz. Mándamela otra vez.")
    audio = bajar(f"https://api.telegram.org/file/bot{TG_TOKEN}/{ruta}")
    parte = [{"inline_data": {"mime_type": tipo, "data": base64.b64encode(audio).decode()}}]
    t = gemini("Transcribe exactamente lo que dice esta nota de voz, en español. "
               "Devuelve solo el texto, sin comillas ni explicaciones.", extra=parte)
    return t.strip()


VOCES_TTS = ["gemini-2.5-flash-preview-tts", "gemini-2.5-flash-tts", "gemini-2.5-pro-preview-tts"]


def subir_tg(metodo, campos, campo_archivo, nombre, contenido, tipo):
    limite = uuid.uuid4().hex
    cuerpo = b""
    for k, v in campos.items():
        cuerpo += (f"--{limite}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    cuerpo += (f"--{limite}\r\nContent-Disposition: form-data; name=\"{campo_archivo}\"; "
               f"filename=\"{nombre}\"\r\nContent-Type: {tipo}\r\n\r\n").encode() + contenido + b"\r\n"
    cuerpo += f"--{limite}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/{metodo}", data=cuerpo,
                                 headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def hablar(texto):
    """Te contesta con voz (además del texto). Si la voz falla, el texto ya te ha llegado."""
    import subprocess, tempfile
    limpio = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF]", "", texto or "")
    limpio = re.sub(r"https?://\S+", "el enlace que te he escrito", limpio)[:1800]
    if not limpio.strip():
        return
    for modelo in VOCES_TTS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        datos = {"contents": [{"parts": [{"text": "Lee en español de España, con voz cálida y segura: " + limpio}]}],
                 "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                     "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Charon"}}}}}
        s, d = http("POST", url, datos, {"x-goog-api-key": GEMINI_KEY}, espera=180)
        if s != 200:
            print("Aviso de voz:", modelo, s, str(d)[:120])
            continue
        try:
            pcm = base64.b64decode(d["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
            with tempfile.TemporaryDirectory() as tmp:
                ent, sal = os.path.join(tmp, "v.pcm"), os.path.join(tmp, "v.ogg")
                with open(ent, "wb") as f:
                    f.write(pcm)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000",
                                "-ac", "1", "-i", ent, "-c:a", "libopus", "-b:a", "32k", sal], check=True)
                with open(sal, "rb") as f:
                    subir_tg("sendVoice", {"chat_id": TG_CHAT}, "voice", "yarvis.ogg", f.read(), "audio/ogg")
            return
        except Exception as e:
            print("Aviso de voz:", str(e)[:150])


def a_json(txt):
    t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", txt.strip())
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"[\{\[].*[\}\]]", t, re.S)
        if not m:
            raise RuntimeError("Gemini respondió algo que no entiendo. Pídemelo otra vez.")
        return json.loads(m.group(0))


def quitar_vallas(txt):
    t = txt.strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", t, re.S)
    return m.group(1) if m else t


# ---------------------------------------------------------------- Telegram
def tg(metodo, datos):
    s, d = http("POST", f"https://api.telegram.org/bot{TG_TOKEN}/{metodo}", datos, espera=80)
    if s != 200:
        print("Aviso de Telegram:", metodo, s, str(d.get("description", ""))[:120])
    return d


def enviar(texto, botones=None):
    texto = texto or "(vacío)"
    trozos = [texto[i:i + 3800] for i in range(0, len(texto), 3800)]
    for n, trozo in enumerate(trozos):
        datos = {"chat_id": TG_CHAT, "text": trozo, "disable_web_page_preview": True}
        if botones and n == len(trozos) - 1:
            datos["reply_markup"] = botones
        tg("sendMessage", datos)


def fila(*pares):
    return [{"text": t, "callback_data": c} for t, c in pares]


def pedir_permiso(est, propuesta):
    pid = uuid.uuid4().hex[:10]
    propuesta["fecha"] = hoy()
    est["pendientes"][pid] = propuesta
    tipo = {"crear_web": "🆕 WEB NUEVA", "mejorar_motor": "🧬 MEJORA DE YARVIS"}.get(propuesta["tipo"], "🔧 MEJORA")
    donde = f"\n📁 {propuesta.get('repo')}" if propuesta.get("repo") else ""
    enviar(f"{tipo}{donde}\n\n{propuesta.get('resumen', '')}\n\n¿Lo hago?",
           {"inline_keyboard": [fila(("✅ Sí, hazlo", "si:" + pid), ("❌ No", "no:" + pid))]})


# ---------------------------------------------------------------- reglas comunes
def empresa_txt():
    e = CONFIG.get("empresa", {})
    return (f"Empresa propietaria: {e.get('nombre', '')}. Dirección: {e.get('direccion', '')}. "
            f"Correo de contacto: {e.get('correo', '')}. Teléfono: {e.get('telefono', '')}.")


def contexto():
    datos = CONFIG.get("dueno", {}).get("datos", []) + CONFIG.get("empresa", {}).get("notas", [])
    return "SOBRE EL DUEÑO Y SU EMPRESA (tenlo siempre en cuenta):\n" + "\n".join(f"- {x}" for x in datos)


REGLAS = """Eres el director digital de un negocio online. Objetivo: facturar más.
Reglas: todo en español sencillo; webs rápidas, bonitas y pensadas primero para móvil;
nunca inventes precios, datos ni opiniones de clientes; nunca pongas contraseñas ni claves
en el código; incluye siempre aviso legal y privacidad en el pie.
Antes de proponer una web nueva, mira la lista de webs: si ya existe una parecida, propón mejorarla."""


def texto_webs(webs):
    if not webs:
        return "(todavía no hay webs)"
    return "\n".join(f"- {w['repo']}: {w['descripcion']} ({w['url']})" for w in webs)


def nombre_limpio(nombre):
    n = re.sub(r"[^a-z0-9-]+", "-", (nombre or "web-nueva").lower()).strip("-")
    return (n or "web-nueva")[:60]


def numero(txt):
    m = re.search(r"-?\d+(?:[.,]\d+)?", txt or "")
    return float(m.group(0).replace(",", ".")) if m else None


def euros(n):
    return f"{n:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# ================================================================ NEGOCIOS
def crear_web(p):
    nombre = nombre_limpio(p.get("nombre_repo"))
    s, _ = gh("GET", f"/repos/{dueno()}/{nombre}")
    if s == 200:
        nombre = f"{nombre}-{ahora().strftime('%m%d%H%M')}"
    pedido = f"""{REGLAS}
{contexto()}
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
        raise RuntimeError("Gemini no terminó la página principal. Pídemelo otra vez.")
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
    activar_pages(repo, rama)
    url = f"https://{dueno().lower()}.github.io/{nombre}/"
    gh("PATCH", f"/repos/{repo}", {"homepage": url})
    return (f"✅ Web creada en un proyecto nuevo de GitHub: {nombre}\n🌐 {url}\n"
            "(tarda 1-2 minutos en verse la primera vez)")


def elegir_archivo(repo, instrucciones):
    archivos = [a for a in lista_archivos(repo)
                if a.endswith((".html", ".css", ".js", ".md", ".json")) and not a.startswith(".github")]
    if not archivos:
        return None
    if archivos == ["index.html"]:
        return "index.html"
    pedido = ("Archivos de la web:\n" + "\n".join(archivos[:300]) +
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
        raise RuntimeError(f"Dime el nombre del archivo que cambio en {repo} y lo hago.")
    if len(viejo) > 180000:
        raise RuntimeError(f"{archivo} es muy grande: pídeme el cambio por partes "
                           "(por ejemplo, solo la portada) y lo hago.")
    pedido = f"""{REGLAS}
{contexto()}
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
        raise RuntimeError("El resultado salió cortado y lo he guardado sin publicar para "
                           "proteger la web. Pídemelo otra vez o con un cambio más pequeño.")
    est["deshacer"].append({"repo": repo, "archivo": archivo, "contenido": viejo})
    escribir(repo, archivo, nuevo, f"Mejora del motor: {(p.get('resumen') or '')[:60]}")
    return (f"✅ Mejorada tu web {repo} (archivo {archivo}).\n"
            "En 1-2 minutos se ve el cambio. Si no te gusta, escribe /deshacer")


def mejorar_motor(p, est):
    """Yarvis se mejora a sí mismo, con copia de seguridad y comprobación antes de guardar."""
    viejo, _ = leer(MOTOR_REPO, "motor.py")
    if not viejo:
        raise RuntimeError("No encuentro mi propio código. Pídemelo otra vez en un rato.")
    pedido = f"""Eres un programador experto en Python. Este es el programa completo de un asistente
llamado Yarvis (motor.py), que funciona en GitHub Actions con Gemini, Telegram y GitHub:
=====
{viejo}
=====
Cambio que pide el dueño: {p.get('instrucciones') or p.get('resumen')}

Reglas: solo librería estándar de Python (y cryptography, ya instalada); no toques las claves;
mantén todo lo que ya funciona; textos para el dueño en español sencillo y positivo.
Devuelve SOLO el archivo motor.py completo ya cambiado, de principio a fin, sin explicaciones."""
    nuevo = quitar_vallas(gemini(pedido))
    if len(nuevo) < len(viejo) * 0.85 or "def main(" not in nuevo:
        raise RuntimeError("El cambio salió incompleto y no lo he aplicado, para no romperme. "
                           "Pídemelo otra vez, con un cambio más concreto.")
    try:
        compile(nuevo, "motor.py", "exec")
    except SyntaxError as e:
        raise RuntimeError(f"El cambio tenía un fallo (línea {e.lineno}) y no lo he aplicado. "
                           "Pídemelo otra vez y lo intento de nuevo.")
    est["deshacer"].append({"repo": MOTOR_REPO, "archivo": "motor.py", "contenido": viejo})
    escribir(MOTOR_REPO, "motor.py", nuevo, f"Yarvis se mejora: {(p.get('resumen') or '')[:60]}")
    return ("🧬 Me he mejorado. El cambio funcionará en mi próximo turno.\n"
            "Para aplicarlo ya escribe /reiniciar. Si algo no te gusta, escribe /deshacer.")


def deshacer(est):
    if not est["deshacer"]:
        return "No hay cambios recientes que deshacer."
    u = est["deshacer"].pop()
    escribir(u["repo"], u["archivo"], u["contenido"], "Deshecho por el motor")
    return f"↩️ Listo: {u['repo']} ({u['archivo']}) vuelve a estar como antes."


def url_de(repo, archivo=None):
    nombre = (repo or "").split("/")[-1]
    base = next((w["url"] for w in mis_webs() if w["repo"] == nombre),
                f"https://{dueno().lower()}.github.io/{nombre}/")
    if not base.endswith("/"):
        base += "/"
    if archivo and archivo != "index.html" and archivo.endswith(".html"):
        base += archivo
    return base


def captura(url):
    """Foto de cómo ha quedado la web, cuando GitHub ya la ha publicado."""
    try:
        time.sleep(110)
        web = f"{url}?v={int(time.time())}"
        foto = "https://s0.wp.com/mshots/v1/" + urllib.parse.quote(web, safe="") + "?w=1100&h=1600"
        http("GET", foto, espera=60)
        time.sleep(50)
        http("GET", foto, espera=60)
        tg("sendPhoto", {"chat_id": TG_CHAT, "photo": foto,
                         "caption": "📸 Así ha quedado. Pulsa «Ver cómo quedó» para verla en vivo."})
    except Exception as e:
        print("Aviso de captura:", str(e)[:150])


def mostrar_resultado(texto, url, deshacer_boton=True):
    teclas = []
    if url:
        teclas.append([{"text": "👀 Ver cómo quedó", "url": url}])
    if deshacer_boton:
        teclas.append(fila(("↩️ Deshacer este cambio", "dz:0")))
    enviar(texto, {"inline_keyboard": teclas} if teclas else None)
    if url:
        threading.Thread(target=captura, args=(url,), daemon=True).start()


def ejecutar(p, est):
    if p["tipo"] == "crear_web":
        r = crear_web(p)
        url = re.search(r"https://\S+", r)
        mostrar_resultado(r + "\n\n📝 " + (p.get("resumen") or ""), url.group(0) if url else None, False)
    elif p["tipo"] == "mejorar_motor":
        r = mejorar_motor(p, est)
        enviar(r)
    else:
        r = mejorar_web(p, est)
        u = est["deshacer"][-1] if est["deshacer"] else {}
        mostrar_resultado(r + "\n\n📝 Qué he hecho: " + (p.get("resumen") or ""),
                          url_de(p.get("repo"), u.get("archivo")))
    est["hecho"].append({"fecha": ahora().isoformat()[:16], "resumen": p.get("resumen", "")[:200],
                         "repo": p.get("repo") or p.get("nombre_repo", "")})
    return None


def ejecutar_aprobada(pid, p, est):
    ejecutar(p, est)
    est.setdefault("aprobadas", {}).pop(pid, None)


def reenviar_pendientes(est):
    if not est["pendientes"]:
        return "No hay ideas esperando respuesta. Escribe /ideas para pedir nuevas."
    enviar(f"📋 Ideas esperando tu respuesta: {len(est['pendientes'])}")
    for pid, p in list(est["pendientes"].items()):
        tipo = {"crear_web": "🆕 WEB NUEVA", "mejorar_motor": "🧬 MEJORA DE YARVIS"}.get(p.get("tipo"), "🔧 MEJORA")
        donde = f"\n📁 {p.get('repo') or p.get('nombre_repo')}" if (p.get("repo") or p.get("nombre_repo")) else ""
        enviar(f"{tipo}{donde}\n\n{p.get('resumen', '')}\n\n¿Lo hago?",
               {"inline_keyboard": [fila(("✅ Sí, hazlo", "si:" + pid), ("❌ No", "no:" + pid))]})
    return None


def ideas(est):
    webs = mis_webs()
    hecho = "\n".join(f"- {h['resumen']}" for h in est["hecho"][-20:]) or "(nada aún)"
    ya = "\n".join(f"- {p.get('resumen', '')}" for p in est["pendientes"].values()) or "(nada)"
    n = int(CONFIG.get("ideas_por_dia", 4))
    pedido = f"""{REGLAS}
{contexto()}

Mis webs:
{texto_webs(webs)}

Lo que ya se hizo hace poco (no lo repitas):
{hecho}

Ideas que ya están esperando respuesta (no las repitas):
{ya}

Propón {n} ideas concretas para ganar más dinero, ordenadas de más a menos dinero.
Como máximo UNA puede ser una web nueva, y solo si no existe ya una parecida.
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
    buenas = [i for i in lista[:n] if i.get("tipo") == "crear_web" or
              (i.get("tipo") == "mejorar_web" and i.get("repo") in nombres)]
    enviar(f"💡 Ideas de hoy para ganar más ({len(buenas)}):")
    for idea in buenas:
        pedir_permiso(est, idea)


# ================================================================ CUENTAS
def apuntar(est, clase, importe, concepto):
    est["apuntes"].append({"fecha": hoy(), "clase": clase, "importe": round(abs(importe), 2),
                           "concepto": (concepto or "").strip()[:120], "origen": "tú"})
    signo = "➕" if clase == "ingreso" else "➖"
    return f"{signo} Apuntado {clase}: {euros(abs(importe))} · {concepto or 'sin concepto'}\n\n" + resumen_cuentas(est)


def resumen_mes(est):
    mes = hoy()[:7]
    ing = sum(a["importe"] for a in est["apuntes"] if a["fecha"][:7] == mes and a["clase"] == "ingreso")
    gas = sum(a["importe"] for a in est["apuntes"] if a["fecha"][:7] == mes and a["clase"] == "gasto")
    stripe = est.get("stripe_mes", {}).get(mes, 0)
    ing_total = ing + stripe
    objetivo = float(est.get("objetivo") or 0)
    dia = ahora().day
    import calendar
    dias_mes = calendar.monthrange(ahora().year, ahora().month)[1]
    falta = max(objetivo - (ing_total - gas), 0) if objetivo else 0
    dias_restan = max(dias_mes - dia + 1, 1)
    return {"mes": mes, "ingresos": round(ing_total, 2), "ingresos_stripe": round(stripe, 2),
            "gastos": round(gas, 2), "beneficio": round(ing_total - gas, 2),
            "objetivo": objetivo, "falta": round(falta, 2),
            "por_dia": round(falta / dias_restan, 2) if objetivo else 0, "dias_restan": dias_restan}


def resumen_cuentas(est):
    r = resumen_mes(est)
    t = (f"📊 Cuentas de {r['mes']}\n"
         f"Ingresos: {euros(r['ingresos'])}" + (f" (Stripe {euros(r['ingresos_stripe'])})" if r['ingresos_stripe'] else "") +
         f"\nGastos: {euros(r['gastos'])}\nBeneficio: {euros(r['beneficio'])}")
    if r["objetivo"]:
        pct = min(100, int(100 * max(r["beneficio"], 0) / r["objetivo"]))
        t += (f"\n\n🎯 Objetivo: {euros(r['objetivo'])} ({pct}% conseguido)\n"
              f"Falta: {euros(r['falta'])} → unos {euros(r['por_dia'])} al día durante {r['dias_restan']} días")
    else:
        t += "\n\n🎯 Pon tu objetivo del mes así: /objetivo 3000"
    return t


def leer_stripe(est):
    """Lee tus cobros de Stripe (solo lectura): lo de este mes y el total desde el principio."""
    if not STRIPE_KEY:
        return
    mes_actual = hoy()[:7]
    total, por_mes, cobros, monedas, despues = 0.0, {}, 0, {}, None
    for _ in range(30):
        q = "limit=100" + (f"&starting_after={despues}" if despues else "")
        s, d = http("GET", "https://api.stripe.com/v1/charges?" + q, None,
                    {"Authorization": "Bearer " + STRIPE_KEY})
        if s != 200:
            print("Aviso de Stripe:", s, str(d)[:150])
            est["stripe_error"] = f"Stripe respondió {s}"
            return
        for c in d.get("data", []):
            if c.get("paid") and c.get("status") == "succeeded":
                neto = (c.get("amount", 0) - c.get("amount_refunded", 0)) / 100.0
                mon = (c.get("currency") or "eur").upper()
                monedas[mon] = round(monedas.get(mon, 0) + neto, 2)
                mes = datetime.fromtimestamp(c.get("created", 0), ZONA).strftime("%Y-%m")
                por_mes[mes] = round(por_mes.get(mes, 0) + neto, 2)
                total += neto
                cobros += 1
        if not d.get("has_more") or not d.get("data"):
            break
        despues = d["data"][-1]["id"]
    est.setdefault("stripe_mes", {})[mes_actual] = por_mes.get(mes_actual, 0)
    est["stripe_total"] = {"total": round(total, 2), "cobros": cobros, "monedas": monedas,
                           "por_mes": dict(sorted(por_mes.items())[-12:]), "fecha": ahora().isoformat()[:16]}
    est.pop("stripe_error", None)


def importe_monedas(monedas):
    if not monedas:
        return euros(0)
    return " + ".join(euros(v) if m == "EUR" else f"{v:,.2f} {m}".replace(",", "X").replace(".", ",").replace("X", ".")
                      for m, v in monedas.items())


def web_viva(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Yarvis"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status < 400
    except Exception:
        return False


def informe(est):
    """Cómo están todas tus webs y tiendas, sus visitas y lo que has facturado."""
    leer_stripe(est)
    filas, visitas_total, caidas = [], 0, []
    for w in mis_webs():
        repo = f"{dueno()}/{w['repo']}"
        s, t = gh("GET", f"/repos/{repo}/traffic/views")
        vistas = t.get("count", 0) if s == 200 else 0
        personas = t.get("uniques", 0) if s == 200 else 0
        s, info = gh("GET", f"/repos/{repo}")
        cambio = (info.get("pushed_at") or "")[:10] if s == 200 else ""
        origen = ""
        if vistas:
            s, refs = gh("GET", f"/repos/{repo}/traffic/popular/referrers")
            if s == 200 and refs:
                origen = ", ".join(r.get("referrer", "") for r in refs[:2])
        viva = web_viva(w["url"])
        if not viva:
            caidas.append(w["repo"])
        visitas_total += vistas
        filas.append((vistas, f"{'🟢' if viva else '🔴'} {w['repo']} — {vistas} visitas ({personas} personas)"
                              + (f" · llegan de {origen}" if origen else "")
                              + (f" · último cambio {cambio}" if cambio else "")))
    filas.sort(key=lambda x: -x[0])
    st = est.get("stripe_total")
    if STRIPE_KEY and st:
        mes = hoy()[:7]
        dinero = (f"💶 FACTURADO\nEste mes: {euros(st['por_mes'].get(mes, 0))}\n"
                  f"Total desde el principio: {importe_monedas(st['monedas'])} ({st['cobros']} cobros)")
    elif STRIPE_KEY:
        dinero = "💶 FACTURADO\nStripe no ha respondido ahora; lo vuelvo a mirar en un rato."
    else:
        dinero = "💶 FACTURADO\nPara verlo falta la llave STRIPE_KEY en GitHub."
    texto = (f"📊 ESTADO DE TUS NEGOCIOS ({ahora().strftime('%d/%m %H:%M')})\n\n{dinero}\n\n"
             f"👀 Visitas de las últimas 2 semanas: {visitas_total} en {len(filas)} webs\n"
             + (f"🔴 No abren ahora: {', '.join(caidas)}\n" if caidas else "✅ Todas las webs abren bien\n")
             + "\n" + "\n".join(f for _, f in filas))
    est["informe"] = {"fecha": ahora().isoformat()[:16], "visitas": visitas_total, "caidas": caidas,
                      "webs": len(filas)}
    return texto


# ================================================================ BIENESTAR
def preguntar_sueno():
    enviar("🌅 Buenos días. ¿Cuántas horas has dormido?",
           {"inline_keyboard": [fila(("5 o menos", "su:5"), ("6", "su:6"), ("7", "su:7"), ("8 o más", "su:8"))]})


def preguntar_estres():
    enviar("🌙 ¿Cómo ha ido el día? Marca tu nivel de estrés (1 = tranquilo, 5 = mucho):",
           {"inline_keyboard": [fila(("1", "es:1"), ("2", "es:2"), ("3", "es:3"), ("4", "es:4"), ("5", "es:5"))]})


def registrar_bienestar(est, campo, valor):
    dia = hoy()
    reg = next((b for b in est["bienestar"] if b["fecha"] == dia), None)
    if not reg:
        reg = {"fecha": dia}
        est["bienestar"].append(reg)
    reg[campo] = valor
    if campo == "sueno":
        return f"😴 Apuntado: {valor}{'+' if valor >= 8 else ''} horas. " + (
            "¡Buen descanso! Hoy rindes al máximo 💪" if valor >= 7 else
            "Hoy intenta acostarte un poco antes: el descanso también hace crecer el negocio 🙏")
    ultimos = [b.get("estres") for b in est["bienestar"][-3:] if b.get("estres")]
    if len(ultimos) == 3 and min(ultimos) >= 4:
        return ("🧘 Apuntado. Llevas tres días con mucho estrés. Mañana regálate un rato de paseo, "
                "oración o descanso sin pantallas. Si te pesa mucho, hablarlo con alguien de confianza ayuda.")
    return "🙏 Apuntado. Descansa bien, mañana seguimos construyendo."


def resumen_bienestar(est):
    sem = est["bienestar"][-7:]
    if not sem:
        return "🌿 Aún no hay datos. Cada mañana te pregunto el sueño y cada noche el estrés."
    s = [b["sueno"] for b in sem if b.get("sueno")]
    e = [b["estres"] for b in sem if b.get("estres")]
    t = "🌿 Tu semana\n"
    if s:
        t += f"Sueño medio: {sum(s) / len(s):.1f} horas\n"
    if e:
        t += f"Estrés medio: {sum(e) / len(e):.1f} de 5\n"
    return t


# ================================================================ REDES
def nueva_publicacion(est):
    marca = CONFIG.get("marca", {})
    hechas = "\n".join(f"- {r.get('texto', '')[:80]}" for r in est["redes"][-10:]) or "(ninguna)"
    pedido = f"""Eres el responsable de redes sociales de una marca personal.
Persona: {marca.get('persona', 'un emprendedor')}.
Historia: {marca.get('historia', '')}
Tono: {marca.get('tono', '')}
Escribe UNA publicación nueva para Instagram, TikTok y Facebook, en español, que inspire.
Incluye un versículo bíblico corto y real de la traducción Reina-Valera 1909, copiado con exactitud,
con su cita (libro capítulo:versículo). Si no estás seguro del texto exacto, elige otro que sí sepas.
No la repitas con estas recientes:
{hechas}
Devuelve SOLO un JSON con: "texto" (máx. 600 caracteres), "versiculo", "cita", "hashtags" (lista de 6),
"idea_foto" (cómo debe ser la foto o vídeo, en una frase)."""
    p = a_json(gemini(pedido, solo_json=True))
    if isinstance(p, list):
        p = p[0] if p else {}
    rid = uuid.uuid4().hex[:8]
    p["fecha"] = hoy()
    est["redes_pend"][rid] = p
    texto = (f"📱 PUBLICACIÓN PARA HOY\n\n{p.get('texto', '')}\n\n📖 \"{p.get('versiculo', '')}\" "
             f"({p.get('cita', '')}, RV1909)\n\n{' '.join(p.get('hashtags', []))}\n\n"
             f"📸 Foto: {p.get('idea_foto', '')}")
    enviar(texto, {"inline_keyboard": [fila(("✅ Guardar", "rs:" + rid), ("🔄 Otra", "ro:" + rid))]})


# ================================================================ PANEL (web + app)
def cifrar(texto):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    sal, iv = os.urandom(16), os.urandom(12)
    clave = hashlib.pbkdf2_hmac("sha256", PANEL_CLAVE.encode("utf-8"), sal, 200000, 32)
    datos = AESGCM(clave).encrypt(iv, texto.encode("utf-8"), None)
    b = lambda x: base64.b64encode(x).decode()
    return json.dumps({"v": 1, "sal": b(sal), "iv": b(iv), "datos": b(datos)})


def asegurar_panel():
    repo = f"{dueno()}/{NOMBRE_PANEL}"
    s, d = gh("GET", f"/repos/{repo}")
    nuevo = s == 404
    if nuevo:
        gh("POST", "/user/repos", {"name": NOMBRE_PANEL, "private": False, "auto_init": True,
                                   "description": "Mi panel privado (datos cifrados)"})
        time.sleep(4)
    carpeta = os.path.join(AQUI, "panel")
    if os.path.isdir(carpeta):
        for nombre in sorted(os.listdir(carpeta)):
            with open(os.path.join(carpeta, nombre), encoding="utf-8") as f:
                contenido = f.read()
            actual, _ = leer(repo, nombre)
            if actual != contenido:
                escribir(repo, nombre, contenido, "Panel actualizado por el motor")
    if nuevo:
        activar_pages(repo)
        enviar(f"📲 Tu panel está listo: https://{dueno().lower()}.github.io/{NOMBRE_PANEL}/\n"
               "Ábrelo en el móvil y pulsa «Añadir a pantalla de inicio» para tenerlo como app.")
    return repo


def publicar_panel(est):
    if not PANEL_CLAVE:
        return
    try:
        repo = asegurar_panel()
        s, me = http("GET", f"https://api.telegram.org/bot{TG_TOKEN}/getMe")
        bot = (me.get("result") or {}).get("username", "")
        mes = hoy()[:7]
        datos = {
            "bot": bot,
            "webs": mis_webs(),
            "pendientes": [{"tipo": p.get("tipo"), "repo": p.get("repo") or p.get("nombre_repo", ""),
                            "resumen": p.get("resumen", ""), "fecha": p.get("fecha", "")}
                           for p in est["pendientes"].values()],
            "hecho": est["hecho"][-30:][::-1],
            "cuentas": resumen_mes(est),
            "apuntes": [a for a in est["apuntes"] if a["fecha"][:7] == mes][-50:][::-1],
            "bienestar": est["bienestar"][-14:],
            "redes": est["redes"][-20:][::-1],
            "facturado_total": est.get("stripe_total", {}),
            "informe": est.get("informe", {}),
        }
        texto = json.dumps(datos, ensure_ascii=False, sort_keys=True)
        huella = hashlib.sha256(texto.encode()).hexdigest()
        if huella == est.get("panel_hash"):
            return
        datos["actualizado"] = ahora().strftime("%d/%m/%Y %H:%M")
        escribir(repo, "datos.json", cifrar(json.dumps(datos, ensure_ascii=False)), "datos")
        est["panel_hash"] = huella
    except Exception as e:
        print("Aviso del panel:", str(e)[:200])


# ================================================================ TUS MENSAJES
AYUDA = """🤖 Tu motor está activo.

Háblame normal, escribiendo o con notas de voz, por ejemplo:
• "Mejora la portada de sports-edge"
• "Crea una web de recetas fáciles con afiliados"
• "He vendido 120 € en DAZMOND" o "He pagado 15 € del dominio"
• "¿Cómo van mis tiendas y cuánto he facturado?"

📂 NEGOCIOS
/ideas – ideas para ganar más
/pendientes – vuelvo a enseñarte las ideas sin responder
/webs – tus webs
/estado – cómo van todas tus webs y tiendas, visitas y lo facturado
/deshacer – quito el último cambio
/reiniciar – aplico al momento mis mejoras

💶 CUENTAS
/ingreso 120 venta tienda
/gasto 15 dominio
/objetivo 3000 – tu meta de beneficio del mes
/cuentas – cómo vas

🌿 BIENESTAR
Cada mañana te pregunto el sueño y cada noche el estrés.
/bienestar – tu semana

📱 REDES
/post – una publicación nueva ahora
Cada mañana te preparo una.

🎙️ VOZ
Mándame notas de voz y te contesto con voz y por escrito.
/voz – activa o quita que te conteste siempre con voz

📲 /panel – enlace a tu panel (web y app)

Te contesto en un minuto como mucho.
Nada se publica sin que pulses ✅."""


def entender(texto, est):
    webs = mis_webs()
    charla = "\n".join(f"{c['quien']}: {c['texto']}" for c in est["charla"][-10:])
    pedido = f"""{REGLAS}
{contexto()}

Mis webs (nombre exacto del repositorio: descripción):
{texto_webs(webs)}

Conversación reciente:
{charla}

Nuevo mensaje del dueño: {texto}

Decide qué hacer y devuelve SOLO un JSON con:
"tipo": "respuesta" (contestar, opinar, dar ideas o aclarar),
        "crear_web" (pide crear una web que no existe),
        "mejorar_web" (pide cambiar una web suya; si dice que una web ya existe, usa esa),
        "apunte" (cuenta un ingreso o un gasto de dinero),
        "mejorar_motor" (pide cambiar o mejorar a Yarvis mismo: cómo contesta, qué hace, nuevas funciones),
        "informe" (pregunta cómo van sus webs o tiendas, visitas, ventas, cuánto ha facturado o el estado del negocio),
"respuesta": tu contestación en español sencillo y positivo (si tipo es respuesta),
"repo": nombre exacto del repositorio de la lista (si es mejora),
"archivo": archivo a cambiar si lo sabes (si no, vacío),
"nombre_repo": nombre corto en minúsculas con guiones (si es web nueva),
"clase": "ingreso" o "gasto" (si es apunte), "importe": número (si es apunte), "concepto": texto corto,
"resumen": 2-3 frases sencillas de lo que vas a hacer,
"instrucciones": encargo detallado para quien lo programe."""
    plan = a_json(gemini(pedido, solo_json=True))
    if isinstance(plan, list):
        plan = plan[0] if plan else {}
    tipo = plan.get("tipo")
    if tipo == "informe":
        return informe(est)
    if tipo == "apunte" and plan.get("importe"):
        return apuntar(est, "gasto" if plan.get("clase") == "gasto" else "ingreso",
                       float(plan["importe"]), plan.get("concepto", ""))
    if tipo == "mejorar_web" and plan.get("repo") not in {w["repo"] for w in webs}:
        return "¿En cuál de tus webs lo hago? Escribe /webs para ver los nombres."
    if tipo in ("crear_web", "mejorar_web", "mejorar_motor"):
        pedir_permiso(est, plan)
        return None
    return plan.get("respuesta") or "Cuéntamelo con otras palabras y me pongo con ello."


# ---------------------------------------------------------------- trabajos largos en segundo plano
COLA = queue.Queue()
REINICIAR = False


def en_cola(est, aviso, tarea, *args):
    """Contesta al momento y hace el trabajo largo detrás, sin dejar de escuchar."""
    pendientes = COLA.unfinished_tasks
    if aviso:
        enviar(aviso + (f"\n(Tengo {pendientes} tarea(s) delante, voy en orden)" if pendientes else ""))
    COLA.put((tarea, args, 0))


REINTENTOS = []  # (cuándo, tarea, args, intento)


def obrero():
    while True:
        tarea, args, n = COLA.get()
        try:
            r = tarea(*args)
            if isinstance(r, str) and r:
                enviar(r)
        except Ocupado:
            if n < 8:
                REINTENTOS.append((time.time() + 1200, tarea, args, n + 1))
                if n == 0:
                    enviar("⏳ Gemini (el cerebro gratis) está saturado ahora mismo.\n"
                           "Lo tengo apuntado y lo reintento yo solo cada 20 minutos. "
                           "Te aviso en cuanto esté hecho.")
            else:
                enviar("⏳ Gemini sigue saturado hoy. Lo dejo guardado y lo retomo en mi próximo turno.")
        except Exception as e:
            enviar(f"⚠️ {e}")
        finally:
            COLA.task_done()


def revisar_reintentos():
    ahora_s = time.time()
    for item in list(REINTENTOS):
        if item[0] <= ahora_s:
            REINTENTOS.remove(item)
            COLA.put((item[1], item[2], item[3]))


def responder(est, texto, voz=False):
    r = entender(texto, est)
    if r:
        est["charla"].append({"quien": "Motor", "texto": r[:1500]})
        if voz or est.get("voz"):
            enviar(r)
            hablar(r)
            return None
    return r


def escuchar_voz(est, file_id, tipo):
    texto = transcribir(file_id, tipo)
    if not texto:
        return "No he entendido la nota de voz. ¿Me la repites?"
    enviar(f"🎙️ He entendido: «{texto}»")
    est["charla"].append({"quien": "Dueño", "texto": texto[:1500]})
    return responder(est, texto, voz=True)


def boton(est, c):
    orden, _, dato = c.get("data", "").partition(":")
    aviso = {"si": "✅ Aprobado, me pongo con ello", "no": "❌ Descartada"}.get(orden, "👍")
    tg("answerCallbackQuery", {"callback_query_id": c["id"], "text": aviso})
    msg = c["message"]
    if orden in ("si", "no") and msg.get("text"):
        marca = ("\n\n✅ APROBADA — trabajando en ello. Te mando el resultado con el enlace."
                 if orden == "si" else "\n\n❌ DESCARTADA")
        tg("editMessageText", {"chat_id": TG_CHAT, "message_id": msg["message_id"],
                               "text": msg["text"][:3900] + marca})
    else:
        tg("editMessageReplyMarkup", {"chat_id": TG_CHAT, "message_id": msg["message_id"],
                                      "reply_markup": {"inline_keyboard": []}})
    if orden in ("si", "no"):
        p = est["pendientes"].pop(dato, None)
        if not p:
            enviar("Esa idea ya estaba atendida. Escribe /pendientes para ver las que quedan.")
        elif orden == "si":
            est.setdefault("aprobadas", {})[dato] = p
            en_cola(est, "⚙️ Manos a la obra. Cuando termine te mando el enlace y una foto de cómo quedó.",
                    ejecutar_aprobada, dato, p, est)
        else:
            enviar("👌 Descartada. No la vuelvo a proponer.")
    elif orden == "dz":
        enviar(deshacer(est))
    elif orden == "su":
        enviar(registrar_bienestar(est, "sueno", int(dato)))
    elif orden == "es":
        enviar(registrar_bienestar(est, "estres", int(dato)))
    elif orden == "rs":
        p = est["redes_pend"].pop(dato, None)
        if p:
            est["redes"].append(p)
            enviar("✅ Guardada en tu panel, sección Redes, lista para copiar y publicar.")
    elif orden == "ro":
        est["redes_pend"].pop(dato, None)
        en_cola(est, "🔄 Preparando otra…", nueva_publicacion, est)


def escuchar(est, espera=0):
    datos = tg("getUpdates", {"offset": est["offset"], "timeout": espera,
                              "allowed_updates": ["message", "callback_query"]})
    for u in datos.get("result", []):
        est["offset"] = u["update_id"] + 1
        try:
            if "callback_query" in u:
                c = u["callback_query"]
                if str(c["message"]["chat"]["id"]) == TG_CHAT:
                    boton(est, c)
                continue
            m = u.get("message") or {}
            if str(m.get("chat", {}).get("id")) != TG_CHAT:
                continue
            nota = m.get("voice") or m.get("audio")
            if nota:
                en_cola(est, "🎧 Te escucho…", escuchar_voz, est, nota["file_id"],
                        nota.get("mime_type") or "audio/ogg")
                continue
            if not m.get("text"):
                continue
            texto = m["text"].strip()
            partes = texto.split(maxsplit=1)
            orden = partes[0].split("@")[0].lower()
            resto = partes[1] if len(partes) > 1 else ""
            if orden in ("/start", "/ayuda", "/help"):
                enviar(AYUDA)
            elif orden == "/ideas":
                en_cola(est, "💡 Pensando ideas… (tardo unos minutos)", ideas, est)
            elif orden == "/pendientes":
                r = reenviar_pendientes(est)
                if r:
                    enviar(r)
            elif orden == "/webs":
                enviar("🌐 Tus webs:\n" + texto_webs(mis_webs()))
            elif orden == "/reiniciar":
                global REINICIAR
                REINICIAR = True
                enviar("🔄 Me reinicio para aplicar mis cambios. Vuelvo en 1-2 minutos.")
            elif orden == "/deshacer":
                enviar(deshacer(est))
            elif orden in ("/ingreso", "/gasto"):
                n = numero(resto)
                if n is None:
                    enviar(f"Escríbelo así: {orden} 120 concepto")
                else:
                    concepto = re.sub(r"-?\d+(?:[.,]\d+)?\s*(€|eur|euros)?", "", resto, count=1).strip()
                    enviar(apuntar(est, orden[1:], n, concepto))
            elif orden == "/objetivo":
                n = numero(resto)
                if n is None:
                    enviar("Escríbelo así: /objetivo 3000")
                else:
                    est["objetivo"] = abs(n)
                    enviar("🎯 Objetivo guardado.\n\n" + resumen_cuentas(est))
            elif orden in ("/estado", "/informe", "/tiendas"):
                en_cola(est, "📊 Revisando todas tus webs, visitas y Stripe… (1-2 minutos)", informe, est)
            elif orden == "/voz":
                est["voz"] = not est.get("voz")
                enviar("🔊 Desde ahora te contesto también con voz." if est["voz"]
                       else "✍️ Desde ahora te contesto solo por escrito (si me mandas voz, te respondo con voz).")
            elif orden == "/cuentas":
                enviar(resumen_cuentas(est))
            elif orden == "/bienestar":
                enviar(resumen_bienestar(est))
            elif orden == "/post":
                en_cola(est, "📱 Preparando tu publicación…", nueva_publicacion, est)
            elif orden == "/panel":
                enviar(f"📲 Tu panel: https://{dueno().lower()}.github.io/{NOMBRE_PANEL}/" if PANEL_CLAVE
                       else "📲 Para activar tu panel falta guardar la llave PANEL_CLAVE en GitHub.")
            else:
                est["charla"].append({"quien": "Dueño", "texto": texto[:1500]})
                en_cola(est, "🧠 Recibido. Lo estoy pensando…", responder, est, texto)
        except Exception as e:
            enviar(f"⚠️ {e}")


# ================================================================ ARRANQUE
def manana(est):
    preguntar_sueno()
    leer_stripe(est)
    try:
        enviar(informe(est))
    except Exception as e:
        print("Aviso del informe:", str(e)[:150])
    for tarea in (ideas, nueva_publicacion):
        try:
            tarea(est)
        except Exception as e:
            enviar(f"⚠️ {e}")
    if MOTOR_REPO:  # señal de vida para que GitHub mantenga el motor encendido
        escribir(MOTOR_REPO, "latido.txt", ahora().isoformat(), "latido")


def tareas_del_dia(est):
    h, dia = ahora(), hoy()
    marcas = est.setdefault("marcas", {})
    if h.hour >= 8 and marcas.get("manana") != dia:
        marcas["manana"] = dia
        en_cola(est, None, manana, est)
    if (h.hour, h.minute) >= (22, 30) and marcas.get("noche") != dia:
        marcas["noche"] = dia
        leer_stripe(est)
        preguntar_estres()


def relanzar():
    """Antes de acabar su turno, el motor se vuelve a encender a sí mismo."""
    if MOTOR_REPO:
        s, d = gh("POST", f"/repos/{MOTOR_REPO}/actions/workflows/motor.yml/dispatches",
                  {"ref": "main", "inputs": {"accion": "vigilar"}})
        print("Relanzado:", s)


def main():
    global _WEBS
    faltan = [n for n, v in (("GEMINI_API_KEY", GEMINI_KEY), ("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                             ("TELEGRAM_CHAT_ID", TG_CHAT), ("GH_TOKEN", GH_TOKEN)) if not v]
    if faltan:
        print("Faltan estas claves en Secrets:", ", ".join(faltan))
        sys.exit(1)
    global MOTOR_REPO
    if MOTOR_REPO:  # si el proyecto cambia de nombre en GitHub, Yarvis usa el nombre nuevo
        s, d = gh("GET", f"/repos/{MOTOR_REPO}")
        if s == 200 and d.get("full_name"):
            MOTOR_REPO = d["full_name"]
    accion = (sys.argv[1] if len(sys.argv) > 1 else "vigilar").strip().lower()
    est, original = cargar_estado()
    if accion in ("manana", "ideas"):
        est.setdefault("marcas", {})["manana"] = hoy()
        try:
            manana(est)
        except Exception as e:
            enviar(f"⚠️ {e}")
    threading.Thread(target=obrero, daemon=True).start()
    aprobadas = dict(est.get("aprobadas", {}))
    if aprobadas:
        enviar(f"♻️ Retomo {len(aprobadas)} tarea(s) que aprobaste y aún no estaban terminadas.")
        for pid, p in aprobadas.items():
            COLA.put((ejecutar_aprobada, (pid, p, est), 0))
    inicio, webs_hora = time.time(), time.time()
    try:
        # Turno de unas 5 horas: escucha Telegram al momento y hace las tareas del día.
        while time.time() - inicio < 5.25 * 3600 and not REINICIAR:
            try:
                if time.time() - webs_hora > 1800:
                    _WEBS, webs_hora = None, time.time()
                escuchar(est, espera=50)
                revisar_reintentos()
                tareas_del_dia(est)
                publicar_panel(est)
                original = guardar_estado(est, original)
            except Exception as e:
                print("Aviso:", str(e)[:200])
                time.sleep(10)
        fin = time.time() + 1500  # deja terminar lo que esté haciendo (máx. 25 min)
        while COLA.unfinished_tasks and time.time() < fin:
            escuchar(est, espera=20)
            original = guardar_estado(est, original)
    finally:
        try:
            publicar_panel(est)
            guardar_estado(est, original)
        finally:
            relanzar()
    print("Turno terminado.")


if __name__ == "__main__":
    main()
