from flask import Flask, render_template, request, jsonify, redirect, session
import requests

# ✅ Recordatorios (legacy)
import os, json, uuid
from datetime import datetime, timedelta
import re
from functools import wraps
from urllib.parse import urlparse

# ✅ IMPORTANTE: apuntar templates a la carpeta actual (porque tus .html están en la raíz)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Si algún día sí creas carpeta templates/, Flask la usa; si no, usa la raíz (BASE_DIR)
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
if not os.path.isdir(TEMPLATES_DIR):
    TEMPLATES_DIR = BASE_DIR

app = Flask(__name__, template_folder=TEMPLATES_DIR)

# ✅ necesario para sesiones (login)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev_secret_change_me")

# ✅ password admin (mejor en variable de entorno)
ADMIN_PASSWORD = os.environ.get("SPECTRA_ADMIN_PASSWORD", "admin123")

# =========================
# 🔧 Helpers URL (next safe)
# =========================
def _full_path_with_query():
    # Ej: "/registro-estudiante?equipo=Osciloscopio"
    # request.full_path a veces termina con "?"
    p = request.full_path or request.path or "/"
    return p[:-1] if p.endswith("?") else p

def _safe_next_url(next_url: str, default="/"):
    """
    Evita open redirect:
    - Solo permite paths internos tipo "/algo"
    - Bloquea "http://..." o "//..." etc
    """
    if not next_url:
        return default
    next_url = next_url.strip()
    if not next_url.startswith("/"):
        return default
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return default
    return next_url

# =========================
# ✅ Helpers de auth/roles
# =========================
def require_role(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = session.get("role")
            if role not in roles:
                # ✅ guardar path completo con query (para ?equipo=... y filtros)
                next_url = _full_path_with_query()
                return redirect(f"/login?next={next_url}")
            return fn(*args, **kwargs)
        return wrapper
    return deco


@app.route("/routes")
def routes():
    return jsonify(sorted([str(r) for r in app.url_map.iter_rules()])), 200


# 🔧 CONFIG
FASTAPI_BASE = os.environ.get("FASTAPI_BASE", "https://spectra-ai-axcs.onrender.com")  # si FastAPI está en otra PC/IP, cambia esto

FASTAPI_TALK = f"{FASTAPI_BASE}/talk"
FASTAPI_ASK = f"{FASTAPI_BASE}/ask"
FASTAPI_FB_ULTIMA = f"{FASTAPI_BASE}/firebase/ultima"
FASTAPI_FB_SENSORES = f"{FASTAPI_BASE}/firebase/sensores"
FASTAPI_SPEAKER = f"{FASTAPI_BASE}/speaker"
FASTAPI_ROOT = f"{FASTAPI_BASE}/"  # para health check

# ✅ Multi-chat / memoria en FastAPI
FASTAPI_CHAT = f"{FASTAPI_BASE}/chat"           # compat
FASTAPI_CHATS = f"{FASTAPI_BASE}/chats"         # lista/crear
FASTAPI_TASKS = f"{FASTAPI_BASE}/tasks"         # recordatorios reales (FastAPI)

# =========================
# ✅ ✅ CONFIG n8n (LAB)
# =========================
N8N_BASE = os.environ.get("N8N_BASE", "http://127.0.0.1:5678")
N8N_PRESTAR  = f"{N8N_BASE}/webhook/lab/prestamo"
N8N_DEVOLVER = f"{N8N_BASE}/webhook/lab/devolver"
N8N_LISTAR   = f"{N8N_BASE}/webhook/lab/listar"


# =========================
# ✅ (LEGACY) Recordatorios Flask (DB que lee tu pestaña /reminders)
# =========================
REM_DB = "reminders_db.json"

def _load_reminders():
    if not os.path.exists(REM_DB):
        return []
    try:
        with open(REM_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else (data.get("items", []) if isinstance(data, dict) else [])
    except Exception:
        return []

def _save_reminders(items):
    with open(REM_DB, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def _add_reminder(text, due=None, priority="MED"):
    text = (text or "").strip()
    if not text:
        return None

    item = {
        "id": uuid.uuid4().hex[:10],
        "text": text,
        "due": due,
        "priority": (priority or "MED").upper(),
        "done": False,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    items = _load_reminders()
    items.append(item)
    _save_reminders(items)
    return item

def _normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("recuérdame", "recuerdame")
    return s

def _parse_due_from_text(text: str):
    """
    Soporta:
      - "en 2 minutos"
      - "en 10 min"
      - "a las 18:30"
    Devuelve string "YYYY-MM-DD HH:MM" o None.
    """
    t = _normalize(text)

    m = re.search(r"\ben\s+(\d+)\s*(minutos|minuto|min)\b", t)
    if m:
        mins = int(m.group(1))
        due_dt = datetime.now() + timedelta(minutes=mins)
        return due_dt.strftime("%Y-%m-%d %H:%M")

    m = re.search(r"\ba\s+las\s+(\d{1,2})\s*[:.]\s*(\d{2})\b", t)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        now = datetime.now()
        due_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if due_dt <= now:
            due_dt = due_dt + timedelta(days=1)
        return due_dt.strftime("%Y-%m-%d %H:%M")

    return None

def _extract_reminder_text(transcript: str) -> str:
    t = (transcript or "").strip()
    low = _normalize(t)

    idx = low.find("recuerdame")
    if idx == -1:
        idx = low.find("recuerda")
        if idx == -1:
            return ""

    raw = t[idx:]
    raw = re.sub(r"(?i)recu[eé]rdame\s*", "", raw, count=1)
    raw = re.sub(r"(?i)recuerda\s*", "", raw, count=1)

    raw = re.sub(r"(?i)\ben\s+\d+\s*(minutos|minuto|min)\b", "", raw)
    raw = re.sub(r"(?i)\ba\s+las\s+\d{1,2}\s*[:.]\s*\d{2}\b", "", raw)
    raw = raw.strip(" .,-")

    return raw.strip()

def _looks_like_reminder(text: str) -> bool:
    t = _normalize(text)
    return ("recuerdame" in t) or ("pon un recordatorio" in t) or ("ponme un recordatorio" in t) or ("alertame" in t) or ("alarma" in t)


# =========================
# ✅ Notificaciones (estado simple para el botón 🔔)
# =========================
NOTIF_DB = "notifications_state.json"

def _load_notif_state():
    if not os.path.exists(NOTIF_DB):
        return {"enabled": False, "updated_at": None}
    try:
        with open(NOTIF_DB, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {
                    "enabled": bool(data.get("enabled", False)),
                    "updated_at": data.get("updated_at"),
                }
    except Exception:
        pass
    return {"enabled": False, "updated_at": None}

def _save_notif_state(enabled: bool):
    payload = {"enabled": bool(enabled), "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    with open(NOTIF_DB, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload

@app.route("/notifications/status", methods=["GET"])
def notifications_status():
    return jsonify({"ok": True, "state": _load_notif_state()}), 200

@app.route("/notifications/enable", methods=["POST"])
def notifications_enable():
    state = _save_notif_state(True)
    return jsonify({"ok": True, "state": state}), 200

@app.route("/notifications/disable", methods=["POST"])
def notifications_disable():
    state = _save_notif_state(False)
    return jsonify({"ok": True, "state": state}), 200

@app.route("/notifications/test", methods=["POST"])
def notifications_test():
    state = _load_notif_state()
    return jsonify({"ok": True, "message": "Notificación de prueba (backend) ✅", "state": state}), 200


# =========================
# ✅ LOGIN / ROLES
# =========================
@app.route("/login")
def login_page():
    # ✅ respeta next (si viene)
    next_url = _safe_next_url(request.args.get("next"), default="/")

    # si ya estás logeado, re-dirige según rol
    role = session.get("role")
    if role == "admin":
        return redirect(next_url if next_url != "/registro-estudiante" else "/")
    if role == "student":
        # permite mandar a inventario-estudiante o registro-estudiante
        return redirect(
            next_url
            if next_url.startswith("/registro-estudiante") or next_url.startswith("/inventario-estudiante")
            else "/registro-estudiante"
        )

    return render_template("login.html", next=next_url)

# ✅✅ DEBUG: ver error real del login en texto
@app.get("/debug/login")
def debug_login():
    try:
        next_url = _safe_next_url(request.args.get("next"), default="/")
        return render_template("login.html", next=next_url)
    except Exception:
        return Response(traceback.format_exc(), mimetype="text/plain")

@app.route("/auth/admin", methods=["POST"])
def auth_admin():
    data = request.get_json(silent=True) or {}
    password = (data.get("password") or "").strip()
    next_url = _safe_next_url(data.get("next"), default="/")

    if not password or password != ADMIN_PASSWORD:
        return jsonify({"ok": False, "error": "Contraseña incorrecta ❌"}), 401

    session["role"] = "admin"
    session["student_name"] = None
    session["banner_id"] = None
    return jsonify({"ok": True, "redirect": next_url or "/"}), 200

@app.route("/auth/student", methods=["POST"])
def auth_student():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    banner_id = (data.get("banner_id") or "").strip()
    next_url = _safe_next_url(data.get("next"), default="/registro-estudiante")

    if not name or not banner_id:
        return jsonify({"ok": False, "error": "Faltan datos (Nombre/ID Banner) ❌"}), 400

    session["role"] = "student"
    session["student_name"] = name
    session["banner_id"] = banner_id
    return jsonify({"ok": True, "redirect": next_url or "/registro-estudiante"}), 200

@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"ok": True, "redirect": "/login"}), 200

@app.route("/whoami", methods=["GET"])
def whoami():
    return jsonify({
        "ok": True,
        "role": session.get("role"),
        "student_name": session.get("student_name"),
        "banner_id": session.get("banner_id"),
    }), 200


# =========================
# ✅ Páginas (protegidas)
# =========================
@app.route("/")
@require_role("admin")
def index():
    return render_template("index.html")

@app.route("/reminders")
@require_role("admin")
def reminders_page():
    return render_template("reminders.html")

# ✅ Admin: registro completo (lista + devolver)
@app.route("/registro")
@require_role("admin")
def registro_admin_page():
    return render_template("registro.html")

# ✅ Student: inventario (para elegir equipo y mandar a registro)
@app.route("/inventario-estudiante")
@require_role("student")
def inventario_estudiante_page():
    return render_template("inventario_estudiante.html")

# ✅ Student: solo registrar (sin lista, sin devolver)
@app.route("/registro-estudiante")
@require_role("student")
def registro_estudiante_page():
    return render_template(
        "registro_estudiante.html",
        student_name=session.get("student_name", ""),
        banner_id=session.get("banner_id", ""),
    )


# =========================
# ✅ API LAB → n8n
# =========================

# ✅ Inventario público (student/admin) para inventario_estudiante.html
@app.route("/api/lab/inventario", methods=["GET"])
@require_role("admin", "student")
def api_lab_inventario_publico():
    """
    Llama al mismo webhook /listar en n8n, pero permite student también.
    Ideal para construir pantalla inventario con botones Solicitar.
    """
    try:
        r = requests.get(N8N_LISTAR, timeout=30)
        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "n8n devolvió no-JSON", "raw": (r.text or "")[:1500]}), 502
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a n8n /listar"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"api_lab_inventario error: {str(e)}"}), 500


@app.route("/api/lab/listar", methods=["GET"])
@require_role("admin")  # 👈 solo admin puede listar (tu endpoint original)
def api_lab_listar():
    try:
        r = requests.get(N8N_LISTAR, timeout=30)
        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "n8n devolvió no-JSON", "raw": (r.text or "")[:1500]}), 502
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a n8n /listar"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"api_lab_listar error: {str(e)}"}), 500

@app.route("/api/lab/prestar", methods=["POST"])
@require_role("admin", "student")
def api_lab_prestar():
    try:
        data = request.get_json(silent=True) or {}

        # 👇 Si es estudiante, fuerza nombre/banner desde sesión
        role = session.get("role")
        if role == "student":
            nombre = (session.get("student_name") or "").strip()
            banner_id = (session.get("banner_id") or "").strip()
        else:
            nombre = (data.get("nombre") or "").strip()
            banner_id = (data.get("banner_id") or data.get("bannerId") or data.get("BannerID") or "").strip()

        payload = {
            "nombre": nombre,
            "banner_id": banner_id,  # ✅ ya lo mandamos a n8n
            "semestre": data.get("semestre"),
            "equipo": (data.get("equipo") or "").strip(),
        }

        if not payload["nombre"] or payload["semestre"] in (None, "", []) or not payload["equipo"]:
            return jsonify({"ok": False, "error": "Faltan campos (nombre/semestre/equipo)"}), 400

        r = requests.post(N8N_PRESTAR, json=payload, timeout=30)
        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "n8n devolvió no-JSON", "raw": (r.text or "")[:1500]}), 502

    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a n8n /prestamo"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"api_lab_prestar error: {str(e)}"}), 500

@app.route("/api/lab/devolver", methods=["POST"])
@require_role("admin")  # 👈 solo admin devuelve
def api_lab_devolver():
    try:
        data = request.get_json(silent=True) or {}
        lab_id = (data.get("id") or "").strip()
        if not lab_id:
            return jsonify({"ok": False, "error": "Falta id"}), 400

        r = requests.post(N8N_DEVOLVER, json={"id": lab_id}, timeout=30)
        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "n8n devolvió no-JSON", "raw": (r.text or "")[:1500]}), 502

    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a n8n /devolver"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"api_lab_devolver error: {str(e)}"}), 500


# =========================
# ✅ API Recordatorios (LEGACY - Flask DB)
# =========================
@app.route("/api/reminders", methods=["GET"])
@require_role("admin")
def api_reminders_list():
    items = _load_reminders()

    def key(x):
        due = x.get("due") or "9999-12-31 23:59"
        return (x.get("done", False), due)

    items = sorted(items, key=key)
    return jsonify({"items": items}), 200

@app.route("/api/reminders", methods=["POST"])
@require_role("admin")
def api_reminders_create():
    try:
        data = request.get_json(silent=True) or {}
        text = data.get("text") or data.get("title") or ""
        due = data.get("due")
        priority = data.get("priority", "MED")

        item = _add_reminder(text=text, due=due, priority=priority)
        if not item:
            return jsonify({"error": "Texto vacío"}), 400

        return jsonify({"ok": True, "item": item}), 200

    except Exception as e:
        return jsonify({"error": f"Fallo creando recordatorio: {str(e)}"}), 500

@app.route("/api/reminders/<rid>", methods=["PATCH"])
@require_role("admin")
def api_reminders_toggle(rid):
    data = request.get_json(silent=True) or {}
    done = bool(data.get("done", False))

    items = _load_reminders()
    for x in items:
        if x.get("id") == rid:
            x["done"] = done
            _save_reminders(items)
            return jsonify({"ok": True, "item": x}), 200

    return jsonify({"error": "No existe ese recordatorio"}), 404

@app.route("/api/reminders/<rid>", methods=["DELETE"])
@require_role("admin")
def api_reminders_delete(rid):
    items = _load_reminders()
    new_items = [x for x in items if x.get("id") != rid]

    if len(new_items) == len(items):
        return jsonify({"error": "No existe ese recordatorio"}), 404

    _save_reminders(new_items)
    return jsonify({"ok": True}), 200


# =========================
# ✅ PROXY Multi-chat (FastAPI /chats)
# =========================
@app.route("/chats-proxy", methods=["GET", "POST"])
@require_role("admin")
def chats_proxy():
    try:
        if request.method == "GET":
            r = requests.get(FASTAPI_CHATS, timeout=30, headers={"Cache-Control": "no-cache"})
        else:
            data = request.get_json(silent=True) or {}
            r = requests.post(FASTAPI_CHATS, json=data, timeout=30)

        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "Respuesta no-JSON desde FastAPI /chats", "raw": (r.text or "")[:1500]}), 502

    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a FastAPI /chats"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"chats-proxy error: {str(e)}"}), 500

@app.route("/chats-proxy/<chat_id>", methods=["GET", "PATCH", "DELETE"])
@require_role("admin")
def chats_proxy_item(chat_id):
    try:
        if request.method == "GET":
            limit_raw = request.args.get("limit", "80")
            try:
                limit = int(limit_raw)
            except Exception:
                limit = 80
            limit = max(1, min(limit, 500))

            r = requests.get(f"{FASTAPI_CHATS}/{chat_id}?limit={limit}", timeout=30, headers={"Cache-Control": "no-cache"})

        elif request.method == "PATCH":
            data = request.get_json(silent=True) or {}
            r = requests.patch(f"{FASTAPI_CHATS}/{chat_id}", json=data, timeout=30)

        else:  # DELETE
            r = requests.delete(f"{FASTAPI_CHATS}/{chat_id}", timeout=30)

        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "Respuesta no-JSON desde FastAPI /chats/{chat_id}", "raw": (r.text or "")[:1500]}), 502

    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a FastAPI /chats/{chat_id}"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"chats-proxy item error: {str(e)}"}), 500

@app.route("/chats", methods=["GET", "POST"])
@require_role("admin")
def chats_alias():
    return chats_proxy()

@app.route("/chats/<chat_id>", methods=["GET", "PATCH", "DELETE"])
@require_role("admin")
def chats_alias_item(chat_id):
    return chats_proxy_item(chat_id)


# =========================
# ✅ PROXY TAREAS / RECORDATORIOS reales (FastAPI /tasks)
# =========================
@app.route("/tasks-proxy", methods=["GET", "POST"])
@require_role("admin")
def tasks_proxy():
    try:
        if request.method == "GET":
            r = requests.get(FASTAPI_TASKS, timeout=30, headers={"Cache-Control": "no-cache"})
        else:
            data = request.get_json(silent=True) or {}
            r = requests.post(FASTAPI_TASKS, json=data, timeout=30)

        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "Respuesta no-JSON desde FastAPI /tasks", "raw": (r.text or "")[:1500]}), 502

    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a FastAPI /tasks"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"tasks-proxy error: {str(e)}"}), 500

@app.route("/tasks-proxy/<task_id>", methods=["DELETE"])
@require_role("admin")
def tasks_proxy_delete(task_id):
    try:
        r = requests.delete(f"{FASTAPI_TASKS}/{task_id}", timeout=30)
        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"ok": False, "error": "Respuesta no-JSON desde FastAPI /tasks/{id}", "raw": (r.text or "")[:1500]}), 502
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout borrando task en FastAPI"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"tasks delete proxy error: {str(e)}"}), 500

@app.route("/tasks", methods=["GET", "POST"])
@require_role("admin")
def tasks_alias():
    return tasks_proxy()

@app.route("/tasks/<task_id>", methods=["DELETE"])
@require_role("admin")
def tasks_alias_item(task_id):
    return tasks_proxy_delete(task_id)


# =========================
# ✅ PROXY MEMORIA (FastAPI /chat) (compat antiguo)
# =========================
@app.route("/chat-proxy", methods=["GET"])
@require_role("admin")
def chat_proxy():
    try:
        limit_raw = request.args.get("limit", "80")
        chat_id = (request.args.get("chat_id", "default") or "default").strip()

        try:
            limit = int(limit_raw)
        except Exception:
            limit = 80
        limit = max(1, min(limit, 300))

        headers = {"Cache-Control": "no-cache"}
        r = requests.get(f"{FASTAPI_CHAT}?limit={limit}&chat_id={chat_id}", timeout=30, headers=headers)

        try:
            payload = r.json()
        except Exception:
            return jsonify({
                "ok": False,
                "error": "FastAPI /chat devolvió respuesta no JSON",
                "status_code": r.status_code,
                "raw": (r.text or "")[:1200],
            }), 502

        return jsonify(payload), r.status_code

    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout llamando a FastAPI /chat"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"chat-proxy error: {str(e)}"}), 500

@app.route("/chat-proxy", methods=["DELETE"])
@require_role("admin")
def chat_clear_proxy():
    try:
        chat_id = (request.args.get("chat_id", "default") or "default").strip()
        r = requests.delete(f"{FASTAPI_CHAT}?chat_id={chat_id}", timeout=30)
        try:
            payload = r.json()
        except Exception:
            return jsonify({
                "ok": False,
                "error": "FastAPI /chat (DELETE) devolvió no JSON",
                "status_code": r.status_code,
                "raw": (r.text or "")[:1200],
            }), 502
        return jsonify(payload), r.status_code
    except requests.exceptions.Timeout:
        return jsonify({"ok": False, "error": "Timeout borrando historial en FastAPI /chat"}), 504
    except Exception as e:
        return jsonify({"ok": False, "error": f"chat-clear proxy error: {str(e)}"}), 500


# =========================
# ✅ Health check
# =========================
@app.route("/health", methods=["GET"])
def health():
    try:
        r = requests.get(FASTAPI_ROOT, timeout=10)
        data = r.json()
        return jsonify({"ok": True, "fastapi_ok": True, "fastapi": data}), 200
    except Exception as e:
        return jsonify({
            "ok": False,
            "fastapi_ok": False,
            "error": str(e),
            "hint": "Asegúrate de tener corriendo: uvicorn main:app --reload --host 0.0.0.0 --port 8000"
        }), 500


# =========================
# ✅ Proxy audio → FastAPI /talk (con chat_id)
# =========================
@app.route("/talk-proxy", methods=["POST"])
@require_role("admin")
def talk_proxy():
    if "audio" not in request.files:
        return jsonify({"error": "No se recibió audio"}), 400

    audio_file = request.files["audio"]
    chat_id = request.form.get("chat_id", "default").strip()

    try:
        files = {"audio": (audio_file.filename, audio_file.stream, audio_file.mimetype)}

        r = requests.post(
            f"{FASTAPI_TALK}?chat_id={chat_id}",
            files=files,
            timeout=300
        )

        try:
            payload = r.json()
        except Exception:
            return jsonify({"error": "Respuesta no-JSON desde FastAPI", "raw": r.text}), r.status_code

        transcript = (
            payload.get("transcript")
            or payload.get("text")
            or payload.get("user_text")
            or ""
        )
        if transcript and _looks_like_reminder(transcript):
            due = _parse_due_from_text(transcript)
            what = _extract_reminder_text(transcript) or transcript
            _add_reminder(text=what, due=due, priority="MED")

        return jsonify(payload), r.status_code

    except requests.exceptions.Timeout:
        return jsonify({"error": "Timeout: /talk tardó demasiado (Whisper/Tavily). Intenta de nuevo."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# ✅ Proxy texto → FastAPI /ask (con chat_id)
# =========================
@app.route("/ask-proxy", methods=["POST"])
@require_role("admin")
def ask_proxy():
    data = request.get_json(silent=True) or {}
    q = (data.get("question") or "").strip()
    chat_id = (data.get("chat_id") or "default").strip()

    if not q:
        return jsonify({"error": "question vacío"}), 400

    if _looks_like_reminder(q):
        due = _parse_due_from_text(q)
        what = _extract_reminder_text(q) or q
        _add_reminder(text=what, due=due, priority="MED")

    try:
        r = requests.post(
            FASTAPI_ASK,
            json={"question": q, "chat_id": chat_id},
            timeout=180
        )

        try:
            return jsonify(r.json()), r.status_code
        except Exception:
            return jsonify({"error": "Respuesta no-JSON desde FastAPI", "raw": r.text}), r.status_code

    except requests.exceptions.Timeout:
        return jsonify({"error": "Timeout: /ask tardó demasiado. Intenta de nuevo."}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Proxy Firebase ultima
# =========================
@app.route("/firebase/ultima-proxy", methods=["GET"])
@require_role("admin")
def firebase_ultima_proxy():
    try:
        r = requests.get(FASTAPI_FB_ULTIMA, timeout=30, headers={"Cache-Control": "no-cache"})
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Proxy Firebase sensores (crudo)
# =========================
@app.route("/firebase/sensores-proxy", methods=["GET"])
@require_role("admin")
def firebase_sensores_proxy():
    try:
        r = requests.get(FASTAPI_FB_SENSORES, timeout=30, headers={"Cache-Control": "no-cache"})
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================
# Abrir el Speaker real (FastAPI)
# =========================
@app.route("/speaker-redirect")
@require_role("admin")
def speaker_redirect():
    return redirect(FASTAPI_SPEAKER, code=302)


# =========================
# (Opcional) redirect a la app futurista de FastAPI
# =========================
@app.route("/spectra")
@require_role("admin")
def spectra_redirect():
    return redirect(f"{FASTAPI_BASE}/app", code=302)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
