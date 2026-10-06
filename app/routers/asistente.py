"""Sección Asistente (Gemini) + pantalla Conexiones (ParkaHub y Gemini, solo admin)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..actividad import registrar
from ..auth import requiere, solo_admin
from ..db import get_db
from ..models import Usuario
from ..services import ajustes, asistente, parkahub
from ..templating import render

router = APIRouter()
acceso = requiere("asistente")

SUGERENCIAS = [
    "¿Cómo viene el día? Resumen de despacho, devoluciones y tareas",
    "¿Qué preguntas de compradores hay sin responder? Proponé respuestas",
    "Buscá la venta 2000000000000 y decime qué lleva y si se puede despachar",
    "¿Qué SKUs se devolvieron más en los últimos 30 días y por qué motivo?",
    "¿Cómo está la reputación en Mercado Libre?",
]


@router.get("/asistente")
def pagina(request: Request, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return render(request, "asistente.html", u=u, sugerencias=SUGERENCIAS,
                  gemini_ok=asistente.configurado(), parkahub_ok=parkahub.configurado())


@router.get("/asistente/api/historial")
def ver_historial(u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return {"mensajes": asistente.historial(db, u)}


class MensajeIn(BaseModel):
    texto: str


@router.post("/asistente/api/mensaje")
def mensaje(body: MensajeIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    try:
        r = asistente.responder(db, u, body.texto)
    except asistente.AsistenteError as e:
        raise HTTPException(400, str(e)) from None
    registrar(db, u, "asistente_consulta", body.texto[:120], seccion="asistente")
    return r


@router.post("/asistente/api/borrar")
def borrar(u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    asistente.borrar_historial(db, u)
    return {"ok": True}


# ─────────────── Conexiones (solo admin) ───────────────
@router.get("/admin/conexiones")
def conexiones(request: Request, u: Usuario = Depends(solo_admin), db: Session = Depends(get_db)):
    return render(request, "admin_conexiones.html", u=u, aj=ajustes.resumen(db))


class AjustesIn(BaseModel):
    valores: dict[str, str]


@router.post("/admin/api/conexiones")
def guardar(body: AjustesIn, u: Usuario = Depends(solo_admin), db: Session = Depends(get_db)):
    cambiados = []
    for k, v in body.valores.items():
        if k not in ajustes.CLAVES:
            raise HTTPException(400, f"ajuste desconocido: {k}")
        v = (v or "").strip()
        if ajustes.CLAVES[k][2] and (not v or v.startswith("•")):
            continue  # secreto sin cambios (se muestra enmascarado)
        ajustes.guardar(db, k, v)
        cambiados.append(k)
    registrar(db, u, "conexiones_guardadas", ", ".join(cambiados))
    return {"ok": True, "guardados": cambiados, "aj": ajustes.resumen(db)}


@router.post("/admin/api/conexiones/probar/{que}")
def probar(que: str, u: Usuario = Depends(solo_admin), db: Session = Depends(get_db)):
    if que == "parkahub":
        try:
            r = parkahub.probar()
        except parkahub.ParkaHubError as e:
            return {"ok": False, "mensaje": str(e)}
        return {"ok": True, "mensaje": "Conectado a ParkaHub" + (" · Mercado Libre conectado" if r["ml_conectado"]
                                                                  else " · ojo: ML aparece desconectado en ParkaHub")}
    if que == "gemini":
        try:
            asistente._llamar({"contents": [{"role": "user", "parts": [{"text": "Respondé solo: ok"}]}]})
        except asistente.AsistenteError as e:
            return {"ok": False, "mensaje": str(e)}
        return {"ok": True, "mensaje": f"Gemini responde ({ajustes.leer('gemini_modelo', db)})"}
    if que == "odoo":
        from ..services import odoo
        try:
            r = odoo.probar(db)
        except odoo.OdooError as e:
            return {"ok": False, "mensaje": str(e)}
        return {"ok": True, "mensaje": f"Conectado a Odoo · {r['con_codigo']} productos con código de barras"}
    if que == "ecommerce":
        from ..services import re_drive
        try:
            r = re_drive.estado(db)
        except re_drive.REDriveError as e:
            return {"ok": False, "mensaje": str(e)}
        return {"ok": True, "mensaje": f"Conectado · carpeta \"Recolección Ecommerce\" (Apps Script {r.get('version', '')})"}
    raise HTTPException(404)
