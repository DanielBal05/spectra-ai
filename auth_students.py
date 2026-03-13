from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
import os
import json
import re

router = APIRouter()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

STUDENTS_FILE = os.path.join(DATA_DIR, "students.json")


def load_students():
    if not os.path.exists(STUDENTS_FILE):
        return []
    try:
        with open(STUDENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []


def save_students(students):
    with open(STUDENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(students, f, indent=2, ensure_ascii=False)


def valid_banner(banner: str):
    return bool(re.fullmatch(r"A\d+", banner))


def find_student(banner):
    students = load_students()
    for s in students:
        if s["banner"].upper() == banner.upper():
            return s
    return None


# ===============================
# PANTALLA REGISTRO
# ===============================

@router.get("/registro-estudiante", response_class=HTMLResponse)
def registro_page():
    return """
<h2>Registro de Estudiante</h2>

<input id="nombre" placeholder="Nombre">
<input id="banner" placeholder="A00123456">

<button onclick="registrar()">Registrar</button>

<script>
async function registrar(){

let nombre=document.getElementById("nombre").value
let banner=document.getElementById("banner").value

const r=await fetch("/registro-estudiante",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({nombre,banner})
})

const data=await r.json()

if(data.ok){
alert("Registro exitoso")
window.location="/login"
}else{
alert(data.error)
}

}
</script>
"""


# ===============================
# REGISTRAR
# ===============================

@router.post("/registro-estudiante")
async def register_student(request: Request):

    data = await request.json()

    nombre = data.get("nombre","").strip()
    banner = data.get("banner","").strip().upper()

    if not valid_banner(banner):
        return JSONResponse({
            "ok":False,
            "error":"El banner debe iniciar con A y solo números"
        })

    students=load_students()

    if find_student(banner):
        return JSONResponse({
            "ok":False,
            "error":"Estudiante ya registrado"
        })

    students.append({
        "nombre":nombre,
        "banner":banner
    })

    save_students(students)

    return {"ok":True}


# ===============================
# LOGIN ESTUDIANTE
# ===============================

@router.post("/auth/student")
async def auth_student(request:Request):

    data=await request.json()

    nombre=data.get("nombre","").strip()
    banner=data.get("banner","").strip().upper()

    student=find_student(banner)

    if not student:
        return JSONResponse({
            "ok":False,
            "error":"Este estudiante no está registrado"
        })

    if student["nombre"].lower()!=nombre.lower():
        return JSONResponse({
            "ok":False,
            "error":"El nombre no coincide"
        })

    return {
        "ok":True,
        "redirect":"/spectra"
    }
