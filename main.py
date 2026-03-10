from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from pydantic import BaseModel
from fastapi.middleware.wsgi import WSGIMiddleware

import sys
import os

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
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Spectra AI - Interfaz de Voz</title>
  <style>
    :root{
      --bg:#030718;
      --panel:rgba(7,14,34,.78);
      --panel2:rgba(10,18,42,.92);
      --line:rgba(130,170,255,.12);
      --text:#f4f7ff;
      --muted:rgba(216,226,255,.68);
      --muted2:rgba(216,226,255,.5);
      --cyan:#27d6ff;
      --blue:#5aa3ff;
      --purple:#9f5cff;
      --pink:#ff4fd8;
      --green:#36e07d;
      --orange:#ffb14a;
      --radius-xl:26px;
      --radius-lg:20px;
      --radius-md:16px;
      --radius-sm:12px;
      --shadow:0 20px 70px rgba(0,0,0,.45);
    }

    *{ box-sizing:border-box; }
    html,body{ height:100%; }
    body{
      margin:0;
      color:var(--text);
      font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background:
        radial-gradient(900px 500px at 10% 0%, rgba(39,214,255,.12), transparent 55%),
        radial-gradient(900px 600px at 100% 0%, rgba(159,92,255,.10), transparent 50%),
        linear-gradient(180deg, #020615 0%, #030816 100%);
      overflow:hidden;
    }

    body::before{
      content:"";
      position:fixed;
      inset:0;
      background:
        linear-gradient(to right, rgba(68,111,255,.02) 1px, transparent 1px),
        linear-gradient(to bottom, rgba(68,111,255,.015) 1px, transparent 1px);
      background-size:56px 56px;
      mask-image: radial-gradient(circle at center, rgba(0,0,0,.96), transparent 92%);
      pointer-events:none;
    }

    .shell{
      width:min(100%, 1600px);
      height:100vh;
      margin:0 auto;
      padding:20px 22px 18px;
    }

    .frame{
      height:100%;
      border-radius:28px;
      border:1px solid rgba(120,155,255,.13);
      background:linear-gradient(180deg, rgba(4,10,26,.88), rgba(2,7,20,.94));
      box-shadow:inset 0 0 0 1px rgba(255,255,255,.02), var(--shadow);
      padding:16px 18px;
      overflow:hidden;
    }

    .top{
      display:flex;
      justify-content:space-between;
      align-items:flex-start;
      gap:20px;
      padding:2px 4px 12px;
      border-bottom:1px solid rgba(255,255,255,.05);
    }

    .brand{
      display:flex;
      align-items:flex-start;
      gap:14px;
    }

    .orb{
      width:50px;
      height:50px;
      border-radius:50%;
      background:
        radial-gradient(circle at 35% 35%, rgba(255,255,255,.7), rgba(255,255,255,.12) 18%, transparent 22%),
        radial-gradient(circle at 35% 35%, #7df0ff 0%, #36d8ff 30%, #1593ff 62%, #0a4db8 100%);
      box-shadow:0 0 12px rgba(39,214,255,.45), 0 0 28px rgba(39,214,255,.22), inset 0 0 12px rgba(255,255,255,.28);
      border:1px solid rgba(255,255,255,.16);
      flex:0 0 50px;
    }

    .brandText .kicker{
      font-size:12px;
      letter-spacing:.34em;
      text-transform:uppercase;
      color:#4be4ff;
      margin-bottom:2px;
    }

    .brandText .title{
      font-size:22px;
      line-height:1.05;
      letter-spacing:.12em;
      font-weight:800;
      text-transform:uppercase;
      color:white;
    }

    .online{
      display:flex;
      align-items:center;
      gap:10px;
      padding:10px 16px;
      border-radius:999px;
      border:1px solid rgba(255,255,255,.09);
      background:rgba(255,255,255,.02);
      white-space:nowrap;
      margin-top:2px;
    }

    .dot{
      width:8px;
      height:8px;
      border-radius:50%;
      background:var(--green);
      box-shadow:0 0 10px rgba(54,224,125,.8);
      flex:0 0 8px;
    }

    .online .strong{
      font-size:13px;
      letter-spacing:.25em;
      text-transform:uppercase;
      font-weight:800;
    }

    .online .sub{
      font-size:13px;
      color:var(--muted);
    }

    .toolbar{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:18px;
      padding:14px 2px 10px;
    }

    .toolbarLeft, .toolbarRight{
      display:flex;
      align-items:center;
      gap:10px;
      flex-wrap:wrap;
    }

    .pill, .iconBtn{
      border:none;
      color:var(--text);
      cursor:pointer;
      border-radius:999px;
      padding:12px 18px;
      background:rgba(255,255,255,.035);
      border:1px solid rgba(255,255,255,.08);
      transition:.18s ease;
      font-weight:700;
      letter-spacing:.08em;
      text-transform:uppercase;
      font-size:13px;
      text-decoration:none;
    }

    .pill:hover, .iconBtn:hover{
      transform:translateY(-1px);
      border-color:rgba(120,170,255,.2);
      background:rgba(255,255,255,.05);
    }

    .pill.active{
      background:linear-gradient(180deg, rgba(0,198,255,.12), rgba(0,198,255,.06));
      border-color:rgba(39,214,255,.3);
    }

    .pill .accent{ color:var(--orange); margin-right:8px; }
    .pillNew .plus{ color:#a976ff; font-size:20px; line-height:0; vertical-align:middle; margin-right:8px; }

    .main{
      height:calc(100% - 126px);
      padding-top:2px;
    }

    .view{ height:100%; display:none; }
    .view.active{ display:block; }

    .coreGrid{
      height:100%;
      display:grid;
      grid-template-columns:1.42fr .95fr;
      gap:18px;
    }

    .panel{
      min-height:0;
      background:linear-gradient(180deg, rgba(5,11,28,.58), rgba(5,11,28,.4));
      border:1px solid rgba(124,157,255,.10);
      border-radius:24px;
      box-shadow:inset 0 0 0 1px rgba(255,255,255,.02);
      overflow:hidden;
    }

    .leftPanel, .rightPanel, .remPanel{
      padding:16px 16px 14px;
      display:flex;
      flex-direction:column;
      min-height:0;
      height:100%;
    }

    .sectionHead{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:16px;
      margin-bottom:12px;
    }

    .sectionTitle{
      font-size:15px;
      font-weight:800;
      letter-spacing:.22em;
      text-transform:uppercase;
      color:#dfe9ff;
    }

    .sectionMeta{
      font-size:14px;
      color:var(--muted);
    }

    .chatPanel{
      background:rgba(255,255,255,.02);
      border:1px solid rgba(255,255,255,.06);
      border-radius:20px;
      padding:14px 14px 12px;
      min-height:0;
      display:flex;
      flex-direction:column;
      flex:1;
    }

    .chatHead{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:12px;
      margin-bottom:10px;
    }

    .miniLabel{ font-size:14px; color:#dce8ff; }

    .chatBadge{
      border-radius:999px;
      padding:8px 14px;
      background:rgba(130,160,255,.06);
      border:1px solid rgba(130,160,255,.16);
      color:#eef4ff;
      font-size:14px;
      white-space:nowrap;
    }

    .chatList, .messagesBox, .tasksBox{
      overflow:auto;
    }

    .chatList{
      display:flex;
      flex-direction:column;
      gap:10px;
      max-height:280px;
      padding-right:4px;
    }

    .chatRow{
      display:grid;
      grid-template-columns:1fr auto;
      gap:12px;
      align-items:center;
      padding:14px;
      border-radius:16px;
      border:1px solid rgba(255,255,255,.08);
      background:rgba(255,255,255,.02);
      cursor:pointer;
      transition:.16s ease;
    }

    .chatRow:hover{ border-color:rgba(39,214,255,.22); background:rgba(255,255,255,.03); }
    .chatRow.active{
      background:linear-gradient(180deg, rgba(0,198,255,.13), rgba(0,198,255,.07));
      border-color:rgba(39,214,255,.35);
    }

    .chatTitle{
      font-size:15px;
      font-weight:800;
      color:#f2f7ff;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
      margin-bottom:4px;
    }

    .chatMeta{ font-size:13px; color:var(--muted2); }

    .deleteChat{
      width:34px;
      height:34px;
      border-radius:12px;
      border:1px solid rgba(255,110,160,.28);
      background:rgba(255,85,136,.08);
      color:#ffd6e7;
      cursor:pointer;
      font-size:15px;
    }

    .messagesWrap{
      margin-top:12px;
      min-height:0;
      flex:1;
      display:flex;
      flex-direction:column;
    }

    .messagesBox, .tasksBox{
      flex:1;
      min-height:72px;
      border-radius:16px;
      border:1px solid rgba(255,255,255,.06);
      background:rgba(255,255,255,.015);
      padding:12px;
      display:flex;
      flex-direction:column;
      gap:10px;
    }

    .msg, .taskItem{
      border-radius:14px;
      border:1px solid rgba(255,255,255,.07);
      background:rgba(255,255,255,.02);
      padding:10px 12px;
    }

    .msg .meta, .taskMeta{
      font-size:12px;
      color:var(--muted2);
      margin-bottom:6px;
    }

    .msg .line, .taskText{
      font-size:14px;
      color:#eff5ff;
      line-height:1.5;
      white-space:pre-wrap;
      word-break:break-word;
    }

    .taskTop{
      display:flex;
      justify-content:space-between;
      gap:12px;
      align-items:flex-start;
    }

    .taskActions{
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      margin-top:10px;
    }

    .smallBtn{
      border:none;
      border-radius:10px;
      border:1px solid rgba(255,255,255,.08);
      background:rgba(255,255,255,.05);
      color:#eef5ff;
      padding:8px 12px;
      cursor:pointer;
      font-size:12px;
      font-weight:700;
    }

    .smallBtn.danger{
      border-color:rgba(255,110,160,.25);
      background:rgba(255,85,136,.08);
      color:#ffdbe8;
    }

    .voiceCard{
      flex:1;
      min-height:0;
      background:linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.015));
      border:1px solid rgba(255,255,255,.06);
      border-radius:22px;
      padding:16px;
      display:flex;
      flex-direction:column;
      gap:12px;
    }

    .voiceTitle{ font-size:16px; line-height:1.4; }

    .spacePill{
      display:inline-flex;
      align-items:center;
      gap:12px;
      width:max-content;
      padding:10px 16px;
      border-radius:999px;
      border:1px dashed rgba(255,255,255,.16);
      background:rgba(255,255,255,.015);
    }

    .spaceKey{
      padding:6px 12px;
      border-radius:10px;
      border:1px solid rgba(255,255,255,.16);
      background:rgba(255,255,255,.03);
      font-weight:800;
      letter-spacing:.1em;
    }

    .spaceText{ color:var(--muted); font-size:14px; }

    .micWrap{ display:flex; justify-content:center; align-items:center; padding:6px 0 2px; }

    .micOuter{
      position:relative;
      width:126px;
      height:126px;
      border-radius:50%;
      display:grid;
      place-items:center;
      background:
        radial-gradient(circle at center, rgba(255,255,255,.04) 0%, rgba(255,255,255,.02) 42%, transparent 60%),
        radial-gradient(circle at center, rgba(39,214,255,.12), rgba(159,92,255,.08), transparent 70%);
      box-shadow:0 0 28px rgba(39,214,255,.12), 0 0 60px rgba(159,92,255,.08), inset 0 0 20px rgba(255,255,255,.03);
    }

    .micOuter::before{
      content:"";
      position:absolute;
      inset:14px;
      border-radius:50%;
      border:1px solid rgba(255,255,255,.06);
    }

    .micInner{
      width:54px;
      height:54px;
      border-radius:50%;
      display:grid;
      place-items:center;
      background:linear-gradient(135deg, rgba(39,214,255,.95), rgba(159,92,255,.92), rgba(255,79,216,.9));
      box-shadow:0 0 18px rgba(39,214,255,.25), 0 0 24px rgba(159,92,255,.2);
      font-size:22px;
    }

    .micStatus{
      text-align:center;
      color:var(--muted);
      font-size:13px;
      margin-top:-4px;
    }

    .responseBox{
      min-height:96px;
      max-height:160px;
      overflow:auto;
      border-radius:16px;
      border:1px dashed rgba(255,255,255,.1);
      background:rgba(255,255,255,.015);
      padding:16px;
      color:#d7e4ff;
      display:flex;
      align-items:center;
      justify-content:center;
      text-align:center;
      line-height:1.55;
      white-space:pre-wrap;
      word-break:break-word;
    }

    .qaRow{
      display:grid;
      grid-template-columns:1fr auto;
      gap:10px;
      align-items:center;
    }

    .askInput{
      height:46px;
      border-radius:14px;
      border:1px solid rgba(255,255,255,.08);
      background:rgba(4,8,20,.7);
      color:#eff6ff;
      padding:0 14px;
      font-size:14px;
      outline:none;
      width:100%;
    }

    .askBtn{
      height:46px;
      border-radius:14px;
      border:1px solid rgba(255,255,255,.08);
      background:rgba(255,255,255,.06);
      color:#f5f8ff;
      padding:0 18px;
      font-weight:800;
      cursor:pointer;
    }

    .quickBtns{
      display:flex;
      flex-wrap:wrap;
      gap:10px;
    }

    .quickBtn{
      border:none;
      border-radius:14px;
      border:1px solid rgba(255,255,255,.08);
      background:rgba(255,255,255,.04);
      color:#eef5ff;
      padding:12px 16px;
      cursor:pointer;
      font-weight:700;
    }

    .quickBtn.on{
      background:rgba(54,224,125,.12);
      border-color:rgba(54,224,125,.28);
      color:#d7ffe8;
    }

    .bottomBar{
      display:flex;
      justify-content:space-between;
      align-items:center;
      gap:14px;
      margin-top:6px;
      color:var(--muted2);
      font-size:12px;
    }

    .reminderBadge{
      display:none;
      margin-left:8px;
      min-width:20px;
      height:20px;
      padding:0 6px;
      border-radius:999px;
      background:rgba(255,177,74,.18);
      border:1px solid rgba(255,177,74,.35);
      color:#ffe7c0;
      font-size:12px;
      line-height:18px;
      text-align:center;
      vertical-align:middle;
    }

    .registroWrap{
      height:100%;
      border-radius:24px;
      border:1px solid rgba(124,157,255,.10);
      overflow:hidden;
      background:rgba(5,11,28,.55);
    }

    .registroFrame{
      width:100%;
      height:100%;
      border:none;
      background:#07112a;
    }

    @media (max-width:1200px){
      .coreGrid{ grid-template-columns:1fr; }
    }

    @media (max-width:720px){
      .shell{ padding:10px; }
      .frame{ padding:12px; border-radius:20px; }
      .top, .toolbar{ flex-direction:column; align-items:flex-start; }
      .qaRow{ grid-template-columns:1fr; }
      .chatHead, .sectionHead{ flex-direction:column; align-items:flex-start; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="frame">
      <div class="top">
        <div class="brand">
          <div class="orb"></div>
          <div class="brandText">
            <div class="kicker">INTERFAZ DE VOZ</div>
            <div class="title">SPECTRA AI</div>
          </div>
        </div>

        <div class="online">
          <div class="dot"></div>
          <div class="strong">ONLINE</div>
          <div class="sub">Listo para escuchar</div>
        </div>
      </div>

      <div class="toolbar">
        <div class="toolbarLeft">
          <button class="pill active" id="tabCore"><span class="accent">⚡</span>CORE</button>
          <button class="pill" id="tabRem">⏱ RECORDATORIOS <span class="reminderBadge" id="remBadge">0</span></button>
          <button class="pill" id="tabRegistro">📦 REGISTRO</button>
          <button class="pill pillNew" id="btnNewChat"><span class="plus">＋</span>NUEVO CHAT</button>
        </div>

        <div class="toolbarRight">
          <a class="iconBtn" href="/speaker-redirect" target="_blank">🔊 SPEAKER PC</a>
        </div>
      </div>

      <div class="main">
        <div class="view active" id="viewCore">
          <div class="coreGrid">
            <div class="panel leftPanel">
              <div class="sectionHead">
                <div class="sectionTitle">REGISTRO DE CONVERSACIÓN</div>
                <div class="sectionMeta">Mensajes recientes</div>
              </div>

              <div class="chatPanel">
                <div class="chatHead">
                  <div class="miniLabel">Tus chats ▼</div>
                  <div class="chatBadge" id="activeChatLabel">Chat: Default</div>
                </div>

                <div class="chatList" id="chatsList"></div>

                <div class="messagesWrap">
                  <div class="messagesBox" id="coreList"></div>
                </div>
              </div>
            </div>

            <div class="panel rightPanel">
              <div class="voiceCard">
                <div class="voiceTitle">
                  Mantén presionada la tecla <b>ESPACIO</b> para hablar con Spectra AI.
                  Suelta la tecla para enviar el mensaje.
                </div>

                <div class="spacePill">
                  <div class="spaceKey">SPACE</div>
                  <div class="spaceText">Presiona y mantén para grabar</div>
                </div>

                <div class="micWrap">
                  <div class="micOuter">
                    <div class="micInner">🎙</div>
                  </div>
                </div>

                <div class="micStatus" id="micStatus">Espera tranquila... Listo para hablar con Spectra</div>

                <div class="responseBox" id="liveBox">Aquí verás la transcripción y la respuesta...</div>

                <div class="qaRow">
                  <input id="q" class="askInput" placeholder="Escribe una pregunta (opcional)..." />
                  <button class="askBtn" id="btnAsk">Preguntar</button>
                </div>

                <div class="quickBtns">
                  <button class="quickBtn" id="btnUltima">Última medición</button>
                  <button class="quickBtn" id="btnCrudo">Sensores (crudo)</button>
                  <button class="quickBtn" id="btnRefreshChats">Actualizar chats</button>
                </div>

                <div class="quickBtns">
                  <button class="quickBtn" id="btnNotif">Habilitar notificaciones</button>
                  <button class="quickBtn" id="btnNotifTest">Probar notificación</button>
                  <button class="quickBtn" id="btnOpenSpeaker">Abrir Speaker PC</button>
                </div>

                <div class="quickBtns">
                  <button class="quickBtn" id="btnLoad">Cargar historial</button>
                </div>

                <div class="bottomBar">
                  <div id="status">WS: desconectado</div>
                  <div id="miniState">Chat activo: default</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="view" id="viewRem">
          <div class="panel remPanel">
            <div class="sectionHead">
              <div class="sectionTitle">RECORDATORIOS</div>
              <div class="sectionMeta">Tareas y recordatorios programados</div>
            </div>

            <div class="tasksBox" id="remList"></div>
          </div>
        </div>

        <div class="view" id="viewRegistro">
          <div class="registroWrap">
            <iframe class="registroFrame" src="/registro-embed"></iframe>
          </div>
        </div>
      </div>
    </div>
  </div>

<script>
  const tabCore = document.getElementById("tabCore");
  const tabRem = document.getElementById("tabRem");
  const tabRegistro = document.getElementById("tabRegistro");
  const viewCore = document.getElementById("viewCore");
  const viewRem = document.getElementById("viewRem");
  const viewRegistro = document.getElementById("viewRegistro");

  const remBadge = document.getElementById("remBadge");
  const chatsListEl = document.getElementById("chatsList");
  const coreList = document.getElementById("coreList");
  const remList = document.getElementById("remList");
  const activeChatLabel = document.getElementById("activeChatLabel");
  const miniState = document.getElementById("miniState");
  const liveBox = document.getElementById("liveBox");
  const status = document.getElementById("status");
  const micStatus = document.getElementById("micStatus");
  const btnNotif = document.getElementById("btnNotif");

  let unreadReminders = 0;
  let currentChatId = localStorage.getItem("spectra_chat_id") || "default";
  let notificationsEnabled = false;

  function escapeHtml(s){
    return (s || "")
      .replaceAll("&","&amp;")
      .replaceAll("<","&lt;")
      .replaceAll(">","&gt;");
  }

  function setView(name){
    [viewCore, viewRem, viewRegistro].forEach(v => v.classList.remove("active"));
    [tabCore, tabRem, tabRegistro].forEach(t => t.classList.remove("active"));

    if(name === "core"){
      viewCore.classList.add("active");
      tabCore.classList.add("active");
    }else if(name === "rem"){
      viewRem.classList.add("active");
      tabRem.classList.add("active");
      unreadReminders = 0;
      remBadge.textContent = "0";
      remBadge.style.display = "none";
      loadTasks();
    }else{
      viewRegistro.classList.add("active");
      tabRegistro.classList.add("active");
    }
  }

  tabCore.onclick = () => setView("core");
  tabRem.onclick = () => setView("rem");
  tabRegistro.onclick = () => setView("registro");

  function setCurrentChat(id, title){
    currentChatId = id || "default";
    localStorage.setItem("spectra_chat_id", currentChatId);
    activeChatLabel.textContent = "Chat: " + (title || currentChatId);
    miniState.textContent = "Chat activo: " + currentChatId;

    document.querySelectorAll(".chatRow").forEach(el => el.classList.remove("active"));
    const current = document.querySelector(`[data-chat-id="${currentChatId}"]`);
    if(current) current.classList.add("active");
  }

  function renderChat(user, assistant, ts){
    const div = document.createElement("div");
    div.className = "msg";
    div.innerHTML = `
      <div class="meta">${escapeHtml(ts || "")}</div>
      ${user ? `<div class="line"><b>Tú:</b> ${escapeHtml(user)}</div>` : ``}
      ${assistant ? `<div class="line"><b>Spectra:</b> ${escapeHtml(assistant)}</div>` : ``}
    `;
    coreList.prepend(div);
  }

  function renderTask(task){
    const text = task.text || task.assistant || "Recordatorio";
    const runAt = task.run_at || task.created_at || "";
    const done = !!task.done;

    const div = document.createElement("div");
    div.className = "taskItem";
    div.innerHTML = `
      <div class="taskTop">
        <div>
          <div class="taskText">${done ? "✅ " : "⏰ "}${escapeHtml(text)}</div>
          <div class="taskMeta">${escapeHtml(runAt)}</div>
        </div>
      </div>
      <div class="taskActions">
        ${!done ? `<button class="smallBtn danger" data-del="${escapeHtml(task.id || "")}">Eliminar</button>` : ``}
      </div>
    `;

    const del = div.querySelector("[data-del]");
    if(del){
      del.addEventListener("click", async () => {
        const id = del.getAttribute("data-del");
        if(!id) return;
        if(!confirm("¿Eliminar este recordatorio?")) return;
        try{
          const r = await fetch(`/tasks-proxy/${encodeURIComponent(id)}`, { method:"DELETE" });
          if(!r.ok) throw new Error("No se pudo eliminar");
          await loadTasks();
        }catch(e){
          alert("No pude eliminar el recordatorio ❌");
        }
      });
    }

    remList.appendChild(div);
  }

  async function loadTasks(){
    try{
      remList.innerHTML = "";
      const r = await fetch("/tasks-proxy");
      const j = await r.json();
      const tasks = j.tasks || [];
      if(!tasks.length){
        remList.innerHTML = `<div class="msg"><div class="line">No hay recordatorios todavía.</div></div>`;
        return;
      }

      const ordered = [...tasks].sort((a,b) => String(b.run_at || "").localeCompare(String(a.run_at || "")));
      ordered.forEach(renderTask);
    }catch(e){
      remList.innerHTML = `<div class="msg"><div class="line">No pude cargar recordatorios ❌</div></div>`;
    }
  }

  async function loadChatsList(){
    try{
      const r = await fetch("/chats-proxy", {
        method:"GET",
        headers:{ "Cache-Control":"no-cache" }
      });

      const text = await r.text();
      let j = {};
      try{ j = JSON.parse(text); }catch(e){ throw new Error(text || "Respuesta inválida"); }

      if(!r.ok) throw new Error(j.error || "No se pudo cargar chats");

      const chats = j.chats || [];
      chatsListEl.innerHTML = "";

      if(!chats.find(c => c.id === "default")){
        chats.unshift({id:"default", title:"Default", updated_at:""});
      }

      chats.forEach(c => {
        const row = document.createElement("div");
        row.className = "chatRow";
        row.setAttribute("data-chat-id", c.id);
        row.innerHTML = `
          <div>
            <div class="chatTitle">${escapeHtml(c.title || c.id)}</div>
            <div class="chatMeta">${escapeHtml(c.updated_at || "")}</div>
          </div>
          <button class="deleteChat" data-del="${escapeHtml(c.id)}">🗑</button>
        `;

        row.addEventListener("click", async (e) => {
          if(e.target.closest(".deleteChat")) return;
          setCurrentChat(c.id, c.title || c.id);
          await loadHistory();
        });

        const delBtn = row.querySelector(".deleteChat");
        delBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const id = delBtn.getAttribute("data-del");
          if(id === "default"){
            alert("No se puede borrar el chat Default.");
            return;
          }
          if(!confirm("¿Eliminar este chat?")) return;
          try{
            const resp = await fetch(`/chats-proxy/${encodeURIComponent(id)}`, { method:"DELETE" });
            if(!resp.ok) throw new Error("No se pudo borrar");
            if(currentChatId === id){
              setCurrentChat("default", "Default");
              coreList.innerHTML = "";
            }
            await loadChatsList();
            await loadHistory();
          }catch(err){
            alert("Error eliminando chat ❌");
          }
        });

        chatsListEl.appendChild(row);
      });

      const current = chats.find(x => x.id === currentChatId) || chats[0];
      if(current) setCurrentChat(current.id, current.title || current.id);
    }catch(e){
      console.error(e);
      chatsListEl.innerHTML = `<div class="msg"><div class="line">No pude cargar chats ❌</div></div>`;
    }
  }

  async function loadHistory(){
    try{
      const r = await fetch(`/chats-proxy/${encodeURIComponent(currentChatId)}?limit=120`, {
        headers:{ "Cache-Control":"no-cache" }
      });
      const j = await r.json();
      const hist = j.history || [];

      coreList.innerHTML = "";
      hist.forEach(item => {
        const kind = item.kind || "";
        if(kind === "ask" || kind === "talk" || kind === "talk_reminder" || kind === "talk_calendar" || kind === "talk_calendar_delete"){
          if(item.user || item.assistant){
            renderChat(item.user || "", item.assistant || "", item.ts || "");
          }
        }
      });

      if(!hist.length){
        coreList.innerHTML = `<div class="msg"><div class="line">Este chat no tiene mensajes todavía.</div></div>`;
      }
    }catch(e){
      console.error(e);
      coreList.innerHTML = `<div class="msg"><div class="line">No pude cargar historial ❌</div></div>`;
    }
  }

  async function refreshNotificationState(){
    try{
      const r = await fetch("/notifications/status");
      const j = await r.json();
      notificationsEnabled = !!(j.state && j.state.enabled);

      if(notificationsEnabled){
        btnNotif.textContent = "Notificaciones activadas";
        btnNotif.classList.add("on");
      }else{
        btnNotif.textContent = "Habilitar notificaciones";
        btnNotif.classList.remove("on");
      }
    }catch(e){
      btnNotif.textContent = "Habilitar notificaciones";
      btnNotif.classList.remove("on");
    }
  }

  document.getElementById("btnRefreshChats").onclick = async () => { await loadChatsList(); };
  document.getElementById("btnLoad").onclick = async () => { await loadHistory(); };

  document.getElementById("btnNewChat").onclick = async () => {
    try{
      const r = await fetch("/chats-proxy", {
        method:"POST",
        headers:{ "Content-Type":"application/json" },
        body: JSON.stringify({ title:"Nuevo chat" })
      });

      const text = await r.text();
      let j = {};
      try{ j = JSON.parse(text); }catch(e){ throw new Error(text || "Respuesta inválida"); }

      if(!r.ok || !j.chat) throw new Error(j.error || "No pude crear chat");

      const chat = j.chat;
      await loadChatsList();
      setCurrentChat(chat.id, chat.title || chat.id);
      coreList.innerHTML = "";
      liveBox.textContent = "Aquí verás la transcripción y la respuesta...";
      await loadHistory();
      setView("core");
    }catch(e){
      console.error(e);
      alert("No pude crear chat ❌");
    }
  };

  document.getElementById("btnAsk").onclick = async () => {
    const q = document.getElementById("q").value.trim();
    if(!q) return;
    document.getElementById("q").value = "";
    micStatus.textContent = "Consultando a Spectra...";
    try{
      const r = await fetch("/ask-proxy", {
        method:"POST",
        headers:{ "Content-Type":"application/json" },
        body: JSON.stringify({ question:q, chat_id:currentChatId })
      });
      const j = await r.json();
      const answer = j.answer || "";
      renderChat(q, answer, new Date().toISOString());
      liveBox.textContent = answer || "Sin respuesta";
      await loadChatsList();
    }catch(e){
      liveBox.textContent = "Error consultando /ask";
    }finally{
      micStatus.textContent = "Espera tranquila... Listo para hablar con Spectra";
    }
  };

  document.getElementById("btnOpenSpeaker").onclick = () => {
    window.open("/speaker-redirect", "_blank");
  };

  document.getElementById("btnUltima").onclick = async () => {
    try{
      const r = await fetch("/firebase/ultima-proxy");
      const j = await r.json();
      liveBox.textContent = JSON.stringify(j.last || {}, null, 2);
    }catch(e){
      liveBox.textContent = "No pude cargar la última medición ❌";
    }
  };

  document.getElementById("btnCrudo").onclick = async () => {
    try{
      const r = await fetch("/firebase/sensores-proxy");
      const j = await r.json();
      liveBox.textContent = JSON.stringify(j.data || {}, null, 2).slice(0, 2500);
    }catch(e){
      liveBox.textContent = "No pude cargar sensores ❌";
    }
  };

  document.getElementById("btnNotif").onclick = async () => {
    try{
      const route = notificationsEnabled ? "/notifications/disable" : "/notifications/enable";
      const r = await fetch(route, { method:"POST" });
      if(!r.ok) throw new Error("No se pudo cambiar estado");
      await refreshNotificationState();
    }catch(e){
      alert("No pude cambiar el estado de notificaciones ❌");
    }
  };

  document.getElementById("btnNotifTest").onclick = async () => {
    try{
      const r = await fetch("/notifications/test", { method:"POST" });
      const j = await r.json();
      liveBox.textContent = j.message || "Notificación enviada";
      alert(j.message || "Notificación de prueba enviada ✅");
    }catch(e){
      alert("No pude enviar la notificación de prueba ❌");
    }
  };

  document.addEventListener("keydown", (e) => {
    if(e.code === "Space" && !["INPUT","TEXTAREA"].includes(document.activeElement.tagName)){
      e.preventDefault();
      micStatus.textContent = "Grabación por SPACE aún no está conectada en esta versión.";
    }
  });

  const wsProto = location.protocol === "https:" ? "wss" : "ws";

  const wsCore = new WebSocket(`${wsProto}://${location.host}/ws`);
  wsCore.onopen = () => status.textContent = "WS: conectado ✅";
  wsCore.onclose = () => status.textContent = "WS: desconectado ❌";
  wsCore.onerror = () => status.textContent = "WS: error ❌";

  wsCore.onmessage = (ev) => {
    try{
      const data = JSON.parse(ev.data);
      if(data.type === "talk"){
        const msgChat = data.chat_id || "default";
        if(msgChat !== currentChatId) return;
        renderChat(data.transcript || "", data.answer || "", new Date().toISOString());
        liveBox.textContent = (data.transcript || "") + "\n\n" + (data.answer || "");
      }
    }catch(e){}
  };

  const wsApp = new WebSocket(`${wsProto}://${location.host}/ws-app`);
  wsApp.onmessage = (ev) => {
    try{
      const data = JSON.parse(ev.data);
      if(data.type === "reminder"){
        const msgChat = data.chat_id || "default";
        if(msgChat !== currentChatId) return;

        if(notificationsEnabled && "Notification" in window){
          if(Notification.permission === "granted"){
            new Notification("Spectra AI", {
              body: data.text || "Tienes un recordatorio"
            });
          }
        }

        unreadReminders += 1;
        remBadge.style.display = "inline-block";
        remBadge.textContent = String(unreadReminders);
        loadTasks();
      }
    }catch(e){}
  };

  setInterval(() => {
    if(wsCore.readyState === 1) wsCore.send("ping");
    if(wsApp.readyState === 1) wsApp.send("ping");
  }, 25000);

  (async () => {
    if("Notification" in window && Notification.permission === "default"){
      try{ await Notification.requestPermission(); }catch(e){}
    }
    await refreshNotificationState();
    await loadChatsList();
    await loadHistory();
    await loadTasks();
    setView("core");
  })();
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
    keys = ["internet", "web", "busca", "buscar", "actual", "actualizado", "última", "ultima", "noticia", "noticias"]
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
          Espera un momento mientras despertamos todos los servicios,
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
        if needs_online(req.question):
            tav = tavily_search(req.question, max_results=5)
            if tav.get("ok"):
                web_ctx = format_tavily_context(tav)
            else:
                web_ctx = f"No se pudo buscar en internet: {tav.get('error','')}"

        prompt = (
            f"{system_style}\n\n"
            + (f"{sensores_ctx}\n\n" if sensores_ctx else "")
            + f"Evidencia web (si aplica):\n{web_ctx}\n\n"
            f"Usuario: {req.question}\n"
            f"Asistente:"
        )

        try:
            raw_answer = ask_ollama(prompt)
            answer = compact_answer(raw_answer, max_chars=max_chars)
        except Exception as e:
            print("❌ /ask error en Ollama:", repr(e))

            if analytics_obj:
                answer = compact_answer(
                    build_answer_from_analytics_text(analytics_obj, detailed=detailed),
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
            "chat_id": chat_id
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
        rem = parse_reminder(transcript)
        if rem:
            minutes, task_text = rem

            task = create_task_internal(
                task_text,
                minutes,
                chat_id=chat_id,
                create_calendar_event=True,
                calendar_title=f"{task_text}"
            )

            answer = compact_answer(
                f"Listo, Daniel. Te lo recuerdo en {minutes} minutos y también lo agendé en tu Google Calendar: {task_text}.",
                MAX_CHARS_SHORT
            )

            save_chat_event(
                "talk_reminder",
                user_text=transcript,
                assistant_text=answer,
                meta={"created_task": task},
                chat_id=chat_id
            )

            await ws_broadcast({"type": "talk", "transcript": transcript, "answer": answer, "chat_id": chat_id})

            return {
                "ok": True,
                "transcript": transcript,
                "answer": answer,
                "created_task": task,
                "chat_id": chat_id
            }

        # ============================================================
        # ✅ 3) FLUJO NORMAL OLLAMA
        # ============================================================
        detailed = wants_detailed(transcript)
        system_style = SYSTEM_STYLE_LONG if detailed else SYSTEM_STYLE_SHORT
        max_chars = MAX_CHARS_LONG if detailed else MAX_CHARS_SHORT

        prompt = f"{system_style}\n\nUsuario: {transcript}\nAsistente:"

        try:
            raw_answer = ask_ollama(prompt)
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
    return await talk(audio=audio, chat_id=chat_id)

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
