from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
import os, uuid, subprocess
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

# (Opcional) Gemini
import google.generativeai as genai

# ✅ TTS gratis local (Windows SAPI)
import pyttsx3

# STT local (Whisper)
from faster_whisper import WhisperModel

# ===============================
# ✅ AGENDA / RECORDATORIOS (APSCHEDULER)
# ===============================
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

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

MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()

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
# 🌐 Gemini (online) (OPCIONAL)
# ====================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel("gemini-1.5-flash")
else:
    gemini_model = None

ESP32_TTS_URL = "http://192.168.100.149/say"

TMP_DIR = "tmp_audio"
os.makedirs(TMP_DIR, exist_ok=True)

TTS_LAST_WAV = os.path.join(TMP_DIR, "tts_last.wav")

# Whisper
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "small").strip()
whisper_model = WhisperModel(WHISPER_MODEL_NAME, device="cpu", compute_type="int8")

# ====================
# 🔥 Firebase RTDB (REST)
# ====================
FIREBASE_REST_BASE = os.getenv(
    "FIREBASE_REST_BASE",
    "https://sensores-6d2ce-default-rtdb.firebaseio.com"
).strip()

# =========================
# ✅ MULTI-CHAT (estilo ChatGPT)
# =========================
TZ = ZoneInfo("America/Guayaquil")

CHATS_DIR = os.getenv("CHATS_DIR", "chats").strip()
os.makedirs(CHATS_DIR, exist_ok=True)

CHATS_INDEX_FILE = os.path.join(CHATS_DIR, "chats_index.json")

CHAT_MAX_LINES = int(os.getenv("CHAT_MAX_LINES", "1500"))


def _now_iso() -> str:
    return datetime.now(TZ).isoformat()


def _safe_load_json(path: str, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default


def _safe_save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass


def _load_chats_index() -> list:
    data = _safe_load_json(CHATS_INDEX_FILE, [])
    return data if isinstance(data, list) else []


def _save_chats_index(items: list):
    _safe_save_json(CHATS_INDEX_FILE, items if isinstance(items, list) else [])


def _sanitize_chat_id(chat_id: str) -> str:
    chat_id = (chat_id or "").strip()
    chat_id = re.sub(r"[^a-zA-Z0-9_\-]", "", chat_id)
    return chat_id or "default"


def _chat_file(chat_id: str) -> str:
    cid = _sanitize_chat_id(chat_id)
    return os.path.join(CHATS_DIR, f"{cid}.jsonl")


def _ensure_chat_exists(chat_id: str, title: Optional[str] = None) -> dict:
    cid = _sanitize_chat_id(chat_id)
    idx = _load_chats_index()

    found = None
    for it in idx:
        if it.get("id") == cid:
            found = it
            break

    now = _now_iso()

    if not found:
        found = {
            "id": cid,
            "title": (title or "Nuevo chat").strip()[:60],
            "created_at": now,
            "updated_at": now,
        }
        idx.append(found)
        _save_chats_index(idx)

        # crea archivo vacío si no existe
        try:
            path = _chat_file(cid)
            if not os.path.exists(path):
                with open(path, "a", encoding="utf-8") as f:
                    f.write("")
        except:
            pass

    return found


def _touch_chat(chat_id: str, title_if_empty: Optional[str] = None):
    cid = _sanitize_chat_id(chat_id)
    idx = _load_chats_index()
    now = _now_iso()
    changed = False

    for it in idx:
        if it.get("id") == cid:
            it["updated_at"] = now
            if title_if_empty and (not it.get("title") or it.get("title") == "Nuevo chat"):
                it["title"] = title_if_empty.strip()[:60]
            changed = True
            break

    if not changed:
        _ensure_chat_exists(cid, title=title_if_empty or "Nuevo chat")
        return

    _save_chats_index(idx)


def list_chats() -> list:
    idx = _load_chats_index()
    idx.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return idx


def create_chat(title: Optional[str] = None) -> dict:
    cid = uuid.uuid4().hex[:10]
    item = _ensure_chat_exists(cid, title=title or "Nuevo chat")
    return item


def rename_chat(chat_id: str, new_title: str) -> dict:
    cid = _sanitize_chat_id(chat_id)
    idx = _load_chats_index()
    now = _now_iso()

    for it in idx:
        if it.get("id") == cid:
            it["title"] = (new_title or "").strip()[:60] or it.get("title", "Chat")
            it["updated_at"] = now
            _save_chats_index(idx)
            return it

    raise HTTPException(status_code=404, detail="chat no encontrado")


def delete_chat(chat_id: str) -> dict:
    cid = _sanitize_chat_id(chat_id)

    # borra archivo
    try:
        path = _chat_file(cid)
        if os.path.exists(path):
            os.remove(path)
    except:
        pass

    # borra del índice
    idx = _load_chats_index()
    idx2 = [x for x in idx if x.get("id") != cid]
    _save_chats_index(idx2)

    return {"ok": True, "deleted": cid}


def save_chat_event(
    kind: str,
    user_text: Optional[str] = None,
    assistant_text: Optional[str] = None,
    meta: Optional[dict] = None,
    chat_id: str = "default",
):
    cid = _sanitize_chat_id(chat_id)
    _ensure_chat_exists(cid)

    evt = {
        "ts": _now_iso(),
        "chat_id": cid,
        "kind": kind,
        "user": user_text,
        "assistant": assistant_text,
        "meta": meta or {}
    }

    try:
        with open(_chat_file(cid), "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
    except:
        pass

    # auto-título: primera pregunta del usuario
    if user_text and user_text.strip():
        auto_title = re.sub(r"\s+", " ", user_text.strip())[:60]
        _touch_chat(cid, title_if_empty=auto_title)
    else:
        _touch_chat(cid)


def load_chat_history(chat_id: str = "default", limit: int = 50) -> list:
    cid = _sanitize_chat_id(chat_id)
    if limit <= 0:
        return []
    path = _chat_file(cid)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        lines = lines[-limit:]
        out = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except:
                continue
        return out
    except:
        return []


def _trim_chat_file(path: str):
    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= CHAT_MAX_LINES:
            return
        keep = lines[-CHAT_MAX_LINES:]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except:
        pass


def _trim_all_chats_if_needed():
    try:
        if not os.path.exists(CHATS_DIR):
            return
        for fn in os.listdir(CHATS_DIR):
            if fn.endswith(".jsonl"):
                _trim_chat_file(os.path.join(CHATS_DIR, fn))
    except:
        pass


# =========================
# ✅ Scheduler (recorte chats)
# =========================
scheduler = BackgroundScheduler(timezone=str(TZ))
scheduler.start()
scheduler.add_job(_trim_all_chats_if_needed, "interval", minutes=5, id="trim_chats", replace_existing=True)

# =========================
# ✅ Endpoints Multi-chat
# =========================
class ChatCreateReq(BaseModel):
    title: Optional[str] = None

class ChatRenameReq(BaseModel):
    title: str

@app.get("/chats")
def api_list_chats():
    return {"ok": True, "chats": list_chats()}

@app.post("/chats")
def api_create_chat(req: ChatCreateReq):
    item = create_chat(req.title if req else None)
    return {"ok": True, "chat": item}

@app.get("/chats/{chat_id}")
def api_get_chat(chat_id: str, limit: int = 50):
    limit = max(1, min(int(limit), 500))
    _ensure_chat_exists(chat_id)
    return {"ok": True, "chat_id": _sanitize_chat_id(chat_id), "history": load_chat_history(chat_id, limit)}

@app.patch("/chats/{chat_id}")
def api_rename_chat(chat_id: str, req: ChatRenameReq):
    item = rename_chat(chat_id, req.title)
    return {"ok": True, "chat": item}

@app.delete("/chats/{chat_id}")
def api_delete_chat(chat_id: str):
    return delete_chat(chat_id)

# =========================
# ✅ Compatibilidad: /chat (viejo)
# =========================
@app.get("/chat")
def get_chat(limit: int = 50, chat_id: str = "default"):
    limit = max(1, min(int(limit), 500))
    _ensure_chat_exists(chat_id)
    return {"ok": True, "chat_id": _sanitize_chat_id(chat_id), "history": load_chat_history(chat_id, limit)}

@app.delete("/chat")
def clear_chat(chat_id: str = "default"):
    cid = _sanitize_chat_id(chat_id)
    try:
        path = _chat_file(cid)
        if os.path.exists(path):
            os.remove(path)
            with open(_chat_file(cid), "a", encoding="utf-8") as f:
                f.write("")
    except:
        pass
    _touch_chat(cid)
    return {"ok": True, "cleared": True, "chat_id": cid}

# =========================
# ✅ RESPUESTAS: modo corto vs modo detallado
# =========================
MAX_CHARS_SHORT = 320
MAX_CHARS_LONG = 2200

SYSTEM_STYLE_SHORT = (
    "Eres un asistente conversacional en español (Ecuador). "
    "Responde SOLO en 1 párrafo, sin listas, sin viñetas, sin títulos. "
    f"Máximo {MAX_CHARS_SHORT} caracteres. "
    "Sé claro y natural."
)

SYSTEM_STYLE_LONG = (
    "Eres un asistente técnico en español (Ecuador). "
    "Da una respuesta DETALLADA y útil. "
    "Puedes usar secciones cortas y listas. "
    f"Máximo {MAX_CHARS_LONG} caracteres. "
    "NO inventes datos: usa SOLO lo que venga en ANALITICA_SENSORES."
)

def wants_detailed(question: str) -> bool:
    q = (question or "").lower()
    triggers = [
        "detall", "detalle", "completo", "completa",
        "profundo", "a fondo", "reporte", "informe",
        "compar", "comparación", "comparacion", "analiza", "análisis", "analisis",
        "últimos", "ultimos", "días", "dias", "resumen por día", "por dia", "por día"
    ]
    return any(t in q for t in triggers)

def compact_answer(text: str, max_chars: int) -> str:
    if not text:
        return text
    out = text.replace("\r", "\n")
    out = " ".join(out.split())
    if len(out) > max_chars:
        out = out[:max_chars].rstrip()
        if not out.endswith((".", "!", "?", ";", ":")):
            out += "…"
    return out

def fetch_firebase_json(path: str = "/") -> dict:
    try:
        clean = (path or "/").strip()
        if not clean.startswith("/"):
            clean = "/" + clean
        url = f"{FIREBASE_REST_BASE}{clean}.json"
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Firebase REST error: {e}")

def needs_online(question: str) -> bool:
    q = (question or "").lower()
    if any(k in q for k in ["firebase", "sensores", "sensor", "medicion", "medición", "registro", "corriente", "voltaje", "potencia", "energia", "energía"]):
        return False
    triggers = [
        "actual", "hoy", "ahora", "última hora", "noticia", "noticias",
        "2024", "2025", "2026", "presidente", "gobierno",
        "precio", "tarifa", "costo", "inflación", "kwh", "ecuador",
        "quién es", "quien es", "qué pasó", "que paso"
    ]
    return any(t in q for t in triggers)

def needs_sensors_context(question: str) -> bool:
    q = (question or "").lower()
    triggers = [
        "sensor", "sensores", "medición", "medicion", "firebase",
        "voltaje", "voltios", "corriente", "amper", "amperios",
        "potencia", "watt", "watts", "consumo", "energía", "energia",
        "kwh", "kilowatt", "kilovatio", "tarifa", "carga", "panel",
        "temperatura", "humedad", "dht", "energia electrica", "electricidad",
        "comparar", "comparación", "comparacion", "analiza", "análisis", "analisis",
        "ultimos", "últimos", "dias", "días", "registro", "último registro", "ultimo registro"
    ]
    return any(t in q for t in triggers)

# =========================
# ✅ ANALÍTICA FIREBASE
# =========================
def get_mediciones_dict(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    meds = data.get("mediciones")
    if isinstance(meds, dict):
        return meds
    return data

def parse_mediciones(mediciones: dict) -> list:
    registros = []
    for _id, value in (mediciones or {}).items():
        if not isinstance(value, dict):
            continue
        ts = value.get("timestamp")
        if not ts:
            continue
        try:
            fecha = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
        except:
            continue

        registros.append({
            "id": _id,
            "fecha": fecha,
            "corriente": float(value.get("corriente", 0) or 0),
            "voltaje": float(value.get("voltaje", 0) or 0),
            "potencia": float(value.get("potencia", 0) or 0),
            "energia_kwh": float(value.get("energia_kwh", 0) or 0),
            "costo_usd": float(value.get("costo_usd", 0) or 0),
            "rele": int(value.get("rele", 0) or 0),
            "raw": value
        })

    registros.sort(key=lambda x: x["fecha"])
    return registros

def pick_latest_medicion(mediciones: dict):
    latest = None
    latest_ts = ""
    for _id, item in (mediciones or {}).items():
        if not isinstance(item, dict):
            continue
        ts = str(item.get("timestamp", "") or "")
        if ts > latest_ts:
            latest_ts = ts
            latest = {"id": _id, **item}
    return latest

def ultimo_registro_completo(registros: list):
    if not registros:
        return {"ok": False, "error": "No hay registros"}
    u = registros[-1]
    return {
        "ok": True,
        "fecha_exacta": u["fecha"].strftime("%Y-%m-%d %H:%M:%S"),
        "id": u["id"],
        "datos": u["raw"]
    }

def analizar_ultimos_dias(registros: list, dias: int = 10):
    if not registros:
        return {"ok": False, "error": "No hay registros"}

    limite = datetime.now() - timedelta(days=int(dias))
    filtrados = [r for r in registros if r["fecha"] >= limite]
    if not filtrados:
        return {"ok": False, "error": f"No hay datos en los últimos {dias} días"}

    resumen = defaultdict(lambda: defaultdict(list))
    for r in filtrados:
        dia = r["fecha"].date()
        for k in ["corriente", "voltaje", "potencia", "energia_kwh", "costo_usd"]:
            resumen[dia][k].append(r.get(k, 0))

    analisis = {}
    for dia, sensores in resumen.items():
        analisis[str(dia)] = {}
        for sensor, valores in sensores.items():
            analisis[str(dia)][sensor] = {
                "promedio": round(mean(valores), 4),
                "max": max(valores),
                "min": min(valores),
                "muestras": len(valores)
            }

    top_por_variable = {}
    for var in ["corriente", "voltaje", "potencia", "energia_kwh", "costo_usd"]:
        best_day = None
        best_val = -float("inf")
        for d, sens in analisis.items():
            v = sens.get(var, {}).get("max")
            if v is None:
                continue
            if v > best_val:
                best_val = v
                best_day = d
        if best_day is not None:
            top_por_variable[var] = {"dia": best_day, "max": best_val}

    return {"ok": True, "dias": dias, "resumen": analisis, "top_por_variable": top_por_variable}

def comparar_dias(registros: list, fecha1_str: str, fecha2_str: str):
    if not registros:
        return {"ok": False, "error": "No hay registros"}

    try:
        f1 = datetime.strptime(fecha1_str, "%Y-%m-%d").date()
        f2 = datetime.strptime(fecha2_str, "%Y-%m-%d").date()
    except:
        return {"ok": False, "error": "Formato inválido. Usa YYYY-MM-DD (ej: 2026-02-10)"}

    grupos = {f1: [], f2: []}
    for r in registros:
        d = r["fecha"].date()
        if d in grupos:
            grupos[d].append(r)

    def _stats(lista):
        if not lista:
            return None
        out = {}
        for var in ["corriente", "voltaje", "potencia", "energia_kwh", "costo_usd"]:
            vals = [x.get(var, 0) for x in lista]
            out[var] = {
                "promedio": round(mean(vals), 4),
                "max": max(vals),
                "min": min(vals),
                "muestras": len(vals)
            }
        return out

    s1 = _stats(grupos[f1])
    s2 = _stats(grupos[f2])

    return {
        "ok": True,
        "fecha1": fecha1_str,
        "fecha2": fecha2_str,
        "stats": {
            fecha1_str: (s1 or "Sin datos"),
            fecha2_str: (s2 or "Sin datos")
        }
    }

def extract_last_n_days(question: str, default_n: int = 10) -> int:
    q = (question or "").lower()
    m = re.search(r"(?:ultimos|últimos)\s+(\d{1,3})\s+(?:dias|días)", q)
    if m:
        try:
            n = int(m.group(1))
            return max(1, min(n, 365))
        except:
            pass
    return default_n

def extract_compare_dates(question: str):
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", (question or "").lower())
    if len(dates) >= 2:
        return dates[0], dates[1]
    return None

def compute_analytics_obj(question: str):
    data = fetch_firebase_json("/")
    mediciones = get_mediciones_dict(data)
    registros = parse_mediciones(mediciones)

    q = (question or "").lower()

    if any(k in q for k in ["último registro", "ultimo registro", "registro completo", "última medición", "ultima medicion"]):
        obj = ultimo_registro_completo(registros)
        return obj, "ANALITICA_SENSORES: " + json.dumps(obj, ensure_ascii=False)

    pair = extract_compare_dates(question)
    if pair:
        obj = comparar_dias(registros, pair[0], pair[1])
        return obj, "ANALITICA_SENSORES: " + json.dumps(obj, ensure_ascii=False)

    n = extract_last_n_days(question, default_n=10)
    obj = analizar_ultimos_dias(registros, dias=n)
    return obj, "ANALITICA_SENSORES: " + json.dumps(obj, ensure_ascii=False)

# =========================
# ✅ VOZ: resumen corto
# =========================
def summarize_analytics_for_voice(analytics: dict) -> str:
    if not isinstance(analytics, dict) or not analytics.get("ok"):
        return "Listo, Daniel. No encontré datos suficientes para ese análisis."

    if "fecha_exacta" in analytics and "datos" in analytics:
        d = analytics.get("datos") or {}
        return (
            f"Último registro: {analytics.get('fecha_exacta')}. "
            f"Voltaje {d.get('voltaje')}, corriente {d.get('corriente')}, potencia {d.get('potencia')}. "
            f"Energía {d.get('energia_kwh')} kilovatios hora y costo {d.get('costo_usd')} dólares."
        )

    if "top_por_variable" in analytics:
        top = analytics.get("top_por_variable") or {}
        winners = []
        for k in ["potencia", "corriente", "voltaje", "energia_kwh", "costo_usd"]:
            if k in top and top[k].get("dia"):
                winners.append((k, top[k].get("dia")))
        winners = winners[:2]
        if winners:
            parts = [f"{k} tuvo su pico el {dia}" for k, dia in winners]
            return "Resumen: " + ". ".join(parts) + ". El detalle completo está en pantalla."
        return "Listo, Daniel. Ya tengo el análisis. Mira el detalle completo en pantalla."

    if "stats" in analytics:
        return "Listo, Daniel. Ya comparé esos dos días. Te dejé promedios, máximos y mínimos completos en pantalla."

    return "Listo, Daniel. Te dejé el análisis completo en pantalla."

def build_answer_from_analytics_text(analytics: dict, detailed: bool) -> str:
    if not isinstance(analytics, dict) or not analytics.get("ok"):
        return "Daniel, no pude generar el análisis porque no encontré registros válidos con timestamp."

    if "fecha_exacta" in analytics and "datos" in analytics:
        d = analytics.get("datos") or {}
        return (
            f"Último registro ({analytics.get('fecha_exacta')}): "
            f"voltaje={d.get('voltaje')}, corriente={d.get('corriente')}, potencia={d.get('potencia')}, "
            f"energia_kwh={d.get('energia_kwh')}, costo_usd={d.get('costo_usd')}, rele={d.get('rele')}. "
            "Te dejé todo el JSON en analytics."
        )

    if "stats" in analytics:
        f1 = analytics.get("fecha1")
        f2 = analytics.get("fecha2")
        return (
            f"Comparación entre {f1} y {f2}: revisa analytics.stats para ver promedios, máximos, mínimos y muestras "
            "por variable. Si alguno sale 'Sin datos' es porque ese día no tuvo registros en Firebase."
        )

    if "top_por_variable" in analytics:
        top = analytics.get("top_por_variable") or {}
        if not detailed:
            pieces = []
            for k in ["potencia", "corriente", "voltaje"]:
                if k in top:
                    pieces.append(f"{k} pico {top[k].get('dia')} ({top[k].get('max')})")
            if pieces:
                return "Top (resumen): " + "; ".join(pieces) + ". Detalle completo en analytics."
        return "Listo, Daniel. Te generé el resumen por día y los máximos por variable; revisa analytics.resumen y analytics.top_por_variable."

    return "Listo, Daniel. Te dejé el análisis en analytics."

# =========================
# ✅ RECORDATORIOS: acepta "2" y "dos"
# =========================
_WORD2NUM = {
    "un": 1, "uno": 1, "una": 1,
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "veinte": 20,
}

def parse_reminder(text: str):
    if not text:
        return None
    t = text.lower().strip()
    if not any(k in t for k in ["recuérdame", "recuerdame", "recordarme", "recordatorio"]):
        return None

    m = re.search(r"\ben\s+(\d{1,3})\s*(min|mins|minuto|minutos)\b", t)
    minutes = int(m.group(1)) if m else None

    if minutes is None:
        m2 = re.search(r"\ben\s+([a-záéíóúñ]+)\s*(min|mins|minuto|minutos)\b", t)
        if m2:
            minutes = _WORD2NUM.get(m2.group(1).strip())

    if minutes is None or minutes <= 0:
        return None

    task = re.sub(r".*\ben\s+(\d{1,3}|[a-záéíóúñ]+)\s*(min|mins|minuto|minutos)\b", "", t).strip()
    task = task.lstrip(" ,:;-").strip() or "tienes un recordatorio pendiente"
    return minutes, task

def tavily_search(query: str, max_results: int = 5) -> dict:
    if not TAVILY_API_KEY:
        return {"ok": False, "error": "TAVILY_API_KEY no está configurada."}
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False
    }
    try:
        r = requests.post(TAVILY_SEARCH_URL, json=payload, timeout=25)
        r.raise_for_status()
        data = r.json()
        data["ok"] = True
        return data
    except Exception as e:
        return {"ok": False, "error": f"Tavily error: {e}"}

def format_tavily_context(tav: dict) -> str:
    results = tav.get("results") if isinstance(tav, dict) else None
    if not results:
        return "No se encontraron resultados relevantes en la búsqueda web."
    lines = []
    for i, it in enumerate(results[:6], start=1):
        title = (it.get("title") or "").strip()
        url = (it.get("url") or "").strip()
        content = " ".join((it.get("content") or "").split())[:350]
        lines.append(f"[{i}] {title}\n{content}\nFuente: {url}")
    return "\n\n".join(lines)

def ask_ollama(prompt: str) -> str:
    try:
        payload = {"model": MODEL, "prompt": prompt, "stream": False}
        r = requests.post(OLLAMA_URL, json=payload, timeout=180)
        r.raise_for_status()
        return (r.json().get("response", "") or "").strip()
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No puedo conectar con Ollama en {OLLAMA_URL}. "
                "Solución: asegúrate de tener Ollama corriendo (ollama serve) y el modelo descargado, "
                "o si Ollama está en otra PC pon OLLAMA_HOST=IP_DE_ESA_PC en tu .env."
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Error consultando Ollama: {e}")

class AskReq(BaseModel):
    question: str
    chat_id: Optional[str] = "default"

class SpeakReq(BaseModel):
    text: str

# ✅ Inicializar TTS engine
engine = pyttsx3.init()
engine.setProperty('rate', 175)
engine.setProperty('volume', 1.0)
try:
    voices = engine.getProperty('voices')
    for v in voices:
        name = (getattr(v, "name", "") or "").lower()
        vid  = (getattr(v, "id", "") or "").lower()
        if "spanish" in name or "es_" in vid or "es-" in vid or "spanish" in vid:
            engine.setProperty('voice', v.id)
            break
except:
    pass

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

# ===============================
# ✅ NUEVO: Página APP futurista (Core + Recordatorios + MultiChats EN REGISTRO)
# ===============================
@app.get("/app", response_class=HTMLResponse)
def app_page():
    return r"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Spectra AI - App</title>
  <style>
    :root{
      --bg:#070b14;
      --card:rgba(255,255,255,.04);
      --card2:rgba(0,0,0,.18);
      --line:rgba(255,255,255,.10);
      --muted:rgba(232,238,252,.70);
      --text:#e8eefc;
      --accent:rgba(46,196,196,.22);
      --accentLine:rgba(46,196,196,.55);
    }
    body { margin:0; font-family: Arial, sans-serif; background:var(--bg); color:var(--text); }
    .topbar{
      padding:14px 16px;
      border-bottom:1px solid rgba(255,255,255,.08);
      display:flex; align-items:center; justify-content:space-between; gap:12px;
      background:rgba(255,255,255,.02);
    }
    .leftTop{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    .pill{
      padding:8px 12px; border-radius:999px; cursor:pointer;
      border:1px solid rgba(255,255,255,.12);
      background:rgba(255,255,255,.06);
      user-select:none;
    }
    .pill.active{ background:var(--accent); border-color:var(--accentLine); }
    .btn{
      padding:8px 12px; border-radius:12px; cursor:pointer;
      border:1px solid rgba(255,255,255,.14);
      background:rgba(0,0,0,.25);
      color:var(--text);
    }
    .btn.primary{ background:rgba(46,196,196,.18); border-color:rgba(46,196,196,.45); }
    .badge{
      display:inline-block; margin-left:8px; padding:2px 8px; border-radius:999px; font-size:12px;
      background:rgba(255,138,33,.22); border:1px solid rgba(255,138,33,.55);
    }
    .wrap{ padding:16px; max-width:1280px; margin:0 auto; }
    .grid2{
      display:grid;
      grid-template-columns: 1.25fr .95fr;
      gap:14px;
    }
    .card{ border:1px solid var(--line); background:var(--card); border-radius:16px; padding:14px; }
    .title{ font-weight:800; letter-spacing:.12em; font-size:14px; opacity:.85; }
    .muted{ color:var(--muted); font-size:13px; }
    .row{ display:flex; gap:10px; align-items:center; }
    input, button{ font-family:inherit; }
    input{
      padding:10px 12px; border-radius:12px; border:1px solid rgba(255,255,255,.14);
      background:rgba(0,0,0,.25); color:var(--text);
      outline:none;
    }
    button{ cursor:pointer; }
    .list{ display:flex; flex-direction:column; gap:10px; }
    .item{ padding:10px 12px; border-radius:14px; border:1px solid rgba(255,255,255,.10); background:rgba(255,255,255,.03); }
    .hide{ display:none; }

    /* === Chat selector like ChatGPT (Tus chats ▼ + list) === */
    .chatSelector{
      margin-top:10px;
      border:1px solid rgba(255,255,255,.10);
      border-radius:14px;
      background:rgba(0,0,0,.18);
      overflow:hidden;
    }
    .chatSelHead{
      padding:10px 12px;
      display:flex; justify-content:space-between; align-items:center;
      cursor:pointer; user-select:none;
      background:rgba(255,255,255,.03);
      border-bottom:1px solid rgba(255,255,255,.08);
    }
    .chatSelHead b{ font-size:13px; }
    .chatSelList{
      max-height:240px;
      overflow:auto;
      padding:8px;
      display:flex; flex-direction:column; gap:8px;
    }
    .chatRow{
      padding:10px 10px;
      border-radius:12px;
      border:1px solid rgba(255,255,255,.08);
      background:rgba(255,255,255,.02);
      cursor:pointer;
    }
    .chatRow.active{
      background:rgba(46,196,196,.16);
      border-color:rgba(46,196,196,.35);
    }
    .chatTitle{ font-weight:700; font-size:14px; }
    .chatMeta{ color:rgba(232,238,252,.60); font-size:12px; margin-top:4px; }
    .toast{
      position:fixed; right:14px; bottom:14px; padding:12px 14px; border-radius:14px;
      background:rgba(0,0,0,.75); border:1px solid rgba(255,255,255,.12);
      display:none; max-width:420px;
    }
    .micro{
      width:110px; height:110px; border-radius:999px;
      border:1px solid rgba(255,255,255,.12);
      background:rgba(0,0,0,.25);
      display:flex; align-items:center; justify-content:center;
      margin:14px auto 8px;
      position:relative;
    }
    .micro::after{
      content:"";
      position:absolute; inset:-10px;
      border-radius:999px;
      border:2px solid rgba(46,196,196,.25);
      filter:blur(.3px);
    }
    .microIcon{ font-size:28px; opacity:.9; }
    .smallBtnRow{ display:flex; gap:10px; flex-wrap:wrap; margin-top:10px; }
  </style>
</head>
<body>

  <div class="topbar">
    <div class="leftTop">
      <div class="pill active" id="tabCore">CORE</div>

      <div class="pill" id="tabRem">
        RECORDATORIOS <span class="badge" id="remBadge" style="display:none;">0</span>
      </div>

      <!-- ✅ Nuevo chat al lado de Recordatorios -->
      <button class="btn primary" id="btnNewChat">+ Nuevo chat</button>

      <span class="muted" id="status">WS: desconectado</span>
    </div>

    <div class="muted" id="activeChatLabel">Chat: -</div>
  </div>

  <div class="wrap">
    <div class="grid2">

      <!-- LEFT: REGISTRO + CHATS -->
      <div class="card" id="viewCore">

        <div class="row" style="justify-content:space-between; align-items:flex-start;">
          <div>
            <div class="title">REGISTRO DE CONVERSACIÓN</div>
            <div class="muted">Escoge un chat y se mantiene la conversación (tipo ChatGPT).</div>
          </div>
          <div class="row">
            <button class="btn" id="btnRefreshChats">↻ Chats</button>
            <button class="btn" id="btnLoad">Cargar historial</button>
          </div>
        </div>

        <!-- ✅ Tus chats dropdown/list -->
        <div class="chatSelector">
          <div class="chatSelHead" id="toggleChats">
            <b>Tus chats</b>
            <span class="muted">▼</span>
          </div>
          <div class="chatSelList" id="chatsList"></div>
        </div>

        <div style="height:12px;"></div>

        <div class="row">
          <input id="q" style="flex:1;" placeholder="Escribe una pregunta..." />
          <button class="btn primary" id="btnAsk">Preguntar</button>
        </div>

        <div style="height:12px;"></div>

        <div class="card" style="background:var(--card2);">
          <div class="muted">Mensajes recientes</div>
          <div style="height:6px;"></div>
          <div id="coreList" class="list"></div>
        </div>
      </div>

      <!-- RIGHT: SPEAKER / VOZ UI (placeholder visual) -->
      <div class="card">
        <div class="row" style="justify-content:space-between;">
          <div>
            <div style="font-weight:800;">Mantén presionada la tecla <b>ESPACIO</b> para hablar con Spectra AI</div>
            <div class="muted">Suelta la tecla para enviar el mensaje.</div>
          </div>
          <a class="btn" href="/speaker" target="_blank" style="text-decoration:none; display:inline-flex; align-items:center; gap:8px;">
            🔊 SPEAKER PC
          </a>
        </div>

        <div class="micro">
          <div class="microIcon">🎙️</div>
        </div>
        <div class="muted" style="text-align:center;">Espera tranquila... Listo para hablar con Spectra</div>

        <div style="height:12px;"></div>

        <div class="card" style="background:var(--card2);">
          <div class="muted">Aquí verás la transcripción y la respuesta…</div>
          <div style="height:10px;"></div>
          <div id="liveBox" class="muted"></div>
        </div>

        <div class="smallBtnRow">
          <button class="btn" id="btnUltima">Última medición</button>
          <button class="btn" id="btnCrudo">Sensores (crudo)</button>
          <button class="btn" id="btnOpenSpeaker">Abrir Speaker PC</button>
        </div>

        <div style="height:10px;"></div>

        <div class="card hide" id="viewRem" style="background:var(--card2);">
          <div style="font-weight:800;">Recordatorios</div>
          <div class="muted">Llegan por /ws-app (type:"reminder").</div>
          <div style="height:10px;"></div>
          <div id="remList" class="list"></div>
        </div>
      </div>

    </div>
  </div>

  <div class="toast" id="toast"></div>

<script>
  // ===== Tabs =====
  const tabCore = document.getElementById("tabCore");
  const tabRem  = document.getElementById("tabRem");
  const viewCore = document.getElementById("viewCore");
  const viewRem  = document.getElementById("viewRem");
  const badge = document.getElementById("remBadge");
  let unreadReminders = 0;

  function setTab(name) {
    if (name === "core") {
      tabCore.classList.add("active");
      tabRem.classList.remove("active");
      viewCore.classList.remove("hide");
      viewRem.classList.add("hide");
    } else {
      tabRem.classList.add("active");
      tabCore.classList.remove("active");
      viewRem.classList.remove("hide");
      // al abrir recordatorios, se limpian "no leídos"
      unreadReminders = 0;
      badge.style.display = "none";
      badge.textContent = "0";
    }
  }
  tabCore.onclick = () => setTab("core");
  tabRem.onclick  = () => setTab("rem");

  // ===== UI =====
  const coreList = document.getElementById("coreList");
  const remList = document.getElementById("remList");
  const status = document.getElementById("status");
  const toast = document.getElementById("toast");
  const liveBox = document.getElementById("liveBox");

  function escapeHtml(s) {
    return (s || "")
      .replaceAll("&","&amp;")
      .replaceAll("<","&lt;")
      .replaceAll(">","&gt;");
  }
  function showToast(msg) {
    toast.style.display = "block";
    toast.innerHTML = escapeHtml(msg);
    setTimeout(() => { toast.style.display = "none"; }, 3000);
  }
  function renderChat(user, assistant, ts) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="muted">${escapeHtml(ts || "")}</div>
      <div style="margin-top:6px;"><b>Tú:</b> ${escapeHtml(user || "")}</div>
      <div style="margin-top:6px;"><b>Spectra:</b> ${escapeHtml(assistant || "")}</div>
    `;
    coreList.prepend(div);
  }
  function addReminderToList(text, run_at) {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="muted">${escapeHtml(run_at || "")}</div>
      <div style="margin-top:6px;">⏰ ${escapeHtml(text || "")}</div>
    `;
    remList.prepend(div);
  }

  // ===== Multi-chat state =====
  const chatsListEl = document.getElementById("chatsList");
  const activeChatLabel = document.getElementById("activeChatLabel");
  const toggleChats = document.getElementById("toggleChats");
  let chatsOpen = true;

  toggleChats.onclick = () => {
    chatsOpen = !chatsOpen;
    chatsListEl.style.display = chatsOpen ? "flex" : "none";
  };

  let currentChatId = localStorage.getItem("spectra_chat_id") || "default";

  function setCurrentChat(id, title) {
    currentChatId = id || "default";
    localStorage.setItem("spectra_chat_id", currentChatId);
    activeChatLabel.textContent = `Chat: ${title ? title : currentChatId}`;

    [...document.querySelectorAll(".chatRow")].forEach(el => el.classList.remove("active"));
    const item = document.querySelector(`[data-chat-id="${currentChatId}"]`);
    if (item) item.classList.add("active");
  }

  async function loadChatsList() {
    try {
      const r = await fetch("/chats");
      const j = await r.json();
      const chats = j.chats || [];
      chatsListEl.innerHTML = "";

      if (!chats.find(c => c.id === "default")) {
        chats.unshift({id:"default", title:"Default", updated_at:""});
      }

      chats.forEach(c => {
        const div = document.createElement("div");
        div.className = "chatRow";
        div.setAttribute("data-chat-id", c.id);
        div.innerHTML = `
          <div class="chatTitle">${escapeHtml(c.title || c.id)}</div>
          <div class="chatMeta">${escapeHtml(c.updated_at || "")}</div>
        `;
        div.onclick = async () => {
          setCurrentChat(c.id, c.title || c.id);
          await loadHistory();
        };
        chatsListEl.appendChild(div);
      });

      const current = chats.find(x => x.id === currentChatId) || chats[0];
      if (current) setCurrentChat(current.id, current.title || current.id);

    } catch (e) {
      showToast("No pude cargar chats ❌");
    }
  }

  document.getElementById("btnRefreshChats").onclick = loadChatsList;

  document.getElementById("btnNewChat").onclick = async () => {
    try {
      const r = await fetch("/chats", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({ title: "Nuevo chat" })
      });
      const j = await r.json();
      const chat = j.chat;

      await loadChatsList();
      setCurrentChat(chat.id, chat.title || chat.id);

      coreList.innerHTML = "";
      remList.innerHTML = "";
      showToast("Nuevo chat creado ✅");
      setTab("core");
    } catch (e) {
      showToast("No pude crear chat ❌");
    }
  };

  // ===== Load history from backend =====
  async function loadHistory() {
    try {
      const r = await fetch(`/chats/${encodeURIComponent(currentChatId)}?limit=120`);
      const j = await r.json();
      const hist = j.history || [];

      coreList.innerHTML = "";
      remList.innerHTML = "";

      hist.forEach(item => {
        const kind = item.kind || "";
        if (kind === "ask" || kind === "talk" || kind === "talk_reminder") {
          if (item.user || item.assistant) {
            renderChat(item.user || "", item.assistant || "", item.ts || "");
          }
        }
        if ((kind || "").includes("reminder")) {
          const runAt = (item.meta && item.meta.run_at) ? item.meta.run_at : (item.ts || "");
          addReminderToList(item.assistant || "", runAt);
        }
      });

      showToast("Historial cargado ✅");
    } catch (e) {
      showToast("No pude cargar historial ❌");
    }
  }
  document.getElementById("btnLoad").onclick = loadHistory;

  // ===== Call /ask (con chat_id) =====
  document.getElementById("btnAsk").onclick = async () => {
    const q = document.getElementById("q").value.trim();
    if (!q) return;
    document.getElementById("q").value = "";
    try {
      const r = await fetch("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, chat_id: currentChatId })
      });
      const j = await r.json();
      renderChat(q, j.answer || "", new Date().toISOString());
      loadChatsList(); // refresca updated_at
    } catch (e) {
      renderChat(q, "Error consultando /ask", new Date().toISOString());
    }
  };

  // ===== Quick buttons right side =====
  document.getElementById("btnOpenSpeaker").onclick = () => window.open("/speaker", "_blank");
  document.getElementById("btnUltima").onclick = async () => {
    try {
      const r = await fetch("/firebase/ultima");
      const j = await r.json();
      showToast("Última medición cargada ✅");
      liveBox.innerHTML = escapeHtml(JSON.stringify(j.last || {}, null, 2));
    } catch(e){ showToast("No pude cargar última medición ❌"); }
  };
  document.getElementById("btnCrudo").onclick = async () => {
    try {
      const r = await fetch("/firebase/sensores");
      const j = await r.json();
      showToast("Sensores (crudo) cargados ✅");
      liveBox.innerHTML = escapeHtml(JSON.stringify(j.data || {}, null, 2)).slice(0, 2500) + "...";
    } catch(e){ showToast("No pude cargar sensores ❌"); }
  };

  // ===== WebSockets =====
  const wsProto = location.protocol === "https:" ? "wss" : "ws";

  // Core WS (talk)
  const wsCore = new WebSocket(`${wsProto}://${location.host}/ws`);
  wsCore.onopen = () => status.textContent = "WS: conectado ✅";
  wsCore.onclose = () => status.textContent = "WS: desconectado ❌";
  wsCore.onerror = () => status.textContent = "WS: error ❌";

  wsCore.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (data.type === "talk") {
        const msgChat = data.chat_id || "default";
        // ✅ solo mostrar si coincide con el chat activo
        if (msgChat !== currentChatId) return;

        renderChat(data.transcript || "", data.answer || "", new Date().toISOString());
        liveBox.innerHTML = escapeHtml((data.transcript || "") + "\n\n" + (data.answer || ""));
      }
    } catch (e) {}
  };

  // App WS (reminders)
  const wsApp = new WebSocket(`${wsProto}://${location.host}/ws-app`);
  wsApp.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data);

      if (data.type === "reminder") {
        const msgChat = data.chat_id || "default";

        // ✅ solo contar/mostrar si coincide con el chat activo
        if (msgChat !== currentChatId) return;

        addReminderToList(data.text || "", data.run_at || "");
        showToast("⏰ " + (data.text || "Recordatorio"));

        unreadReminders += 1;
        badge.style.display = "inline-block";
        badge.textContent = String(unreadReminders);
        return;
      }
    } catch (e) {}
  };

  setInterval(() => {
    if (wsCore.readyState === 1) wsCore.send("ping");
    if (wsApp.readyState === 1) wsApp.send("ping");
  }, 25000);

  // init
  (async () => {
    await loadChatsList();
    await loadHistory();
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

# ===============================
# ✅ /ask (texto)
# ===============================
@app.post("/ask")
def ask(req: AskReq):
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
    except HTTPException as e:
        if analytics_obj:
            answer = compact_answer(build_answer_from_analytics_text(analytics_obj, detailed=detailed), max_chars=max_chars)
        else:
            raise e

    save_chat_event(
        "ask",
        user_text=req.question,
        assistant_text=answer,
        meta={"used_web": bool(web_ctx), "used_sensors": bool(sensores_ctx), "detailed": detailed},
        chat_id=chat_id
    )

    return {
        "answer": answer,
        "used_web": bool(web_ctx),
        "used_sensors": bool(sensores_ctx),
        "analytics": analytics_obj,
        "detailed": detailed,
        "chat_id": chat_id
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

        cmd = ["ffmpeg", "-y", "-i", in_path, "-ac", "1", "-ar", "16000", wav_path]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {p.stderr[:300]}")

        segments, info = whisper_model.transcribe(wav_path, language="es")
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

