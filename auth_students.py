from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
import os
import re
import psycopg

router = APIRouter()

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("Falta DATABASE_URL en variables de entorno")
    return psycopg.connect(DATABASE_URL)


def init_students_table():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    id SERIAL PRIMARY KEY,
                    nombre TEXT NOT NULL,
                    banner TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        conn.commit()


def valid_banner(banner: str):
    return bool(re.fullmatch(r"A\d+", banner))


def find_student(banner: str):
    banner = (banner or "").strip().upper()
    if not banner:
        return None

    init_students_table()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT nombre, banner FROM students WHERE UPPER(banner) = UPPER(%s) LIMIT 1",
                (banner,)
            )
            row = cur.fetchone()

    if not row:
        return None

    return {
        "nombre": row[0],
        "banner": row[1]
    }


@router.get("/registro-alumno", response_class=HTMLResponse)
def registro_page():
    return """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Spectra AI — Registro Estudiante</title>

<style>
:root{
  --bg0:#020617;
  --bg1:#000;
  --card: rgba(11, 18, 36, .62);
  --stroke: rgba(64, 116, 255, .16);
  --stroke2: rgba(34, 211, 238, .22);
  --text:#e5e7eb;
  --muted:#9ca3af;
  --cyan:#22d3ee;
  --purple:#a855f7;
  --pink:#ec4899;
  --danger:#f97373;
  --ok:#4ade80;
  --shadow: 0 20px 70px rgba(0,0,0,.55);
  --radius: 22px;
}
*{ box-sizing:border-box; margin:0; padding:0; }
html, body { height:100%; }

body{
  font-family: system-ui, -apple-system, Segoe UI, sans-serif;
  color: var(--text);
  display:flex;
  align-items:center;
  justify-content:center;
  overflow:hidden;
  position:relative;
  background:
    radial-gradient(1200px 600px at 15% 10%, rgba(34,211,238,.18), transparent 55%),
    radial-gradient(900px 500px at 85% 18%, rgba(168,85,247,.16), transparent 60%),
    radial-gradient(700px 500px at 50% 85%, rgba(236,72,153,.10), transparent 60%),
    radial-gradient(circle at top, #0b1120 0, #020617 45%, #000 100%);
}
body::before{
  content:"";
  position:absolute;
  inset:-18%;
  background:
    radial-gradient(circle at 30% 40%, rgba(0,140,255,.38), transparent 42%),
    radial-gradient(circle at 75% 30%, rgba(0,110,255,.26), transparent 46%),
    radial-gradient(circle at 55% 75%, rgba(0,80,200,.18), transparent 58%),
    radial-gradient(circle at 45% 55%, rgba(34,211,238,.10), transparent 60%);
  filter: blur(70px);
  opacity: .95;
  animation: floatWave 12s ease-in-out infinite alternate;
  z-index:-2;
  pointer-events:none;
}
body::after{
  content:"";
  position:absolute;
  inset:0;
  background:
    linear-gradient(to right, rgba(0,170,255,.085) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(0,170,255,.055) 1px, transparent 1px),
    radial-gradient(2px 2px at 20% 30%, rgba(255,255,255,.22), transparent 55%),
    radial-gradient(2px 2px at 70% 20%, rgba(255,255,255,.16), transparent 55%),
    radial-gradient(2px 2px at 40% 80%, rgba(255,255,255,.12), transparent 55%),
    radial-gradient(1px 1px at 85% 70%, rgba(255,255,255,.12), transparent 55%);
  background-size: 60px 60px, 60px 60px, auto, auto, auto, auto;
  opacity:.50;
  mask-image: radial-gradient(circle at 50% 40%, rgba(0,0,0,.95), transparent 72%);
  animation: gridDrift 18s linear infinite;
  z-index:-1;
  pointer-events:none;
}
@keyframes floatWave{
  0%{ transform: translateX(-40px) translateY(-25px) scale(1); }
  50%{ transform: translateX(45px) translateY(25px) scale(1.08); }
  100%{ transform: translateX(-15px) translateY(10px) scale(1.02); }
}
@keyframes gridDrift{
  0%{ transform: translateY(0px); }
  50%{ transform: translateY(18px); }
  100%{ transform: translateY(0px); }
}
.wrap{
  position:relative;
  width:min(92vw, 440px);
}
.glow{
  position:absolute; inset:-40px;
  background:
    radial-gradient(280px 180px at 25% 20%, rgba(34,211,238,.20), transparent 65%),
    radial-gradient(320px 200px at 75% 30%, rgba(168,85,247,.18), transparent 70%),
    radial-gradient(260px 200px at 50% 85%, rgba(236,72,153,.10), transparent 70%);
  filter: blur(18px);
  opacity:.9;
  pointer-events:none;
}
.card{
  position:relative;
  background: var(--card);
  border: 1px solid rgba(255,255,255,.06);
  border-radius: var(--radius);
  padding: 34px 28px 26px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(14px);
  overflow:hidden;
  transform: translateZ(0);
}
.card::before{
  content:"";
  position:absolute; inset:0;
  border-radius: inherit;
  padding:1px;
  background:
    linear-gradient(135deg, rgba(34,211,238,.55), rgba(168,85,247,.35), rgba(236,72,153,.20));
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  opacity:.45;
  pointer-events:none;
}
.card::after{
  content:"";
  position:absolute; left:-30%; top:-20%;
  width:160%; height:60px;
  background: linear-gradient(90deg, transparent, rgba(34,211,238,.12), transparent);
  transform: rotate(8deg);
  animation: scan 5.2s linear infinite;
  pointer-events:none;
}
@keyframes scan{
  0%{ transform: translateY(-140px) rotate(8deg); opacity:.0; }
  15%{ opacity:.65; }
  55%{ opacity:.35; }
  100%{ transform: translateY(520px) rotate(8deg); opacity:.0; }
}
.brand{
  display:flex;
  align-items:center;
  justify-content:center;
  gap:10px;
  margin-bottom: 18px;
}
.badge{
  width:46px; height:46px;
  border-radius: 16px;
  display:grid;
  place-items:center;
  background:
    radial-gradient(circle at 30% 30%, rgba(34,211,238,.22), transparent 60%),
    linear-gradient(135deg, rgba(34,211,238,.16), rgba(168,85,247,.12));
  border:1px solid rgba(34,211,238,.22);
  box-shadow: 0 0 22px rgba(34,211,238,.10);
}
.badge svg{
  width:22px; height:22px;
  fill:none;
  stroke: rgba(229,231,235,.9);
  stroke-width: 1.7;
}
h2{
  text-align:center;
  letter-spacing:.28em;
  text-transform:uppercase;
  font-weight: 800;
  font-size: 13px;
  color: var(--muted);
  margin-bottom: 6px;
}
.sub{
  text-align:center;
  color: rgba(229,231,235,.78);
  font-size: 13px;
  margin-bottom: 22px;
}
.hr{
  height:1px;
  background: linear-gradient(90deg, transparent, rgba(34,211,238,.22), rgba(168,85,247,.18), transparent);
  margin: 14px 0 22px;
  opacity:.8;
}
label{
  font-size: 11px;
  color: rgba(156,163,175,.95);
  margin-top: 12px;
  display:block;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.field{
  position:relative;
  margin-top:6px;
}
input{
  width:100%;
  padding: 12px 12px 12px 40px;
  border-radius: 14px;
  border: 1px solid rgba(34,211,238,.12);
  background: rgba(5, 11, 24, .78);
  color: white;
  outline:none;
  transition: border-color .12s ease, box-shadow .12s ease;
}
input::placeholder{
  color: rgba(156,163,175,.55);
}
input:focus{
  border-color: rgba(34,211,238,.55);
  box-shadow: 0 0 0 3px rgba(34,211,238,.10);
}
.icon{
  position:absolute;
  left:12px; top:50%;
  transform: translateY(-50%);
  width:18px; height:18px;
  opacity:.85;
  fill:none;
  stroke: rgba(229,231,235,.80);
  stroke-width: 1.7;
}
.btn{
  width:100%;
  padding: 13px 14px;
  border-radius: 16px;
  border: 1px solid rgba(34,211,238,.18);
  background:
    linear-gradient(135deg, rgba(34,211,238,.18), rgba(168,85,247,.10)),
    radial-gradient(120px 50px at 30% 10%, rgba(34,211,238,.16), transparent 60%);
  color: white;
  font-weight: 800;
  cursor: pointer;
  margin-top: 14px;
  transition: transform .12s ease, border-color .12s ease, box-shadow .12s ease, filter .12s ease;
  box-shadow: 0 10px 30px rgba(0,0,0,.25);
}
.btn:hover{
  border-color: rgba(34,211,238,.55);
  box-shadow: 0 14px 38px rgba(0,0,0,.35), 0 0 22px rgba(34,211,238,.10);
  filter: brightness(1.04);
  transform: translateY(-1px);
}
.btn:active{
  transform: translateY(0px) scale(.99);
}
.btn.submit{
  background: linear-gradient(135deg, rgba(34,211,238,.95), rgba(168,85,247,.55));
  border: 1px solid rgba(34,211,238,.25);
  font-weight: 900;
}
.btn.secondary{
  background: rgba(2,6,23,.45);
  border: 1px solid rgba(255,255,255,.10);
}
.error{
  margin-top: 12px;
  color: var(--danger);
  font-size: 13px;
  text-align:center;
  min-height: 18px;
}
.success{
  margin-top: 12px;
  color: var(--ok);
  font-size: 13px;
  text-align:center;
  min-height: 18px;
}
@media (max-width:420px){
  .card{ padding: 30px 22px 22px; }
}
</style>
</head>

<body>
  <div class="wrap">
    <div class="glow"></div>

    <div class="card">
      <div class="brand">
        <div class="badge" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M12 3v4M7 7l5-4 5 4M5 11l7 4 7-4M12 15v6M5 11v6l7 4 7-4v-6"/>
          </svg>
        </div>
      </div>

      <h2>Registro</h2>
      <div class="sub">Crea tu acceso como estudiante</div>

      <div class="hr"></div>

      <label>Nombre</label>
      <div class="field">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M20 21a8 8 0 0 0-16 0"/>
          <path d="M12 11a4 4 0 1 0-4-4 4 4 0 0 0 4 4z"/>
        </svg>
        <input type="text" id="nombre" placeholder="Tu nombre" />
      </div>

      <label>ID Banner</label>
      <div class="field">
        <svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 7h16M4 12h16M4 17h10"/>
          <path d="M4 5h16v14H4z"/>
        </svg>
        <input type="text" id="banner" placeholder="A00123456" />
      </div>

      <button class="btn submit" onclick="registrar()">Registrar</button>
      <button class="btn secondary" onclick="window.location.href='/login'">Volver al login</button>

      <div class="error" id="error"></div>
      <div class="success" id="success"></div>
    </div>
  </div>

<script>
async function registrar(){
  const nombre = document.getElementById("nombre").value.trim();
  const banner = document.getElementById("banner").value.trim().toUpperCase();
  const error = document.getElementById("error");
  const success = document.getElementById("success");

  error.textContent = "";
  success.textContent = "";

  if(!nombre || !banner){
    error.textContent = "Completa todos los campos ❗";
    return;
  }

  if(!/^A\d+$/.test(banner)){
    error.textContent = "El ID Banner debe empezar con A y luego solo números. Ej: A00123456";
    return;
  }

  try{
    const r = await fetch("/auth/register", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ nombre, banner })
    });

    const j = await r.json().catch(() => ({}));

    if(!r.ok){
      error.textContent = j.error || "No se pudo registrar ❌";
      return;
    }

    success.textContent = "Registro exitoso. Redirigiendo al login...";
    setTimeout(() => {
      window.location.href = "/login";
    }, 1200);

  }catch(e){
    error.textContent = "Error de red ❌";
  }
}
</script>
</body>
</html>
"""


@router.post("/registro-alumno")
async def register_student(request: Request):
    try:
        data = await request.json()

        nombre = (data.get("nombre") or "").strip()
        banner = (data.get("banner") or "").strip().upper()

        if not nombre or not banner:
            return JSONResponse({
                "ok": False,
                "error": "Faltan datos (Nombre/ID Banner)"
            }, status_code=400)

        if not valid_banner(banner):
            return JSONResponse({
                "ok": False,
                "error": "El banner debe iniciar con A y solo números"
            }, status_code=400)

        init_students_table()

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM students WHERE UPPER(banner) = UPPER(%s) LIMIT 1",
                    (banner,)
                )
                exists = cur.fetchone()

                if exists:
                    return JSONResponse({
                        "ok": False,
                        "error": "Estudiante ya registrado"
                    }, status_code=400)

                cur.execute(
                    "INSERT INTO students (nombre, banner) VALUES (%s, %s)",
                    (nombre, banner)
                )
            conn.commit()

        return JSONResponse({"ok": True}, status_code=200)

    except Exception as e:
        return JSONResponse({
            "ok": False,
            "error": f"Error registrando estudiante: {str(e)}"
        }, status_code=500)
