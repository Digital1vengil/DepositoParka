"""Sección Devoluciones: buscar artículo → color → talle → cantidad → confirmar (Drive + etiquetas)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..actividad import registrar
from ..auth import requiere
from ..db import get_db, hoy
from ..models import Articulo, Devolucion, DevolucionItem, Usuario
from ..services import devoluciones as svc
from ..services.etiquetas import pdf_etiquetas
from ..services.etiquetas_html import html_etiquetas
from ..templating import render

router = APIRouter(prefix="/devoluciones")
acceso = requiere("devoluciones")


@router.get("")
def pagina(request: Request, u: Usuario = Depends(acceso)):
    return render(request, "devoluciones.html", u=u)


@router.get("/api/buscar")
def buscar(q: str = "", u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return {"modelos": svc.buscar_modelos(db, q)}


class ItemIn(BaseModel):
    articulo_id: int
    cantidad: int = 1
    estado: str = "OPTIMO"


class ConfirmarIn(BaseModel):
    items: list[ItemIn]


def dev_json(d: Devolucion) -> dict:
    try:
        resp = json.loads(d.drive_resp) if d.drive_ok and d.drive_resp else {}
    except ValueError:
        resp = {}
    return {"id": d.id, "hora": d.creado_at.strftime("%d/%m %H:%M"), "total": d.total,
            "usuario": d.usuario.nombre if d.usuario else "", "drive_ok": d.drive_ok,
            "drive_error": "" if d.drive_ok else d.drive_resp, "drive": resp,
            "items": [{"articulo": i.articulo, "color": i.color, "talle": i.talle, "cantidad": i.cantidad,
                       "estado": i.estado, "sku": i.sku} for i in d.items]}


def _enviar(db: Session, d: Devolucion) -> None:
    try:
        r = svc.enviar(d)
        d.drive_ok, d.drive_resp = True, json.dumps(r, ensure_ascii=False)[:2000]
    except Exception as e:  # noqa: BLE001
        d.drive_ok, d.drive_resp = False, f"No se pudo guardar en Drive: {e}"[:1000]
    db.commit()


@router.post("/api/confirmar")
def confirmar(body: ConfirmarIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    items = [i for i in body.items if i.cantidad > 0]
    if not items:
        raise HTTPException(400, "Agregá al menos un artículo")
    d = Devolucion(fecha=hoy(), usuario_id=u.id)
    db.add(d)
    for it in items:
        a = db.get(Articulo, it.articulo_id)
        if not a or not a.codigo_barras:
            raise HTTPException(400, "Hay un artículo que no existe o no tiene código de barras")
        if svc.marca(a) not in ("Parka", "Puffers"):
            raise HTTPException(400, f"{a.nombre}: las devoluciones son solo de artículos Parka o Puffers")
        color, talle = svc.color_talle(a)
        d.items.append(DevolucionItem(articulo_id=a.id, codigo_barras=a.codigo_barras, sku=a.sku,
                                      articulo=a.articulo, color=color, talle=talle,
                                      estado="FALLA" if it.estado.upper() == "FALLA" else "OPTIMO",
                                      cantidad=min(it.cantidad, 999)))
    d.total = sum(i.cantidad for i in d.items)
    db.commit()
    registrar(db, u, "devolucion", f"PD-{d.id} · {d.total} unidades", "devoluciones")
    _enviar(db, d)
    return {"ok": True, "devolucion": dev_json(d)}


@router.post("/api/{did}/reintentar")
def reintentar(did: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    d = db.get(Devolucion, did)
    if not d:
        raise HTTPException(404)
    if d.drive_ok:
        return {"ok": True, "devolucion": dev_json(d)}
    _enviar(db, d)
    if not d.drive_ok:
        raise HTTPException(502, d.drive_resp)
    return {"ok": True, "devolucion": dev_json(d)}


@router.get("/api/historial")
def historial(u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    ds = db.scalars(select(Devolucion).order_by(Devolucion.id.desc()).limit(25)).all()
    return {"devoluciones": [dev_json(d) for d in ds]}


def _lista_etiquetas(db: Session, did: int) -> tuple[Devolucion, list[dict]]:
    d = db.get(Devolucion, did)
    if not d:
        raise HTTPException(404)
    nombres = {a.id: a.nombre for a in db.scalars(select(Articulo).where(
        Articulo.id.in_([i.articulo_id for i in d.items if i.articulo_id]))).all()}
    lista = [{"nombre": nombres.get(i.articulo_id, ""), "articulo": i.articulo, "color": i.color, "talle": i.talle,
              "sku": i.sku, "codigo_barras": i.codigo_barras} for i in d.items for _ in range(i.cantidad)]
    return d, lista


@router.get("/{did}/etiquetas.html")
def etiquetas_html(did: int, desde: int = 1, guias: bool = False, u: Usuario = Depends(acceso),
                   db: Session = Depends(get_db)):
    d, lista = _lista_etiquetas(db, did)
    return HTMLResponse(html_etiquetas(lista, desde=desde, guias=guias, titulo=f"Etiquetas PD-{d.id}"))


@router.get("/{did}/etiquetas.pdf")
def etiquetas(did: int, desde: int = 1, guias: bool = False, u: Usuario = Depends(acceso),
              db: Session = Depends(get_db)):
    d, lista = _lista_etiquetas(db, did)
    return Response(pdf_etiquetas(lista, desde=desde, guias=guias), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="etiquetas_PD-{d.id}.pdf"'})
