from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from pydantic import BaseModel
from fastapi.middleware.wsgi import WSGIMiddleware
from auth_students import router as student_router

import sys
import os
import google.generativeai as genai

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = r"C:\Users\DANIEL\Desktop\APP"


from app import app as flask_app

import requests
import uuid
import subprocess
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from statistics import mean
from typing import Optional

# ✅ .env (TAVILY_API_KEY, GEMINI_API_KEY, OLLAMA_HOST opcional)
from dotenv import load_dotenv
load_dotenv()

N8N_BASE_URL = "https://n8n-lab-automation.onrender.com"

N8N_PRESTAR = f"{N8N_BASE_URL}/webhook/lab/prestamo"
N8N_ENTREGAR = f"{N8N_BASE_URL}/webhook/lab-entregar"
N8N_DEVOLVER = f"{N8N_BASE_URL}/webhook/lab/devolver"
N8N_LISTAR = f"{N8N_BASE_URL}/webhook/lab/listar"


def warmup_n8n():
    try:
        requests.get(N8N_BASE_URL, timeout=20)
        print("n8n despertado o ya activo")
    except Exception as e:
        print("No se pudo despertar n8n:", e)

def is_n8n_awake():
    try:
        r = requests.get(N8N_BASE_URL, timeout=10)
        return r.ok
    except Exception:
        return False

print("MAIN REAL CARGADO:", os.path.abspath(__file__))
# ===============================
# ✅ TIMEZONE (Render-safe)
# ===============================
TZ_NAME = os.getenv("TZ_NAME", "America/Cancun").strip()
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:
    TZ = ZoneInfo("UTC")

# (Opcional) Gemini
# import google.generativeai as genai

# ✅ TTS gratis local (Windows SAPI) (OPCIONAL)
# En Render (Linux) normalmente NO hay motor de voz, así que lo apagamos por defecto.
PYTTSX3_ENABLED = os.getenv("PYTTSX3_ENABLED", "0").strip() == "1"

pyttsx3 = None
engine = None

if PYTTSX3_ENABLED:
    try:
        import pyttsx3  # <-- solo importa si está habilitado
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        engine.setProperty("volume", 1.0)

        # Intentar escoger voz español (si existe)
        try:
            voices = engine.getProperty("voices")
            for v in voices:
                name = (getattr(v, "name", "") or "").lower()
                vid  = (getattr(v, "id", "") or "").lower()
                if "spanish" in name or "es_" in vid or "es-" in vid or "spanish" in vid:
                    engine.setProperty("voice", v.id)
                    break
        except Exception:
            pass

    except Exception as e:
        print("⚠️ pyttsx3 no disponible. TTS local desactivado:", e)
        pyttsx3 = None
        engine = None


# ✅ STT local (Whisper) (OPCIONAL / pesado)
# Importarlo al arranque puede tumbar Render por RAM/tiempo. Mejor lazy-load.
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "tiny").strip()
whisper_model = None

def get_whisper():
    global whisper_model
    if whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            whisper_model = WhisperModel(WHISPER_MODEL_NAME, device="cpu", compute_type="int8")
        except Exception as e:
            print("⚠️ Whisper no disponible:", e)
            whisper_model = None
    return whisper_model


# ===============================
# ✅ AGENDA / RECORDATORIOS (APSCHEDULER)
# ===============================
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
# ===============================
# ✅ Scheduler global (CRÍTICO)
# ===============================
scheduler = BackgroundScheduler(timezone=str(TZ))
scheduler.start()

from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    swagger_ui_parameters={"tryItOutEnabled": True},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================
# ====== CONFIG OLLAMA ======
# ==========================
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1").strip()
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434").strip()
OLLAMA_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"
OLLAMA_TAGS_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/tags"

MODEL = os.getenv("OLLAMA_MODEL", "phi3:mini").strip()

# ====================
# 🌐 Tavily (búsqueda web)
# ====================
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
def tavily_search(query: str, max_results: int = 5) -> dict:
    if not TAVILY_API_KEY:
        return {"ok": False, "error": "Falta TAVILY_API_KEY"}

    try:
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": True,
            "include_raw_content": False,
            "include_images": False,
        }

        r = requests.post(TAVILY_SEARCH_URL, json=payload, timeout=25)
        r.raise_for_status()

        data = r.json()
        return {
            "ok": True,
            "answer": data.get("answer", ""),
            "results": data.get("results", []) or []
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

def ask_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise Exception("Falta GEMINI_API_KEY")

    try:
        genai.configure(api_key=GEMINI_API_KEY)

        model = genai.GenerativeModel("gemini-1.5-flash")

        response = model.generate_content(prompt)

        if response and response.text:
            return response.text.strip()

        return "Spectra: No pude generar una respuesta."

    except Exception as e:
        print("❌ Error Gemini:", e)
        raise
    
# ====================
# 📅 n8n Webhook (Google Calendar)
# ====================
N8N_CAL_WEBHOOK = os.getenv("N8N_CAL_WEBHOOK", "http://localhost:5678/webhook/spectra-teams").strip()

# ====================
# 🗑️ n8n Webhooks (Delete Calendar)
# ====================
N8N_DEL_EXACT_WEBHOOK = os.getenv(
    "N8N_DEL_EXACT_WEBHOOK",
    "http://localhost:5678/webhook/spectra-delete-exact"
).strip()

N8N_DEL_ID_WEBHOOK = os.getenv(
    "N8N_DEL_ID_WEBHOOK",
    "http://localhost:5678/webhook/spectra-delete-id"
).strip()

DEFAULT_EVENT_MINUTES = int(os.getenv("DEFAULT_EVENT_MINUTES", "60"))


# ====================
# 🌐 Gemini (online) (OPCIONAL) - lazy import (Render-safe)
# ====================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
gemini_model = None

def get_gemini_model():
    global gemini_model
    if gemini_model is not None:
        return gemini_model
    if not GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel("gemini-1.5-flash")
        return gemini_model
    except Exception as e:
        print("⚠️ Gemini desactivado:", repr(e))
        gemini_model = None
        return None

ESP32_TTS_URL = "http://192.168.100.149/say"

TMP_DIR = "tmp_audio"
os.makedirs(TMP_DIR, exist_ok=True)

TTS_LAST_WAV = os.path.join(TMP_DIR, "tts_last.wav")

# ====================
# 🔥 Firebase RTDB (REST)
# ====================
FIREBASE_REST_BASE = os.getenv(
    "FIREBASE_REST_BASE",
    "https://sensores-6d2ce-default-rtdb.firebaseio.com"
).strip()

# =========================
# 📅 Calendar por voz/texto (Spectra -> n8n -> Google Calendar)
# =========================
def _dt_to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).isoformat()

def _parse_time_from_text(t: str):
    """
    Detecta horas tipo:
    - "a las 5"
    - "a las 5 pm"
    - "a las 17:30"
    - "para las 8:15 am"
    """
    m = re.search(r"(?:a\s+las?|para\s+las?)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", t)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ap = (m.group(3) or "").replace(".", "").lower()  # am/pm

    if ap == "pm" and hh < 12:
        hh += 12
    if ap == "am" and hh == 12:
        hh = 0

    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return hh, mm

def _parse_duration_minutes(t: str) -> int:
    """
    Duración:
    - "por 30 minutos"
    - "durante 2 horas"
    """
    m = re.search(r"(?:por|durante)\s+(\d{1,3})\s*(min|mins|minuto|minutos|hora|horas)\b", t)
    if not m:
        return DEFAULT_EVENT_MINUTES
    n = int(m.group(1))
    unit = m.group(2)
    if "hora" in unit:
        return max(1, min(n * 60, 24 * 60))
    return max(1, min(n, 24 * 60))

def parse_calendar_event_command(text: str):
    """
    Intención: crear evento.
    Soporta:
    - "agenda reunión mañana a las 5 pm por 30 minutos"
    - "programa evento hoy a las 18:00"
    - "crea evento 2026-02-19 15:00 por 60 minutos"
    """
    if not text:
        return None

    t = text.lower().strip()

    # Palabras clave para "crear evento"
    if not any(k in t for k in ["agenda", "agendar", "programa", "programar", "crea evento", "crear evento", "evento", "reunión", "reunion"]):
        return None

        # ✅ Título: toma lo que está ENTRE el comando y la parte de fecha/hora
    title = None
    mtitle = re.search(r"(?:agenda|agendar|programa|programar|crea evento|crear evento)\s+(.*)", t)
    if mtitle:
        raw = mtitle.group(1).strip()

        # corta cuando detecta indicadores de fecha/hora (sin borrar lo anterior)
        cut = re.split(r"\b(hoy|mañana|pasado\s+mañana|\d{4}-\d{2}-\d{2}|a\s+las?|para\s+las?)\b", raw, maxsplit=1)
        title = cut[0].strip(" ,;:-").strip()

    title = (title or "Evento Spectra").strip()[:80]

    duration_min = _parse_duration_minutes(t)

    now = datetime.now(TZ)

    # 1) Si viene ISO tipo 2026-02-19 15:00 o 2026-02-19T15:00
    miso = re.search(r"\b(20\d{2}-\d{2}-\d{2})[ tT](\d{2}):(\d{2})\b", t)
    if miso:
        ymd = miso.group(1)
        hh = int(miso.group(2))
        mm = int(miso.group(3))
        try:
            base = datetime.strptime(ymd, "%Y-%m-%d")
            start_dt = base.replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=TZ)
            end_dt = start_dt + timedelta(minutes=duration_min)
            return {
                "title": title,
                "start": _dt_to_iso(start_dt),
                "end": _dt_to_iso(end_dt),
            }
        except:
            pass

    # 2) Hoy / mañana + hora
    hhmm = _parse_time_from_text(t)
    if hhmm:
        hh, mm = hhmm
        day = now.date()
        if "mañana" in t:
            day = (now + timedelta(days=1)).date()
        # si no dice hoy/mañana, asumimos hoy
        start_dt = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
        end_dt = start_dt + timedelta(minutes=duration_min)
        return {
            "title": title,
            "start": _dt_to_iso(start_dt),
            "end": _dt_to_iso(end_dt),
        }

    # Si detectó intención pero no entendió fecha/hora: no crea nada (para no inventar)
    return {"error": "No pude entender la fecha/hora del evento. Di: 'mañana a las 5 pm' o '2026-02-19 15:00'."}

def crear_evento_calendar_via_n8n(title: str, start_iso: str, end_iso: str):
    payload = {"title": title, "start": start_iso, "end": end_iso}
    try:
        r = requests.post(N8N_CAL_WEBHOOK, json=payload, timeout=20)
        # n8n a veces devuelve texto/json; intentamos json primero
        try:
            return {"ok": r.ok, "status": r.status_code, "data": r.json()}
        except:
            return {"ok": r.ok, "status": r.status_code, "data": r.text}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def delete_event_exact_via_n8n(title_exact: str):
    payload = {"title_exact": title_exact}
    try:
        r = requests.post(N8N_DEL_EXACT_WEBHOOK, json=payload, timeout=20)
        try:
            data = r.json()
        except:
            data = {"raw": r.text}
        return {"ok": r.ok, "status": r.status_code, "data": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def delete_event_id_via_n8n(event_id: str):
    payload = {"event_id": event_id}
    try:
        r = requests.post(N8N_DEL_ID_WEBHOOK, json=payload, timeout=20)
        try:
            data = r.json()
        except:
            data = {"raw": r.text}
        return {"ok": r.ok, "status": r.status_code, "data": data}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    
# =========================
# 🗑️ DELETE Calendar: parser + resolver (Spectra -> n8n)
# =========================
def parse_delete_calendar_command(text: str):
    """
    Detecta intención de eliminar/cancelar un evento por voz.
    Devuelve dict o None.

    Devuelve:
    {
      "intent": "delete_calendar_event",
      "raw": "...",
      "target": "reunión de prueba",
      "event_id": ""  # opcional si detecta un id
    }
    """
    if not text:
        return None

    t = text.strip().lower()

    # verbo de borrar
    if not re.search(r"\b(elimina|eliminar|borra|borrar|cancela|cancelar|quita|quitar)\b", t):
        return None

    # si viene un ID explícito (ej: "borra id abc123")
    mid = re.search(r"\b(id|event id|event_id)\s*[:=]?\s*([A-Za-z0-9_\-]+)\b", t)
    if mid:
        return {
            "intent": "delete_calendar_event",
            "raw": text,
            "target": "",
            "event_id": mid.group(2).strip()
        }

    # extraer lo que viene después del verbo
    m = re.search(r"\b(elimina|eliminar|borra|borrar|cancela|cancelar|quita|quitar)\b\s+(.*)$", t)
    target = (m.group(2).strip() if m else "")

    # limpiar artículos / palabras genéricas
    target = re.sub(r"^(mi|la|el|un|una|este|esta)\s+", "", target)
    target = re.sub(r"\b(evento|reunión|reunion|cita|recordatorio|agenda|calendario)\b", "", target)
    target = target.strip(" ,;:-").strip()

    return {
        "intent": "delete_calendar_event",
        "raw": text,
        "target": target,
        "event_id": ""
    }


def resolve_delete_command_via_n8n(del_cmd: dict, chat_id: str = "default"):
    """
    Decide si borrar por ID o por título exacto.
    SIEMPRE retorna (answer: str, meta: dict)
    """
    del_cmd = del_cmd if isinstance(del_cmd, dict) else {}
    event_id = (del_cmd.get("event_id") or "").strip()
    target = (del_cmd.get("target") or "").strip()

    # 1) Si hay event_id, borra por ID
    if event_id:
        resp = delete_event_id_via_n8n(event_id)
        ok = bool(resp.get("ok"))
        meta = {"mode": "id", "event_id": event_id, "n8n": resp, "chat_id": chat_id}

        if ok:
            return (f"Listo, Daniel. Eliminé el evento con ID {event_id}.", meta)
        return ("Daniel, intenté eliminar por ID pero falló en n8n/Google Calendar. Revisa Executions.", meta)

    # 2) Si no hay target, pedirlo
    if not target:
        meta = {"mode": "missing_target", "delete_cmd": del_cmd, "chat_id": chat_id}
        return ("Daniel, dime el nombre exacto del evento que quieres eliminar.", meta)

    # 3) Borrar por título exacto
    resp = delete_event_exact_via_n8n(target)
    ok = bool(resp.get("ok"))
    meta = {"mode": "exact_title", "title_exact": target, "n8n": resp, "chat_id": chat_id}

    if ok:
        return (f"Listo, Daniel. Eliminé el evento: {target}.", meta)
    return ("Daniel, intenté eliminarlo pero falló en n8n/Google Calendar. Revisa Executions.", meta)    

# ===============================
# ✅ WebSocket: Core (speaker PC) (/ws)
# ===============================
ws_clients = set()

async def ws_broadcast(payload: dict):
    dead = []
    msg = json.dumps(payload, ensure_ascii=False)
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "hello", "msg": "ws conectado"}, ensure_ascii=False))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_clients.discard(websocket)
    except:
        ws_clients.discard(websocket)

# ===============================
# ✅ WebSocket: App Futurista (notificaciones) (/ws-app)
# ===============================
ws_app_clients = set()

async def ws_app_broadcast(payload: dict):
    dead = []
    msg = json.dumps(payload, ensure_ascii=False)
    for ws in ws_app_clients:
        try:
            await ws.send_text(msg)
        except:
            dead.append(ws)
    for ws in dead:
        ws_app_clients.discard(ws)

@app.websocket("/ws-app")
async def ws_app_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_app_clients.add(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "hello", "msg": "ws-app conectado"}, ensure_ascii=False))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_app_clients.discard(websocket)
    except:
        ws_app_clients.discard(websocket)

# ===============================
# ✅ Página SPEAKER para PC
# ===============================
@app.get("/speaker", response_class=HTMLResponse)
def speaker_page():
    return r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>PC Speaker</title>
</head>
<body style="font-family: Arial, sans-serif; padding: 16px;">
  <h2>🖥️ Speaker (PC)</h2>
  <p>1) Click en <b>Activar audio</b>. 2) Cuando llegue un mensaje tipo "talk", la PC lo lee.</p>

  <button id="btnEnable">Activar audio</button>
  <button id="btnStop">Stop</button>
  <p id="st">Estado: desconectado</p>

  <hr/>
  <h3>Transcripción</h3>
  <pre id="t" style="white-space:pre-wrap;"></pre>

  <h3>Respuesta</h3>
  <pre id="a" style="white-space:pre-wrap;"></pre>

<script>
let enabled = false;
const st = document.getElementById("st");
const t  = document.getElementById("t");
const a  = document.getElementById("a");

document.getElementById("btnEnable").onclick = () => {
  enabled = true;
  const u = new SpeechSynthesisUtterance("Audio activado");
  u.lang = "es-EC";
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
};

document.getElementById("btnStop").onclick = () => speechSynthesis.cancel();

function speak(text) {
  if (!enabled) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "es-EC";
  speechSynthesis.speak(u);
}

const wsProto = location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${wsProto}://${location.host}/ws`);

ws.onopen = () => st.textContent = "Estado: conectado ✅";
ws.onclose = () => st.textContent = "Estado: desconectado ❌";
ws.onerror = () => st.textContent = "Estado: error ❌";

ws.onmessage = (ev) => {
  try {
    const data = JSON.parse(ev.data);
    if (data.type === "talk") {
      t.textContent = data.transcript || "";
      a.textContent = data.answer || "";
      speak(data.answer || "");
    }
  } catch (e) {}
};

setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 25000);
</script>
</body>
</html>
"""

@app.get("/app-spectra", response_class=HTMLResponse)
def app_spectra_page():
    return r"""
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Spectra AI - Interface</title>

    <style>
        :root {
            --bg-main: #020617;
            --bg-card: #020617cc;
            --accent-cyan: #22d3ee;
            --accent-purple: #a855f7;
            --accent-pink: #ec4899;
            --text-main: #e5e7eb;
            --text-muted: #9ca3af;
            --danger: #f97373;
            --success: #4ade80;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: radial-gradient(circle at top, #0b1120 0, #020617 40%, #000 100%);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .glow-orbit {
            position: fixed;
            inset: 0;
            pointer-events: none;
            background:
                radial-gradient(circle at 10% 20%, #22d3ee33 0, transparent 50%),
                radial-gradient(circle at 90% 80%, #a855f733 0, transparent 55%),
                radial-gradient(circle at 50% 50%, #ec489933 0, transparent 60%);
            opacity: 0.9;
            mix-blend-mode: screen;
            z-index: -1;
        }

        .container { width: 100%; max-width: 1200px; padding: 24px; }

        .card {
            background: linear-gradient(135deg, #020617ee, #020617cc);
            border-radius: 24px;
            border: 1px solid #1f2937;
            box-shadow: 0 0 40px #0ea5e920, 0 0 80px #a855f720;
            padding: 24px 28px 28px;
            backdrop-filter: blur(20px);
        }

        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 10px;
            flex-wrap: wrap;
        }

        .logo-block { display: flex; align-items: center; gap: 14px; }

        .logo-orb {
            width: 48px; height: 48px; border-radius: 999px;
            background: radial-gradient(circle at 30% 30%, #e0f2fe, #22d3ee 40%, #0f172a 70%);
            box-shadow: 0 0 18px #22d3eeaa, 0 0 40px #22d3ee66;
            position: relative; overflow: hidden;
        }

        .logo-orb::after {
            content: "";
            position: absolute;
            inset: 4px;
            border-radius: inherit;
            border: 1px solid #e0f2fe55;
            box-shadow: 0 0 12px #a855f766;
        }

        .title-block h1 {
            font-size: 1.6rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: #f9fafb;
        }

        .title-block span {
            display: block;
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.2em;
            color: var(--accent-cyan);
            opacity: 0.85;
        }

        .status-pill {
            border-radius: 999px;
            padding: 6px 14px;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: #020617;
            border: 1px solid #1f2937;
            font-size: 0.8rem;
            white-space: nowrap;
        }

        .status-dot {
            width: 8px; height: 8px; border-radius: 999px;
            background: var(--success);
            box-shadow: 0 0 12px #4ade8080;
        }

        .status-pill span:nth-child(2) {
            text-transform: uppercase;
            letter-spacing: 0.16em;
            font-weight: 600;
            color: #e5e7eb;
        }

        .status-pill span:last-child { color: var(--text-muted); }

        .nav {
            width: 100%;
            display: flex;
            gap: 10px;
            align-items: center;
            justify-content: space-between;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #111827;
            margin-bottom: 18px;
            flex-wrap: wrap;
        }

        .nav-left {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            align-items: center;
        }

        .nav-right {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: flex-end;
            align-items: center;
        }

        .nav-link {
            text-decoration: none;
            border-radius: 999px;
            border: 1px solid #1f2937;
            background: #020617;
            color: #e5e7eb;
            padding: 8px 12px;
            font-size: 0.78rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
        }

        .nav-link:hover {
            border-color: #22d3ee55;
            box-shadow: 0 0 14px #22d3ee22;
        }

        .nav-link.active {
            border-color: #22d3ee77;
            background: radial-gradient(circle at top left, #22d3ee18, #020617);
            color: #e0faff;
        }

        .nav-link.logout {
            border-color: rgba(249,115,115,.35);
            background: rgba(249,115,115,.08);
            color: #fecaca;
        }

        .nav-link.logout:hover {
            border-color: rgba(249,115,115,.70);
            box-shadow: 0 0 14px rgba(249,115,115,.18);
        }

        .view { display: none; }
        .view.active { display: block; }

        .layout {
            display: grid;
            grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
            gap: 20px;
        }

        @media (max-width: 900px) {
            .layout { grid-template-columns: minmax(0, 1fr); }
        }

        .panel {
            border-radius: 20px;
            border: 1px solid #111827;
            padding: 18px;
            background: radial-gradient(circle at top, #020617, #020617dd 40%, #020617 100%);
        }

        .panel-header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-bottom: 8px;
            gap: 10px;
            flex-wrap: wrap;
        }

        .panel-header h2 {
            font-size: 0.95rem;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            color: #9ca3af;
        }

        .panel-header span { font-size: 0.8rem; color: var(--text-muted); }

        .chat-selector {
            margin-top: 10px;
            border-radius: 14px;
            border: 1px solid #111827;
            background: #020617;
            padding: 10px;
        }

        .chat-selector-top {
            display:flex;
            align-items:center;
            justify-content: space-between;
            gap: 10px;
        }

        .chat-selector-title {
            font-size: 0.8rem;
            color: var(--text-muted);
            cursor: pointer;
            user-select: none;
        }

        .active-chat-pill {
            font-size: 0.78rem;
            color: #e5e7eb;
            border: 1px solid #1f2937;
            background: #0b1224;
            padding: 6px 10px;
            border-radius: 999px;
            white-space: nowrap;
        }

        .chat-list {
            margin-top: 10px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            max-height: 220px;
            overflow: auto;
            scrollbar-width: thin;
        }

        .chat-row {
            display:flex;
            align-items:center;
            justify-content: space-between;
            gap: 10px;
            border: 1px solid rgba(255,255,255,.08);
            background: rgba(255,255,255,.02);
            padding: 8px 10px;
            border-radius: 12px;
            cursor: pointer;
        }

        .chat-row.active {
            border-color: rgba(34,211,238,.5);
            background: rgba(34,211,238,.08);
        }

        .chat-row-left { min-width: 0; }
        .chat-row-title {
            font-weight: 650;
            font-size: 0.86rem;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 340px;
        }

        .chat-row-meta {
            font-size: 0.72rem;
            color: rgba(229,231,235,.55);
            margin-top: 2px;
        }

        .chat-row-actions { display:flex; gap: 8px; align-items:center; }

        .btn-mini {
            border-radius: 10px;
            border: 1px solid #1f2937;
            background: #0b1224;
            color: #e5e7eb;
            padding: 6px 9px;
            cursor:pointer;
            font-size: 0.78rem;
            white-space: nowrap;
        }

        .btn-mini.danger {
            border-color: rgba(249,115,115,.45);
            background: rgba(249,115,115,.10);
            color: #fecaca;
        }

        .hide { display:none; }

        .chat-window {
            margin-top: 10px;
            border-radius: 14px;
            border: 1px solid #111827;
            background: #020617;
            padding: 12px;
            max-height: 360px;
            overflow-y: auto;
            scrollbar-width: thin;
        }

        .msg { margin-bottom: 10px; display: flex; gap: 10px; }

        .msg-avatar {
            width: 26px; height: 26px; border-radius: 999px;
            display: flex; align-items: center; justify-content: center;
            font-size: 0.82rem;
            flex: 0 0 26px;
        }

        .msg.user .msg-avatar {
            background: #22c55e22;
            color: #bbf7d0;
            border: 1px solid #22c55e55;
        }

        .msg.ai .msg-avatar {
            background: #22d3ee22;
            color: #e0faff;
            border: 1px solid #22d3ee77;
        }

        .msg-bubble {
            border-radius: 12px;
            padding: 8px 11px;
            font-size: 0.9rem;
            line-height: 1.4;
            max-width: 100%;
            white-space: pre-wrap;
            word-break: break-word;
        }

        .msg.user .msg-bubble {
            background: linear-gradient(135deg, #064e3b, #022c22);
            border: 1px solid #16a34a66;
        }

        .msg.ai .msg-bubble {
            background: radial-gradient(circle at top left, #0f172a, #020617);
            border: 1px solid #1f2937;
        }

        .msg-meta { margin-top: 2px; font-size: 0.72rem; color: #6b7280; }

        .voice-panel { display: flex; flex-direction: column; gap: 14px; }

        .voice-card {
            border-radius: 18px;
            border: 1px solid #111827;
            padding: 14px;
            background: radial-gradient(circle at 10% 0%, #22d3ee11, #020617 50%);
        }

        .hint { font-size: 0.86rem; color: var(--text-muted); margin-bottom: 10px; }
        .hint strong { color: #e5e7eb; }

        .space-key {
            margin-top: 6px;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            border-radius: 999px;
            padding: 6px 11px;
            border: 1px dashed #4b5563;
            font-size: 0.8rem;
            color: #9ca3af;
        }

        .kbd {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 6px;
            border: 1px solid #4b5563;
            padding: 3px 10px;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: #e5e7eb;
            background: #020617;
            box-shadow: 0 2px 0 #020617;
        }

        .wave-ring {
            position: relative;
            margin-top: 10px;
            width: 120px;
            height: 120px;
            border-radius: 999px;
            border: 1px solid #1f2937;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }

        .wave-ring::before,
        .wave-ring::after {
            content: "";
            position: absolute;
            width: 140%;
            height: 140%;
            border-radius: 50%;
            border: 1px solid #22d3ee33;
            animation: pulse 2.6s infinite;
        }

        .wave-ring::after {
            animation-delay: 0.9s;
            border-color: #a855f733;
        }

        .mic-core {
            width: 52px;
            height: 52px;
            border-radius: 999px;
            background: conic-gradient(from 210deg, #22d3ee, #a855f7, #ec4899, #22d3ee);
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 0 20px #22d3eeaa, 0 0 32px #a855f799;
            position: relative;
        }

        .mic-core-inner {
            width: 44px;
            height: 44px;
            border-radius: inherit;
            background: #020617;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #e5e7eb;
            font-size: 1.4rem;
        }

        .mic-core-inner.listening {
            background: radial-gradient(circle at 30% 30%, #4ade8033, #16a34a33, #022c22);
            color: #bbf7d0;
        }

        .mic-status { margin-top: 12px; font-size: 0.8rem; color: var(--text-muted); }
        .mic-status span { font-weight: 600; }
        .mic-status.listening span { color: var(--accent-cyan); }
        .mic-status.error span { color: var(--danger); }
        .mic-status.idle span { color: #6b7280; }

        .transcript-box {
            margin-top: 10px;
            border-radius: 10px;
            border: 1px dashed #1f2937;
            padding: 8px 10px;
            font-size: 0.86rem;
            color: var(--text-muted);
            min-height: 40px;
            white-space: pre-wrap;
        }

        .settings-card {
            border-radius: 18px;
            border: 1px solid #111827;
            padding: 12px 14px;
            background: radial-gradient(circle at 90% 100%, #a855f711, #020617 50%);
            font-size: 0.82rem;
            color: var(--text-muted);
        }

        .settings-card h3 {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            color: #9ca3af;
            margin-bottom: 8px;
        }

        .tag-list {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 6px;
        }

        .tag {
            border-radius: 999px;
            padding: 3px 9px;
            border: 1px solid #1f2937;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
        }

        .tag.primary { border-color: #22d3ee66; color: #e0f2fe; }
        .tag.secondary { border-color: #a855f766; color: #f5d0fe; }

        .reminders-panel {
            border-radius: 20px;
            border: 1px solid #111827;
            padding: 18px;
            background: radial-gradient(circle at top, #020617, #020617dd 40%, #020617 100%);
        }

        .tasks-box {
            margin-top: 12px;
            border-radius: 14px;
            border: 1px solid #111827;
            background: #020617;
            padding: 12px;
            min-height: 320px;
            max-height: 480px;
            overflow-y: auto;
        }

        .task-item {
            border: 1px solid rgba(255,255,255,.08);
            background: rgba(255,255,255,.02);
            border-radius: 12px;
            padding: 12px;
            margin-bottom: 10px;
        }

        .task-text {
            color: #e5e7eb;
            line-height: 1.4;
            white-space: pre-wrap;
        }

        .task-meta {
            font-size: 0.78rem;
            color: var(--text-muted);
            margin-top: 6px;
        }

        .task-actions {
            margin-top: 10px;
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .registro-wrap {
            border-radius: 20px;
            border: 1px solid #111827;
            overflow: hidden;
            background: #020617;
            min-height: 720px;
        }

        .registro-frame {
            width: 100%;
            height: 720px;
            border: 0;
            background: #020617;
        }

        @keyframes pulse {
            0% { transform: scale(0.9); opacity: 0.9; }
            60% { transform: scale(1.15); opacity: 0.0; }
            100% { transform: scale(1.25); opacity: 0; }
        }

        .toast {
            position: fixed;
            right: 18px;
            bottom: 18px;
            width: min(420px, calc(100vw - 36px));
            border-radius: 16px;
            border: 1px solid #1f2937;
            background: radial-gradient(circle at top left, #0f172a, #020617);
            box-shadow: 0 0 20px #22d3ee22, 0 0 40px #a855f722;
            padding: 12px 14px;
            display: none;
            z-index: 9999;
        }

        .toast.show { display: block; }

        .toast-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 6px;
        }

        .toast-title {
            font-size: 0.82rem;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            color: #9ca3af;
        }

        .toast-close {
            border: 1px solid #1f2937;
            background: #020617;
            color: #e5e7eb;
            border-radius: 10px;
            padding: 6px 10px;
            cursor: pointer;
            font-size: 0.8rem;
        }

        .toast-msg {
            font-size: 0.92rem;
            line-height: 1.35;
            color: #e5e7eb;
            white-space: pre-wrap;
        }

        .btn-noti {
            border-radius: 12px;
            border: 1px solid #1f2937;
            background: #0b1224;
            color: #e5e7eb;
            padding: 10px 12px;
            cursor: pointer;
        }

        @media (max-width: 900px) {
            .container { padding: 14px; }
            .card { padding: 18px; }
            .registro-frame { height: 780px; }
        }
    </style>
</head>

<body>
<div class="glow-orbit"></div>

<div class="container">
    <div class="card">
        <div class="header">
            <div class="logo-block">
                <div class="logo-orb"></div>
                <div class="title-block">
                    <span>INTERFAZ DE VOZ</span>
                    <h1>SPECTRA&nbsp;AI</h1>
                </div>
            </div>

            <div class="status-pill" id="connectionStatus">
                <div class="status-dot" id="statusDot"></div>
                <span>ONLINE</span>
                <span id="statusText">Listo para escuchar</span>
            </div>
        </div>

        <div class="nav">
            <div class="nav-left">
                <button class="nav-link active" id="tabCore" type="button">⚡ Core</button>
                <button class="nav-link" id="tabRem" type="button">🗓 Recordatorios</button>
                <button class="nav-link" id="tabRegistro" type="button">🧾 Registro</button>
                <button class="nav-link" id="btnNewChat" type="button">➕ Nuevo chat</button>
            </div>

            <div class="nav-right">
                <a class="nav-link" href="/speaker-redirect" target="_blank" rel="noopener noreferrer">🔊 Speaker PC</a>
                <button class="nav-link logout" id="btnLogoutTop" type="button">🔓 Cerrar sesión</button>
            </div>
        </div>

        <!-- CORE -->
        <div class="view active" id="viewCore">
            <div class="layout">
                <div class="panel">
                    <div class="panel-header">
                        <h2>Registro de conversación</h2>
                        <span>Mensajes recientes</span>
                    </div>

                    <div class="chat-selector">
                        <div class="chat-selector-top">
                            <div class="chat-selector-title" id="toggleChats">Tus chats ▼</div>
                            <div class="active-chat-pill" id="activeChatPill">Chat: default</div>
                        </div>
                        <div class="chat-list" id="chatsList"></div>
                    </div>

                    <div class="chat-window" id="chatWindow"></div>
                </div>

                <div class="voice-panel">
                    <div class="voice-card">
                        <p class="hint">
                            Mantén presionada la tecla <strong>ESPACIO</strong> para hablar con Spectra AI.
                            Suelta la tecla para enviar el mensaje.
                        </p>

                        <div class="space-key">
                            <span class="kbd">Space</span>
                            <span>Presiona y mantén para grabar</span>
                        </div>

                        <div class="wave-ring">
                            <div class="mic-core" id="micButton" style="cursor:pointer;">
                                <div class="mic-core-inner" id="micCoreInner">🎙</div>
                            </div>
                        </div>

                        <div class="mic-status idle" id="micStatus">
                            <span>Espera tranquila…</span> Spectra está lista.
                        </div>

                        <div class="transcript-box" id="transcriptBox">
                            Aquí verás la transcripción y la respuesta…
                        </div>
                    </div>

                    <div style="margin-top:12px; display:flex; gap:10px; flex-wrap:wrap;">
                      <input id="textQuestion" placeholder="Escribe una pregunta (opcional)..."
                             style="flex:1; min-width:220px; border-radius:12px; border:1px solid #1f2937; background:#020617; color:#e5e7eb; padding:10px 12px; outline:none;" />
                      <button id="btnAskText" type="button"
                        style="border-radius:12px; border:1px solid #1f2937; background:#0b1224; color:#e5e7eb; padding:10px 12px; cursor:pointer;">
                        Preguntar
                      </button>

                      <button id="btnUltima"
                              style="border-radius:12px; border:1px solid #1f2937; background:#0b1224; color:#e5e7eb; padding:10px 12px; cursor:pointer;">
                        Última medición
                      </button>

                      <button id="btnSensores"
                              style="border-radius:12px; border:1px solid #1f2937; background:#0b1224; color:#e5e7eb; padding:10px 12px; cursor:pointer;">
                        Sensores (crudo)
                      </button>

                      <button id="btnNoti" class="btn-noti" type="button">🔔 Activar notificaciones</button>

                      <button id="btnLoadHistory" type="button"
                              style="border-radius:12px; border:1px solid #1f2937; background:#0b1224; color:#e5e7eb; padding:10px 12px; cursor:pointer;">
                        ⟳ Cargar historial
                      </button>
                    </div>

                    <div class="settings-card">
                        <h3>Especificaciones</h3>
                        <p>
                            Este demo graba audio real (MediaRecorder) y lo envía al backend en Python,
                            que lo reenvía a FastAPI. FastAPI transcribe, genera respuesta
                            y la manda al PC Speaker por WebSocket.
                        </p>
                        <div class="tag-list">
                            <div class="tag primary">audio → fastapi</div>
                            <div class="tag primary">whisper stt</div>
                            <div class="tag secondary">pc speaker</div>
                            <div class="tag secondary">tecla espacio</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- RECORDATORIOS -->
        <div class="view" id="viewRem">
            <div class="reminders-panel">
                <div class="panel-header">
                    <h2>Recordatorios</h2>
                    <span>Tareas programadas</span>
                </div>

                <div class="tasks-box" id="tasksBox">
                    <div class="task-item">
                        <div class="task-text">Cargando recordatorios...</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- REGISTRO -->
        <div class="view" id="viewRegistro">
            <div class="registro-wrap">
                <iframe class="registro-frame" src="/registro"></iframe>
            </div>
        </div>
    </div>
</div>

<div class="toast" id="toast">
  <div class="toast-top">
    <div class="toast-title">Notificación</div>
    <button class="toast-close" id="toastClose">Cerrar</button>
  </div>
  <div class="toast-msg" id="toastMsg">…</div>
</div>

<script>
const chatsListEl     = document.getElementById("chatsList");
const activeChatPill  = document.getElementById("activeChatPill");
const toggleChats     = document.getElementById("toggleChats");

const chatWindow      = document.getElementById("chatWindow");
const transcriptBox   = document.getElementById("transcriptBox");
const micStatus       = document.getElementById("micStatus");
const micCoreInner    = document.getElementById("micCoreInner");

const toast           = document.getElementById("toast");
const toastMsg        = document.getElementById("toastMsg");
const toastClose      = document.getElementById("toastClose");
if (toastClose) toastClose.onclick = () => toast.classList.remove("show");

const tabCore         = document.getElementById("tabCore");
const tabRem          = document.getElementById("tabRem");
const tabRegistro     = document.getElementById("tabRegistro");
const viewCore        = document.getElementById("viewCore");
const viewRem         = document.getElementById("viewRem");
const viewRegistro    = document.getElementById("viewRegistro");
const tasksBox        = document.getElementById("tasksBox");

function showToast(message, ms = 9000) {
  if (!toast || !toastMsg) return;
  toastMsg.textContent = message || "";
  toast.classList.add("show");
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => toast.classList.remove("show"), ms);
}

function setMic(state, message) {
  if (!micStatus || !micCoreInner) return;
  micStatus.classList.remove("idle", "listening", "error");
  micStatus.classList.add(state);
  micCoreInner.classList.toggle("listening", state === "listening");
  micStatus.innerHTML = `<span>${
    state === "idle" ? "Espera tranquila…" :
    state === "listening" ? "Escuchando…" :
    "Error"
  }</span> ${message || ""}`;
}

function activateTab(tabName) {
  [tabCore, tabRem, tabRegistro].forEach(btn => btn.classList.remove("active"));
  [viewCore, viewRem, viewRegistro].forEach(view => view.classList.remove("active"));

  if (tabName === "core") {
    tabCore.classList.add("active");
    viewCore.classList.add("active");
  } else if (tabName === "rem") {
    tabRem.classList.add("active");
    viewRem.classList.add("active");
    loadTasks();
  } else if (tabName === "registro") {
    tabRegistro.classList.add("active");
    viewRegistro.classList.add("active");
  }
}

tabCore?.addEventListener("click", () => activateTab("core"));
tabRem?.addEventListener("click", () => activateTab("rem"));
tabRegistro?.addEventListener("click", () => activateTab("registro"));

const btnLogoutTop = document.getElementById("btnLogoutTop");
if (btnLogoutTop) {
  btnLogoutTop.addEventListener("click", async () => {
    const ok = confirm("¿Cerrar sesión y volver al login?");
    if (!ok) return;

    try {
      await fetch("/auth/logout", { method: "POST" }).catch(() => null);
    } catch (e) {}

    window.location.replace("/login");
  });
}

const CHAT_INDEX_KEY = "spectra_chat_index_v1";
const CHAT_ID_KEY    = "spectra_chat_id";
const CORE_CACHE_MAX = 120;

function _safeJsonParse(s) {
  try { return JSON.parse(s); } catch (e) { return null; }
}

function coreCacheKey(chatId) {
  return `spectra_core_cache_v2_${chatId || "default"}`;
}

function loadCoreCache(chatId) {
  const raw = localStorage.getItem(coreCacheKey(chatId));
  const arr = _safeJsonParse(raw);
  return Array.isArray(arr) ? arr : [];
}

function saveCoreCache(chatId, messages) {
  try {
    const keep = (messages || []).slice(-CORE_CACHE_MAX);
    localStorage.setItem(coreCacheKey(chatId), JSON.stringify(keep));
  } catch (e) {}
}

let currentChatId = (localStorage.getItem(CHAT_ID_KEY) || "default").trim();

function loadChatIndex() {
  const raw = localStorage.getItem(CHAT_INDEX_KEY);
  const arr = _safeJsonParse(raw);
  if (Array.isArray(arr) && arr.length) return arr;

  const init = [{ id: "default", title: "Default", updated_at: "" }];
  localStorage.setItem(CHAT_INDEX_KEY, JSON.stringify(init));
  return init;
}

function saveChatIndex(chats) {
  localStorage.setItem(CHAT_INDEX_KEY, JSON.stringify(chats || []));
}

function escapeHtml(s) {
  return (s || "")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

function fmtUpdated(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString([], {
    year:"numeric", month:"2-digit", day:"2-digit",
    hour:"2-digit", minute:"2-digit"
  });
}

function clearChatWindow() {
  if (!chatWindow) return;
  while (chatWindow.firstChild) chatWindow.removeChild(chatWindow.firstChild);
}

function formatTimeFromTs(ts) {
  try {
    const d = new Date(ts);
    if (!isNaN(d.getTime())) {
      return d.toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" });
    }
  } catch (e) {}
  return new Date().toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" });
}

function renderMessage(role, text, tsIso) {
  const wrapper = document.createElement("div");
  wrapper.classList.add("msg", role === "user" ? "user" : "ai");

  const avatar = document.createElement("div");
  avatar.classList.add("msg-avatar");
  avatar.textContent = role === "user" ? "Tú" : "S";

  const bubble = document.createElement("div");
  bubble.classList.add("msg-bubble");
  bubble.textContent = text;

  const meta = document.createElement("div");
  meta.classList.add("msg-meta");
  meta.textContent = (role === "user" ? "Usuario · " : "Spectra AI · ") + formatTimeFromTs(tsIso);

  const right = document.createElement("div");
  right.appendChild(bubble);
  right.appendChild(meta);

  wrapper.appendChild(avatar);
  wrapper.appendChild(right);

  chatWindow.appendChild(wrapper);
}

let coreMessages = loadCoreCache(currentChatId);

function renderCoreFromCache() {
  clearChatWindow();
  for (const m of coreMessages) {
    renderMessage(m.role, m.text, m.ts);
  }
  if (chatWindow) chatWindow.scrollTop = chatWindow.scrollHeight;
}

function makeChatTitleFromText(text) {
  const s = String(text || "")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^["'“”‘’]+|["'“”‘’]+$/g, "")
    .trim();

  if (!s) return "";
  const max = 48;
  return s.length > max ? (s.slice(0, max).trim() + "…") : s;
}

function isGenericTitle(t) {
  const x = String(t || "").trim().toLowerCase();
  return !x || x === "nuevo chat" || x === "new chat";
}

function maybeAutoRenameCurrentChat(firstUserText) {
  const cid = (currentChatId || "default").trim();
  if (!cid || cid === "default") return;

  const doneKey = `spectra_autotitle_done_${cid}`;
  if (localStorage.getItem(doneKey) === "1") return;

  const title = makeChatTitleFromText(firstUserText);
  if (!title) return;

  let chats = loadChatIndex();
  const idx = chats.findIndex(c => c.id === cid);
  if (idx < 0) return;

  const currentTitle = chats[idx].title || "";
  if (!isGenericTitle(currentTitle)) {
    localStorage.setItem(doneKey, "1");
    return;
  }

  chats[idx].title = title;
  chats[idx].updated_at = new Date().toISOString();
  saveChatIndex(chats);

  localStorage.setItem(doneKey, "1");
  loadChatsListLocal();
  activeChatPill.textContent = `Chat: ${title}`;
}

function setCurrentChat(id, title) {
  currentChatId = (id || "default").trim();
  localStorage.setItem(CHAT_ID_KEY, currentChatId);

  const showTitle = title ? title : currentChatId;
  if (activeChatPill) activeChatPill.textContent = `Chat: ${showTitle}`;

  document.querySelectorAll(".chat-row").forEach(el => el.classList.remove("active"));
  const active = document.querySelector(`.chat-row[data-chat-id="${CSS.escape(currentChatId)}"]`);
  if (active) active.classList.add("active");

  coreMessages = loadCoreCache(currentChatId);
  renderCoreFromCache();
}

function deleteChatLocal(chatId) {
  if (chatId === "default") {
    const ok = confirm(
      "¿Quieres limpiar el chat Default?\n\n" +
      "Se borrarán todos los mensajes,\n" +
      "pero el chat seguirá existiendo."
    );
    if (!ok) return;

    localStorage.removeItem(coreCacheKey("default"));
    localStorage.removeItem("spectra_autotitle_done_default");

    let chats = loadChatIndex();
    const idx = chats.findIndex(c => c.id === "default");
    if (idx >= 0) {
      chats[idx].title = "Default";
      chats[idx].updated_at = new Date().toISOString();
      saveChatIndex(chats);
    }

    currentChatId = "default";
    localStorage.setItem("spectra_chat_id", "default");

    coreMessages = [];
    renderCoreFromCache();
    loadChatsListLocal();

    showToast("Chat Default limpiado ✅", 2500);
    return;
  }

  const ok = confirm(`¿Eliminar este chat?\n\nID: ${chatId}`);
  if (!ok) return;

  localStorage.removeItem(coreCacheKey(chatId));
  localStorage.removeItem(`spectra_autotitle_done_${chatId}`);

  let chats = loadChatIndex();
  chats = chats.filter(c => c.id !== chatId);
  saveChatIndex(chats);

  if (currentChatId === chatId) {
    const def = chats.find(c => c.id === "default") || { id:"default", title:"Default" };
    setCurrentChat(def.id, def.title);
  }

  loadChatsListLocal();
  showToast("Chat eliminado ✅", 2500);
}

function loadChatsListLocal() {
  let chats = loadChatIndex();

  if (!chats.find(c => c.id === "default")) {
    chats.unshift({ id:"default", title:"Default", updated_at:"" });
    saveChatIndex(chats);
  }

  if (!chatsListEl) return;
  chatsListEl.innerHTML = "";

  chats.forEach(c => {
    const row = document.createElement("div");
    row.className = "chat-row";
    row.dataset.chatId = c.id;

    row.innerHTML = `
      <div class="chat-row-left">
        <div class="chat-row-title">${escapeHtml(c.title || c.id)}</div>
        <div class="chat-row-meta">${escapeHtml(fmtUpdated(c.updated_at) || "")}</div>
      </div>
      <div class="chat-row-actions">
        <button class="btn-mini danger" data-del="${escapeHtml(c.id)}" title="Eliminar chat">🗑</button>
      </div>
    `;

    row.addEventListener("click", (ev) => {
      if (ev.target && ev.target.matches("[data-del]")) return;
      setCurrentChat(c.id, c.title || c.id);
    });

    const delBtn = row.querySelector("[data-del]");
    delBtn.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      deleteChatLocal(delBtn.getAttribute("data-del"));
    });

    chatsListEl.appendChild(row);
  });

  const exists = chats.find(x => x.id === currentChatId);
  if (!exists) currentChatId = "default";

  const active = chats.find(x => x.id === currentChatId) || chats[0];
  setCurrentChat(active.id, active.title || active.id);
}

if (toggleChats) {
  toggleChats.addEventListener("click", () => {
    chatsListEl.classList.toggle("hide");
    toggleChats.textContent = chatsListEl.classList.contains("hide") ? "Tus chats ▶" : "Tus chats ▼";
  });
}

const btnNewChat = document.getElementById("btnNewChat");
if (btnNewChat) {
  btnNewChat.addEventListener("click", () => {
    let chats = loadChatIndex();
    const id = "chat_" + Math.random().toString(16).slice(2) + "_" + Date.now().toString(16);

    const chat = { id, title: "Nuevo chat", updated_at: new Date().toISOString() };
    chats.unshift(chat);
    saveChatIndex(chats);

    loadChatsListLocal();
    setCurrentChat(chat.id, chat.title);

    coreMessages = [];
    saveCoreCache(currentChatId, coreMessages);
    renderCoreFromCache();

    showToast("Nuevo chat creado ✅", 2500);
    activateTab("core");
  });
}

function appendMessage(role, text) {
  const nowIso = new Date().toISOString();

  renderMessage(role, text, nowIso);
  if (chatWindow) chatWindow.scrollTop = chatWindow.scrollHeight;

  coreMessages.push({ role, text, ts: nowIso });
  if (coreMessages.length > CORE_CACHE_MAX) coreMessages = coreMessages.slice(-CORE_CACHE_MAX);

  saveCoreCache(currentChatId, coreMessages);

  try {
    const chats = loadChatIndex();
    const idx = chats.findIndex(c => c.id === currentChatId);
    if (idx >= 0) {
      chats[idx].updated_at = nowIso;
      saveChatIndex(chats);
      loadChatsListLocal();
    }
  } catch (e) {}
}

async function loadTasks() {
  if (!tasksBox) return;

  tasksBox.innerHTML = `
    <div class="task-item">
      <div class="task-text">Cargando recordatorios...</div>
    </div>
  `;

  try {
    const res = await fetch("/tasks-proxy", { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    const tasks = Array.isArray(data.tasks) ? data.tasks : [];

    if (!tasks.length) {
      tasksBox.innerHTML = `
        <div class="task-item">
          <div class="task-text">No hay recordatorios todavía.</div>
        </div>
      `;
      return;
    }

    tasksBox.innerHTML = "";
    tasks.forEach(task => {
      const div = document.createElement("div");
      div.className = "task-item";
      div.innerHTML = `
        <div class="task-text">${escapeHtml(task.text || task.assistant || "Recordatorio")}</div>
        <div class="task-meta">${escapeHtml(task.run_at || task.created_at || "")}</div>
        <div class="task-actions">
          ${task.done ? "" : `<button class="btn-mini danger" data-del="${escapeHtml(task.id || "")}">Eliminar</button>`}
        </div>
      `;

      const delBtn = div.querySelector("[data-del]");
      if (delBtn) {
        delBtn.addEventListener("click", async () => {
          const id = delBtn.getAttribute("data-del");
          if (!id) return;
          if (!confirm("¿Eliminar este recordatorio?")) return;

          try {
            const rr = await fetch(`/tasks-proxy/${encodeURIComponent(id)}`, { method: "DELETE" });
            if (!rr.ok) throw new Error("No se pudo eliminar");
            await loadTasks();
            showToast("Recordatorio eliminado ✅", 2500);
          } catch (e) {
            showToast("No pude eliminar el recordatorio ❌");
          }
        });
      }

      tasksBox.appendChild(div);
    });
  } catch (e) {
    tasksBox.innerHTML = `
      <div class="task-item">
        <div class="task-text">No pude cargar recordatorios ❌</div>
      </div>
    `;
  }
}

const TALK_PROXY   = "/talk-proxy";
const ASK_PROXY    = "/ask-proxy";
const FB_ULTIMA    = "/firebase/ultima-proxy";
const FB_SENSORES  = "/firebase/sensores-proxy";

const textQuestion = document.getElementById("textQuestion");
const btnAskText   = document.getElementById("btnAskText");
const btnUltima    = document.getElementById("btnUltima");
const btnSensores  = document.getElementById("btnSensores");
const btnLoadHistory = document.getElementById("btnLoadHistory");

let isSendingText = false;

if (btnAskText) {
  btnAskText.onclick = async (ev) => {
    ev?.preventDefault?.();
    ev?.stopPropagation?.();

    const q = (textQuestion?.value || "").trim();
    if (!q) return;

    maybeAutoRenameCurrentChat(q);

    if (isSendingText) return;
    isSendingText = true;
    btnAskText.disabled = true;

    appendMessage("user", q);
    if (textQuestion) textQuestion.value = "";
    if (transcriptBox) transcriptBox.textContent = "⏳ Enviando pregunta por texto…";

    try {
      const res = await fetch(ASK_PROXY, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, chat_id: currentChatId || "default" })
      });

      const ct = res.headers.get("content-type") || "";
      if (!ct.includes("application/json")) {
        const raw = await res.text();
        appendMessage("ai", "❌ Respuesta no JSON del servidor.");
        if (transcriptBox) transcriptBox.textContent = raw.slice(0, 1200);
        setMic("error", "Respuesta inválida");
        return;
      }

      const data = await res.json();
      if (!res.ok) {
        const msg = data.error || data.detail || JSON.stringify(data);
        appendMessage("ai", `❌ Error backend (${res.status}): ${msg}`);
        if (transcriptBox) transcriptBox.textContent = `❌ ${msg}`;
        setMic("error", "Backend error");
        return;
      }

      const ans = (data.answer || "").trim();
      appendMessage("ai", ans || "(Sin respuesta)");
      if (transcriptBox) transcriptBox.textContent = `📝 Tú: ${q}\n\n🤖 Spectra: ${ans}`;
      setMic("idle", "Listo ✅");

    } catch (e) {
      appendMessage("ai", "❌ Error de conexión: " + (e?.message || e));
      if (transcriptBox) transcriptBox.textContent = "❌ Error de conexión: " + (e?.message || e);
      setMic("error", "No conecta");
    } finally {
      isSendingText = false;
      btnAskText.disabled = false;
    }
  };
}

if (btnLoadHistory) {
  btnLoadHistory.onclick = () => {
    renderCoreFromCache();
    showToast("Historial cargado desde caché local ✅", 2000);
    activateTab("core");
  };
}

if (btnUltima) {
  btnUltima.onclick = async () => {
    if (transcriptBox) transcriptBox.textContent = "⏳ Consultando última medición…";
    try {
      const res = await fetch(FB_ULTIMA);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (transcriptBox) transcriptBox.textContent = `❌ Error: ${data.error || data.detail || res.status}`;
        return;
      }
      appendMessage("ai", `📡 Última medición: ${JSON.stringify(data.last)}`);
      if (transcriptBox) transcriptBox.textContent = `📡 Última medición:\n${JSON.stringify(data.last, null, 2)}`;
      activateTab("core");
    } catch (e) {
      if (transcriptBox) transcriptBox.textContent = "❌ No se pudo consultar Firebase.";
    }
  };
}

if (btnSensores) {
  btnSensores.onclick = async () => {
    if (transcriptBox) transcriptBox.textContent = "⏳ Consultando sensores (crudo)…";
    try {
      const res = await fetch(FB_SENSORES);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (transcriptBox) transcriptBox.textContent = `❌ Error: ${data.error || data.detail || res.status}`;
        return;
      }
      if (transcriptBox) transcriptBox.textContent = `📦 Firebase crudo:\n${JSON.stringify(data.data, null, 2)}`;
      appendMessage("ai", "📦 Te mostré el JSON crudo de Firebase en el panel.");
      activateTab("core");
    } catch (e) {
      if (transcriptBox) transcriptBox.textContent = "❌ No se pudo consultar Firebase.";
    }
  };
}

let mediaRecorder = null;
let chunks = [];
let isRecording = false;
let spaceDown = false;

async function startRecording() {
  if (isRecording) return;

  try {
    chunks = [];
    setMic("listening", "Mantén ESPACIO mientras hablas.");
    if (transcriptBox) transcriptBox.textContent = "🎙 Grabando audio…";

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const options = {};
    if (window.MediaRecorder && MediaRecorder.isTypeSupported("audio/webm")) options.mimeType = "audio/webm";

    mediaRecorder = new MediaRecorder(stream, options);

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      try {
        setMic("idle", "Procesando… enviando al servidor");
        const blob = new Blob(chunks, { type: "audio/webm" });
        const fd = new FormData();
        fd.append("audio", blob, "voice.webm");
        fd.append("chat_id", (currentChatId || "default"));

        const res = await fetch(TALK_PROXY, { method: "POST", body: fd });
        const ct = res.headers.get("content-type") || "";

        if (!ct.includes("application/json")) {
          const raw = await res.text();
          appendMessage("ai", "❌ /talk-proxy devolvió NO JSON.");
          if (transcriptBox) transcriptBox.textContent = raw.slice(0, 1200);
          setMic("error", "Respuesta inválida");
          return;
        }

        const data = await res.json();

        if (!res.ok) {
          const msg = data.error || data.detail || JSON.stringify(data);
          appendMessage("ai", `❌ Error backend voz (${res.status}): ${msg}`);
          if (transcriptBox) transcriptBox.textContent = `❌ ${msg}`;
          setMic("error", "Backend error");
          return;
        }

        const transcript = (data.transcript || "").trim();
        const answer     = (data.answer || "").trim();

        if (transcript) maybeAutoRenameCurrentChat(transcript);

        if (transcript) {
          appendMessage("user", transcript);
          if (transcriptBox) transcriptBox.textContent = `📝 Tú: ${transcript}\n\n🤖 Spectra: ${answer}`;
        } else {
          appendMessage("user", "🎙️ (audio enviado)");
          if (transcriptBox) transcriptBox.textContent = `📝 (Sin transcripción)\n\n🤖 Spectra: ${answer}`;
        }

        appendMessage("ai", answer || "(Sin respuesta)");
        setMic("idle", "Listo para escuchar ✅");

      } catch (e) {
        appendMessage("ai", "❌ Error de conexión en VOZ: " + (e?.message || e));
        if (transcriptBox) transcriptBox.textContent = "❌ Error de conexión en VOZ: " + (e?.message || e);
        setMic("error", "No conecta");
      }
    };

    mediaRecorder.start();
    isRecording = true;
  } catch (e) {
    setMic("error", "Permisos de micrófono");
    if (transcriptBox) transcriptBox.textContent = "❌ No se pudo acceder al micrófono. Revisa permisos.";
  }
}

function stopRecording() {
  if (!isRecording || !mediaRecorder) return;
  try {
    isRecording = false;
    setMic("idle", "Procesando…");
    mediaRecorder.stop();
  } catch (e) {
    setMic("error", "No se pudo detener");
  }
}

document.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !spaceDown && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    e.preventDefault();
    spaceDown = true;
    startRecording();
  }
});

document.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    spaceDown = false;
    stopRecording();
  }
});

const micButton = document.getElementById("micButton");
if (micButton) {
  micButton.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
  micButton.addEventListener("touchend",   (e) => { e.preventDefault(); stopRecording(); });
  micButton.addEventListener("mousedown", (e) => {
    e.preventDefault();
    startRecording();
  });
  micButton.addEventListener("mouseup", (e) => {
    e.preventDefault();
    stopRecording();
  });
}

const btnNoti = document.getElementById("btnNoti");

async function notifGetState() {
  let res = await fetch("/notifications/status", { cache: "no-store" }).catch(() => null);
  if (!res || !res.ok) {
    res = await fetch("/api/notifications/status", { cache: "no-store" }).catch(() => null);
  }

  if (!res) throw new Error("No hay respuesta del servidor");
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || data.detail || "No pude leer estado de notificaciones");
  return data.state?.enabled === true;
}

async function notifSetState(enable) {
  const url1 = enable ? "/notifications/enable" : "/notifications/disable";
  const url2 = enable ? "/api/notifications/enable" : "/api/notifications/disable";

  let res = await fetch(url1, { method: "POST" }).catch(() => null);
  if (!res || !res.ok) res = await fetch(url2, { method: "POST" }).catch(() => null);

  if (!res) throw new Error("No hay respuesta del servidor");
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || data.detail || "No pude cambiar notificaciones");
  return data.state?.enabled === true;
}

function paintNotif(enabled) {
  if (!btnNoti) return;
  btnNoti.textContent = enabled ? "🔕 Desactivar notificaciones" : "🔔 Activar notificaciones";
}

async function initNotifButton() {
  if (!btnNoti) return;
  try {
    const enabled = await notifGetState();
    paintNotif(enabled);
  } catch (e) {
    paintNotif(false);
    showToast("Notif: " + (e?.message || e));
  }

  btnNoti.addEventListener("click", async () => {
    try {
      btnNoti.disabled = true;
      const current = await notifGetState();
      const next = await notifSetState(!current);
      paintNotif(next);
      showToast(next ? "Notificaciones activadas ✅" : "Notificaciones desactivadas ✅", 2500);
    } catch (e) {
      showToast("Notif: " + (e?.message || e));
    } finally {
      btnNoti.disabled = false;
    }
  });
}

loadChatsListLocal();
renderCoreFromCache();
setMic("idle", "Listo para hablar con Spectra");
initNotifButton();
activateTab("core");
</script>
</body>
</html>
"""

# ===============================
# ✅ AGENDA / RECORDATORIOS (persistente) + chat_id
# ===============================
TASKS_FILE = "tasks.json"

def _load_tasks() -> list:
    if not os.path.exists(TASKS_FILE):
        return []
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except:
        return []

def _save_tasks(tasks: list):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

def create_task_internal(
    text: str,
    in_minutes: int,
    chat_id: str = "default",
    create_calendar_event: bool = False,   # ✅ NUEVO (opcional)
    calendar_title: Optional[str] = None,  # ✅ NUEVO (opcional)
):
    now = datetime.now(TZ)
    run_at = now + timedelta(minutes=int(in_minutes))

    # ✅ (igual que antes) recordatorio local
    task = {
        "id": uuid.uuid4().hex[:10],
        "text": text.strip(),
        "run_at": run_at.isoformat(),
        "done": False,
        "created_at": now.isoformat(),
        "chat_id": _sanitize_chat_id(chat_id or "default"),
    }

    # ✅ NUEVO: si quieres, además crea evento en Google Calendar (vía n8n)
    # - Evento desde "now" hasta "run_at" (o sea, dura in_minutes)
    if create_calendar_event:
        try:
            title = (calendar_title or f"Recordatorio: {task['text']}").strip()

            start_iso = _dt_to_iso(now)      # requiere _dt_to_iso() que te di
            end_iso   = _dt_to_iso(run_at)

            cal_resp = crear_evento_calendar_via_n8n(title, start_iso, end_iso)
            task["calendar"] = {
                "requested": True,
                "title": title,
                "start": start_iso,
                "end": end_iso,
                "response": cal_resp
            }
        except Exception as e:
            task["calendar"] = {
                "requested": True,
                "error": str(e)
            }

    tasks = _load_tasks()
    tasks.append(task)
    _save_tasks(tasks)
    _schedule_task(task)
    return task


def _schedule_task(task: dict):
    task_id = task["id"]
    run_at = datetime.fromisoformat(task["run_at"])
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=TZ)
    if run_at <= datetime.now(TZ):
        return

    job_id = f"task_{task_id}"

    async def _fire_async():
        cid = _sanitize_chat_id(task.get("chat_id") or "default")
        msg = f"Recordatorio: {task.get('text','')}".strip()

        save_chat_event(
            "reminder",
            user_text=None,
            assistant_text=msg,
            meta={"task_id": task_id, "run_at": task.get("run_at")},
            chat_id=cid
        )

        await ws_broadcast({"type": "talk", "transcript": "recordatorio", "answer": msg, "chat_id": cid})
        await ws_app_broadcast({
            "type": "reminder",
            "text": msg,
            "run_at": task.get("run_at"),
            "task_id": task_id,
            "chat_id": cid
        })

        tasks = _load_tasks()
        for t in tasks:
            if t.get("id") == task_id:
                t["done"] = True
        _save_tasks(tasks)

    def _fire():
        try:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(_fire_async())
                else:
                    loop.run_until_complete(_fire_async())
            except RuntimeError:
                asyncio.run(_fire_async())
        except Exception:
            pass

    try:
        scheduler.remove_job(job_id)
    except:
        pass

    scheduler.add_job(_fire, trigger=DateTrigger(run_date=run_at), id=job_id, replace_existing=True)

def _reschedule_all():
    tasks = _load_tasks()
    for t in tasks:
        if not t.get("done"):
            try:
                _schedule_task(t)
            except:
                pass

_reschedule_all()

class TaskCreateReq(BaseModel):
    text: str
    run_at: Optional[str] = None
    in_minutes: Optional[int] = None
    chat_id: Optional[str] = "default"

class ChatCreateReq(BaseModel):
    title: Optional[str] = "Nuevo chat"


class LabPrestarReq(BaseModel):
    nombre: Optional[str] = ""
    semestre: Optional[str] = ""
    equipo: Optional[str] = ""
    banner_id: Optional[str] = ""
    extra_general: Optional[str] = ""

class LabEntregarReq(BaseModel):
    id: str

class LabDevolverReq(BaseModel):
    id: Optional[str] = None
    row_number: Optional[int] = None


# ===============================
# ✅ Multi-chat local (JSON)
# ===============================
CHATS_DIR = "chats"
os.makedirs(CHATS_DIR, exist_ok=True)

def _sanitize_chat_id(chat_id: str) -> str:
    chat_id = (chat_id or "default").strip().lower()
    chat_id = re.sub(r"[^a-z0-9_\-]", "-", chat_id)
    return chat_id or "default"

def _chat_file(chat_id: str) -> str:
    return os.path.join(CHATS_DIR, f"{_sanitize_chat_id(chat_id)}.json")

def _ensure_chat_exists(chat_id: str, title: Optional[str] = None):
    chat_id = _sanitize_chat_id(chat_id)
    path = _chat_file(chat_id)

    if not os.path.exists(path):
        data = {
            "id": chat_id,
            "title": title or ("Default" if chat_id == "default" else "Nuevo chat"),
            "created_at": datetime.now(TZ).isoformat(),
            "updated_at": datetime.now(TZ).isoformat(),
            "history": []
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def _load_chat(chat_id: str) -> dict:
    chat_id = _sanitize_chat_id(chat_id)
    _ensure_chat_exists(chat_id)

    with open(_chat_file(chat_id), "r", encoding="utf-8") as f:
        return json.load(f)

def _save_chat(chat: dict):
    chat_id = _sanitize_chat_id(chat.get("id") or "default")
    chat["id"] = chat_id
    chat["updated_at"] = datetime.now(TZ).isoformat()

    with open(_chat_file(chat_id), "w", encoding="utf-8") as f:
        json.dump(chat, f, ensure_ascii=False, indent=2)

def save_chat_event(kind: str, user_text=None, assistant_text=None, meta=None, chat_id: str = "default"):
    chat_id = _sanitize_chat_id(chat_id)
    chat = _load_chat(chat_id)

    event = {
        "kind": kind,
        "user": user_text,
        "assistant": assistant_text,
        "meta": meta or {},
        "ts": datetime.now(TZ).isoformat()
    }

    chat.setdefault("history", []).append(event)
    _save_chat(chat)
    return event

# ===============================
# HELPERS PARA RESPUESTAS
# ===============================

SYSTEM_STYLE_SHORT = (
    "Eres Spectra AI. Responde en español, claro, corto y útil."
)

SYSTEM_STYLE_LONG = (
    "Eres Spectra AI. Responde en español, claro, útil y un poco más detallado cuando te lo pidan."
)

MAX_CHARS_SHORT = 500
MAX_CHARS_LONG = 1500

def wants_detailed(text: str) -> bool:
    t = (text or "").lower()
    keywords = [
        "explica",
        "explícame",
        "detalla",
        "detallado",
        "detalle",
        "profundo",
        "a fondo",
        "paso a paso",
        "desarrolla",
        "amplía",
        "amplia",
    ]
    return any(k in t for k in keywords)

def compact_answer(text: str, max_chars: int = 500) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."

def needs_sensors_context(text: str) -> bool:
    t = (text or "").lower()
    keys = ["sensor", "sensores", "medición", "medicion", "temperatura", "humedad", "firebase"]
    return any(k in t for k in keys)

def needs_online(text: str) -> bool:
    t = (text or "").lower()
    keys = [
        "internet", "web", "busca", "buscar", "buscame", "búscame",
        "actual", "actualizado", "última", "ultima", "noticia", "noticias",
        "google", "investiga", "averigua", "qué está pasando", "que esta pasando",
        "hoy", "reciente", "recientes", "quien", "quién", "dónde", "donde", "cuándo", "cuando","que"
    ]
    return any(k in t for k in keys)

def compute_analytics_obj(question: str):
    return None, ""

def format_tavily_context(tav_result: dict) -> str:
    if not isinstance(tav_result, dict):
        return ""
    results = tav_result.get("results", []) or []
    lines = []
    for r in results[:5]:
        title = r.get("title", "")
        content = r.get("content", "")
        url = r.get("url", "")
        lines.append(f"- {title}\n  {content}\n  {url}")
    return "\n".join(lines).strip()

def build_answer_from_analytics_text(analytics_obj, detailed: bool = False) -> str:
    if not analytics_obj:
        return "No encontré datos analíticos para responder eso."
    return str(analytics_obj)


# ===============================
# UTILIDADES PARA CHAT
# ===============================

def _sanitize_chat_id(chat_id: str) -> str:
    chat_id = (chat_id or "default").strip().lower()
    chat_id = re.sub(r"[^a-z0-9_\-]", "-", chat_id)
    return chat_id or "default"

@app.get("/tasks")
def list_tasks():
    return {"ok": True, "tasks": _load_tasks()}

@app.post("/tasks")
def create_task(req: TaskCreateReq):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text vacío")

    now = datetime.now(TZ)
    cid = _sanitize_chat_id((req.chat_id or "default").strip())

    if req.run_at:
        try:
            run_at = datetime.fromisoformat(req.run_at)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=TZ)
        except:
            raise HTTPException(status_code=400, detail="run_at inválido (usa ISO 8601)")
    elif req.in_minutes is not None:
        run_at = now + timedelta(minutes=int(req.in_minutes))
    else:
        run_at = now + timedelta(minutes=10)

    task = {
        "id": uuid.uuid4().hex[:10],
        "text": text,
        "run_at": run_at.isoformat(),
        "done": False,
        "created_at": now.isoformat(),
        "chat_id": cid,
    }

    tasks = _load_tasks()
    tasks.append(task)
    _save_tasks(tasks)
    _schedule_task(task)
    return {"ok": True, "task": task}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    tasks = _load_tasks()
    new_tasks = [t for t in tasks if t.get("id") != task_id]
    if len(new_tasks) == len(tasks):
        raise HTTPException(status_code=404, detail="task no encontrada")
    _save_tasks(new_tasks)
    try:
        scheduler.remove_job(f"task_{task_id}")
    except:
        pass
    return {"ok": True, "deleted": task_id}

# ===============================
# ✅ Health Ollama
# ===============================
@app.get("/health/ollama")
def health_ollama():
    try:
        r = requests.get(OLLAMA_TAGS_URL, timeout=5)
        r.raise_for_status()
        return {"ok": True, "ollama": True, "ollama_host": OLLAMA_HOST, "ollama_port": OLLAMA_PORT, "models": r.json()}
    except Exception as e:
        return {"ok": True, "ollama": False, "ollama_host": OLLAMA_HOST, "ollama_port": OLLAMA_PORT, "error": str(e)}

# ===============================
# ✅ Root
# ===============================
@app.get("/")
def root():
    return RedirectResponse(url="/boot", status_code=302)

@app.get("/health")
def health():
    ollama_ok = False
    try:
        requests.get(OLLAMA_TAGS_URL, timeout=2).raise_for_status()
        ollama_ok = True
    except:
        ollama_ok = False

    return {
        "ok": True,
        "ollama_ok": ollama_ok,
        "ollama_url": OLLAMA_URL,
        "model": MODEL,
        "message": "Servidor listo: STT + Speaker PC + Firebase + Analítica + WS App + MultiChat"
    }

@app.get("/ready-status")
def ready_status():
    warmup_n8n()
    n8n_ok = is_n8n_awake()

    return {
        "ok": True,
        "spectra": True,
        "n8n": n8n_ok,
        "ready": n8n_ok
    }


@app.get("/boot", response_class=HTMLResponse)
def boot_screen():
    return """
    <!doctype html>
    <html lang="es">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Iniciando Spectra</title>
      <style>
        body{
          margin:0;
          min-height:100vh;
          display:flex;
          align-items:center;
          justify-content:center;
          background:
            radial-gradient(circle at top, #0b1120 0, #020617 45%, #000 100%);
          color:white;
          font-family:Arial, sans-serif;
        }
        .box{
          text-align:center;
          padding:34px;
          border-radius:24px;
          background:rgba(255,255,255,.04);
          border:1px solid rgba(255,255,255,.08);
          width:min(92%, 560px);
          box-shadow:0 0 30px rgba(34,211,238,.12);
        }
        .spinner{
          width:58px;
          height:58px;
          border:5px solid rgba(255,255,255,.14);
          border-top:5px solid #22d3ee;
          border-radius:50%;
          margin:0 auto 20px;
          animation:spin 1s linear infinite;
        }
        @keyframes spin{
          to{ transform:rotate(360deg); }
        }
        h2{
          margin:0 0 12px;
          font-size:28px;
          letter-spacing:.04em;
        }
        p{
          color:#cbd5e1;
          line-height:1.6;
          margin:0;
        }
        #state{
          margin-top:18px;
          font-size:14px;
          color:#67e8f9;
        }
      </style>
    </head>
    <body>
      <div class="box">
        <div class="spinner"></div>
        <h2>Iniciando Spectra AI</h2>
        <p>
          Espera un momento mientras despertamos todos los servicios
          esto puede tardar de 2 a 4 minutos.
        </p>
        <div id="state">Verificando servicios...</div>
      </div>

      <script>
        async function checkReady(){
          try{
            const r = await fetch("/ready-status", { cache: "no-store" });
            const j = await r.json();

            if(j.ready){
              document.getElementById("state").textContent = "Todo listo. Entrando al login...";
              setTimeout(() => {
                window.location.href = "/login";
              }, 900);
              return;
            }

            document.getElementById("state").textContent = "Esperando a que n8n despierte...";
          }catch(e){
            document.getElementById("state").textContent = "Conectando con servicios...";
          }

          setTimeout(checkReady, 2500);
        }

        checkReady();
      </script>
    </body>
    </html>
    """

@app.get("/warmup")
def warmup():
    warmup_n8n()
    return {"ok": True, "message": "Spectra y n8n despertando"}

# ===============================
# 🔥 Endpoints Firebase
# ===============================
@app.get("/firebase/sensores")
def get_sensores():
    data = fetch_firebase_json("/")
    return {"ok": True, "data": data}

@app.get("/firebase/ultima")
def firebase_ultima():
    data = fetch_firebase_json("/")
    mediciones = get_mediciones_dict(data)
    last = pick_latest_medicion(mediciones)
    return {"ok": True, "last": last, "count": len(mediciones)}


from pydantic import BaseModel
from typing import Optional

class AskReq(BaseModel):
    question: str
    chat_id: Optional[str] = "default"

def ask_ollama(prompt: str) -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    }

    r = requests.post(OLLAMA_URL, json=payload, timeout=120)
    r.raise_for_status()

    data = r.json()
    return (data.get("response") or "").strip()

# ===============================
# ✅ /ask (texto)
# ===============================
@app.post("/ask")
def ask(req: AskReq):
    try:
        chat_id = _sanitize_chat_id(req.chat_id or "default")
        _ensure_chat_exists(chat_id)

        detailed = wants_detailed(req.question)
        system_style = SYSTEM_STYLE_LONG if detailed else SYSTEM_STYLE_SHORT
        max_chars = MAX_CHARS_LONG if detailed else MAX_CHARS_SHORT

        sensores_ctx = ""
        analytics_obj = None
        if needs_sensors_context(req.question):
            analytics_obj, sensores_ctx = compute_analytics_obj(req.question)

        web_ctx = ""
        tav_answer = ""
        if needs_online(req.question):
            tav = tavily_search(req.question, max_results=5)
            if tav.get("ok"):
                tav_answer = (tav.get("answer") or "").strip()
                web_ctx = format_tavily_context(tav)

                if tav_answer:
                    web_ctx = f"Respuesta breve Tavily:\n{tav_answer}\n\nFuentes:\n{web_ctx}"
            else:
                web_ctx = f"No se pudo buscar en internet: {tav.get('error','')}"

        prompt = (
            f"{system_style}\n\n"
            + (f"{sensores_ctx}\n\n" if sensores_ctx else "")
            + (f"Contexto web actualizado:\n{web_ctx}\n\n" if web_ctx else "")
            + f"Usuario: {req.question}\n"
            f"Asistente:"
        )

        try:
            raw_answer = ask_gemini(prompt)
            answer = compact_answer(raw_answer, max_chars=max_chars)
        except Exception as e:
            print("❌ /ask error en Ollama:", repr(e))

            if analytics_obj:
                answer = compact_answer(
                    build_answer_from_analytics_text(analytics_obj, detailed=detailed),
                    max_chars=max_chars
                )
            elif tav_answer:
                answer = compact_answer(
                    f"Según la búsqueda web: {tav_answer}",
                    max_chars=max_chars
                )
            elif web_ctx:
                answer = compact_answer(
                    f"Encontré contexto web, pero no pude procesarlo con Ollama.\n\n{web_ctx}",
                    max_chars=max_chars
                )
            else:
                answer = compact_answer(
                    "Spectra: En este servidor no hay motor de IA activo (Ollama no está disponible).",
                    max_chars=max_chars
                )

        save_chat_event(
            "ask",
            user_text=req.question,
            assistant_text=answer,
            meta={
                "used_web": bool(web_ctx),
                "used_sensors": bool(sensores_ctx),
                "detailed": detailed,
                "tavily_answer": tav_answer,
            },
            chat_id=chat_id
        )

        return {
            "ok": True,
            "answer": answer,
            "used_web": bool(web_ctx),
            "used_sensors": bool(sensores_ctx),
            "analytics": analytics_obj,
            "detailed": detailed,
            "chat_id": chat_id,
            "tavily_answer": tav_answer,
        }

    except Exception as e:
        import traceback
        print("❌ ERROR REAL EN /ask")
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(e)
        }
    
# ===============================
# ✅ /talk (voz)
# ===============================
@app.post("/talk")
async def talk(audio: UploadFile = File(...), chat_id: str = "default"):
    chat_id = _sanitize_chat_id(chat_id or "default")
    _ensure_chat_exists(chat_id)

    tmp_id = uuid.uuid4().hex
    in_path = os.path.join(TMP_DIR, f"in_{tmp_id}_{audio.filename}")
    wav_path = os.path.join(TMP_DIR, f"in_{tmp_id}.wav")

    try:
        with open(in_path, "wb") as f:
            f.write(await audio.read())

        # ✅ 1) Convertir a wav 16k (Render necesita ffmpeg instalado)
        cmd = ["ffmpeg", "-y", "-i", in_path, "-ac", "1", "-ar", "16000", wav_path]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            raise HTTPException(
                status_code=503,
                detail="FFmpeg no está instalado en el servidor. /talk requiere ffmpeg para convertir audio."
            )

        if p.returncode != 0:
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {p.stderr[:300]}")

        # ✅ 2) MOD3: Whisper lazy-load (NO usar whisper_model directo)
        model = get_whisper()
        if model is None:
            raise HTTPException(
                status_code=503,
                detail="Whisper no disponible en este servidor (falta dependencia o RAM)."
            )

        segments, info = model.transcribe(wav_path, language="es")
        transcript = " ".join([seg.text.strip() for seg in segments]).strip()

        if not transcript:
            raise HTTPException(status_code=400, detail="No se pudo transcribir (audio vacío o muy bajo)")

        # ============================================================
        # ✅ 0) DELETE EVENT (PRIORIDAD MÁXIMA)
        # ============================================================
        del_cmd = parse_delete_calendar_command(transcript)

        if del_cmd:
            target = del_cmd.get("target", "").strip()

            if not target:
                answer = compact_answer(
                    "Daniel, dime el nombre exacto del evento que quieres eliminar.",
                    MAX_CHARS_SHORT
                )

                save_chat_event(
                    "talk_calendar_delete_missing_target",
                    user_text=transcript,
                    assistant_text=answer,
                    meta={"delete_cmd": del_cmd},
                    chat_id=chat_id
                )

                await ws_broadcast({
                    "type": "talk",
                    "transcript": transcript,
                    "answer": answer,
                    "chat_id": chat_id
                })

                return {
                    "ok": False,
                    "transcript": transcript,
                    "answer": answer,
                    "chat_id": chat_id
                }

            try:
                answer, meta = resolve_delete_command_via_n8n(del_cmd, chat_id=chat_id)
            except Exception as e:
                answer = compact_answer(
                    "Daniel, hubo un error al intentar eliminar el evento. Revisa n8n.",
                    MAX_CHARS_SHORT
                )

                save_chat_event(
                    "talk_calendar_delete_error",
                    user_text=transcript,
                    assistant_text=answer,
                    meta={"error": str(e)},
                    chat_id=chat_id
                )

                await ws_broadcast({
                    "type": "talk",
                    "transcript": transcript,
                    "answer": answer,
                    "chat_id": chat_id
                })

                return {
                    "ok": False,
                    "transcript": transcript,
                    "answer": answer,
                    "chat_id": chat_id
                }

            if answer:
                answer = compact_answer(answer, MAX_CHARS_SHORT)

                save_chat_event(
                    "talk_calendar_delete",
                    user_text=transcript,
                    assistant_text=answer,
                    meta=meta,
                    chat_id=chat_id
                )

                await ws_broadcast({
                    "type": "talk",
                    "transcript": transcript,
                    "answer": answer,
                    "chat_id": chat_id
                })

                return {
                    "ok": True,
                    "transcript": transcript,
                    "answer": answer,
                    "delete": meta,
                    "chat_id": chat_id
                }

        # ============================================================
        # ✅ 1) CREAR EVENTO CALENDAR
        # ============================================================
        cal = parse_calendar_event_command(transcript)

        if cal and isinstance(cal, dict):

            if not cal.get("error") and all(k in cal for k in ["title", "start", "end"]):
                resp = crear_evento_calendar_via_n8n(cal["title"], cal["start"], cal["end"])

                if resp.get("ok"):
                    answer = compact_answer(
                        f"Listo, Daniel. Ya lo agendé en tu Google Calendar: {cal['title']}.",
                        MAX_CHARS_SHORT
                    )
                else:
                    answer = compact_answer(
                        "Daniel, intenté agendarlo pero falló n8n.",
                        MAX_CHARS_SHORT
                    )

                save_chat_event(
                    "talk_calendar",
                    user_text=transcript,
                    assistant_text=answer,
                    meta={"calendar_event": cal, "n8n": resp},
                    chat_id=chat_id
                )

                await ws_broadcast({"type": "talk", "transcript": transcript, "answer": answer, "chat_id": chat_id})

                return {
                    "ok": True,
                    "transcript": transcript,
                    "answer": answer,
                    "calendar": cal,
                    "n8n": resp,
                    "chat_id": chat_id
                }

            if cal.get("error"):
                answer = compact_answer(f"Daniel, {cal['error']}", MAX_CHARS_SHORT)

                save_chat_event(
                    "talk_calendar_error",
                    user_text=transcript,
                    assistant_text=answer,
                    meta={"calendar_event": cal},
                    chat_id=chat_id
                )

                await ws_broadcast({"type": "talk", "transcript": transcript, "answer": answer, "chat_id": chat_id})

                return {
                    "ok": False,
                    "transcript": transcript,
                    "answer": answer,
                    "calendar": cal,
                    "chat_id": chat_id
                }

        # ============================================================
        # ✅ 2) RECORDATORIO
        # ============================================================
        #rem = parse_reminder(transcript)
 #       if rem:
  #          minutes, task_text = rem
#
 #           task = create_task_internal(
  #              task_text,
   ##             minutes,
     #           chat_id=chat_id,
      #          create_calendar_event=True,
       #         calendar_title=f"{task_text}"
        #    )

        #    answer = compact_answer(
       #         f"Listo, Daniel. Te lo recuerdo en {minutes} minutos y también lo agendé en tu Google Calendar: {task_text}.",
        #        MAX_CHARS_SHORT
         #   )

        #    save_chat_event(
         #       "talk_reminder",
          #      user_text=transcript,
           #     assistant_text=answer,
            #    meta={"created_task": task},
             #   chat_id=chat_id
            #)

           # await ws_broadcast({"type": "talk", "transcript": transcript, "answer": answer, "chat_id": chat_id})

          #  return {
         #       "ok": True,
          #      "transcript": transcript,
           #     "answer": answer,
            #    "created_task": task,
             #   "chat_id": chat_id
           # }

        # ============================================================
        # ✅ 3) FLUJO NORMAL OLLAMA
        # ============================================================
        detailed = wants_detailed(transcript)
        system_style = SYSTEM_STYLE_LONG if detailed else SYSTEM_STYLE_SHORT
        max_chars = MAX_CHARS_LONG if detailed else MAX_CHARS_SHORT

        prompt = f"{system_style}\n\nUsuario: {transcript}\nAsistente:"

        try:
            raw_answer = ask_gemini(prompt)
            answer = compact_answer(raw_answer, max_chars=max_chars)
        except:
            answer = "Daniel, no pude conectar con Ollama."

        save_chat_event(
            "talk",
            user_text=transcript,
            assistant_text=answer,
            meta={},
            chat_id=chat_id
        )

        await ws_broadcast({"type": "talk", "transcript": transcript, "answer": answer, "chat_id": chat_id})

        return {
            "ok": True,
            "transcript": transcript,
            "answer": answer,
            "chat_id": chat_id
        }

    finally:
        for path in [in_path, wav_path]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except:
                pass
# ===============================
# PROXY ROUTES PARA FRONTEND
# ===============================

@app.get("/chats-proxy")
def chats_proxy():
    return api_list_chats()

@app.post("/chats-proxy")
def chats_create_proxy(req: ChatCreateReq):
    return api_create_chat(req)

@app.get("/chats-proxy/{chat_id}")
def chats_get_proxy(chat_id: str, limit: int = 120):
    return api_get_chat(chat_id, limit)

@app.delete("/chats-proxy/{chat_id}")
def chats_delete_proxy(chat_id: str):
    return api_delete_chat(chat_id)


@app.post("/ask-proxy")
def ask_proxy(req: AskReq):
    return ask(req)

@app.post("/talk-proxy")
async def talk_proxy(audio: UploadFile = File(...), chat_id: str = "default"):
    try:
        return await talk(audio=audio, chat_id=chat_id)
    except HTTPException as e:
        return {
            "ok": False,
            "error": str(e.detail),
            "transcript": "",
            "answer": f"Error: {e.detail}",
            "chat_id": chat_id
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(e),
            "transcript": "",
            "answer": f"Error interno: {e}",
            "chat_id": chat_id
        }

@app.get("/firebase/sensores-proxy")
def sensores_proxy():
    return get_sensores()


@app.get("/firebase/ultima-proxy")
def ultima_proxy():
    return firebase_ultima()


@app.get("/tasks-proxy")
def tasks_proxy():
    return list_tasks()


@app.delete("/tasks-proxy/{task_id}")
def delete_task_proxy(task_id: str):
    return delete_task(task_id)

@app.get("/api/lab/listar")
def api_lab_listar_fastapi():
    try:
        r = requests.get(N8N_LISTAR, timeout=30)
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/lab/entregar")
def api_lab_entregar_fastapi(payload: LabEntregarReq):
    try:
        body = {"id": payload.id}

        print("\n========== DEBUG FASTAPI ENTREGAR ==========")
        print("payload recibido:", payload.model_dump())
        print("body enviado a n8n:", body)
        print("URL N8N_ENTREGAR:", N8N_ENTREGAR)
        print("============================================\n")

        r = requests.post(N8N_ENTREGAR, json=body, timeout=30)

        try:
            data = r.json()
        except Exception:
            data = {"raw": (r.text or "")[:1000]}

        print("STATUS N8N:", r.status_code)
        print("RESPUESTA N8N:", data)

        if not r.ok:
            raise HTTPException(
                status_code=r.status_code,
                detail=data
            )

        return {
            "ok": True,
            "msg": data.get("msg") or data.get("message") or "Equipo entregado correctamente ✅",
            "id": payload.id,
            "data": data
        }

    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Timeout llamando a n8n /entregar")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.include_router(student_router)

# ===============================
# Montar Flask (login antiguo)
# ===============================
app.mount("/", WSGIMiddleware(flask_app))

for route in app.routes:
    try:
        print("RUTA FASTAPI:", route.path)
    except Exception:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
