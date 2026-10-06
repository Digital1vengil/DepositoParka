"""Sección Recolección Ecommerce (reemplaza a Recolección y a Control de paquetes).

- Preparar: el admin (o Supervisión) crea la búsqueda con los archivos de la carpeta del día en Drive.
- Equipo: los operarios se suman; la app reparte la ruta en tramos parejos.
- Mi búsqueda: cada uno ve lo suyo en orden de ruta y escanea lo que encuentra (verde / rojo).
- Mesa de control: se usa la API de /control (etiqueta del envío → prendas)."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..actividad import registrar
from ..auth import puede, requiere
from ..db import get_db, hoy
from ..models import Control, ControlItem, Usuario
from ..services import ecommerce as svc
from ..services import re_drive
from ..templating import render

router = APIRouter(prefix="/ecommerce")
acceso = requiere("ecommerce")
MAX_BYTES = 15 * 1024 * 1024


def _organiza(db: Session, u: Usuario) -> bool:
    """Crear búsquedas, repartir y sacar gente: admin o quien tenga Supervisión."""
    return puede(db, u, "supervision")


def _exigir_organiza(db: Session, u: Usuario) -> None:
    if not _organiza(db, u):
        raise HTTPException(403, "Esto lo hace quien organiza la búsqueda (admin o con acceso a Supervisión)")


def _control(db: Session, cid: int) -> Control:
    c = db.get(Control, cid)
    if not c or c.modo != svc.MODO:
        raise HTTPException(404, "No existe esa búsqueda")
    return c


def _abierta(c: Control) -> None:
    if c.estado != "abierto":
        raise HTTPException(400, "Esta búsqueda ya está cerrada")


@router.get("")
def pagina(request: Request, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return render(request, "ecommerce.html", u=u, organiza=_organiza(db, u), drive_ok=re_drive.configurado(db),
                  puede_vincular=puede(db, u, "supervision"))


# ─────────────── preparar ───────────────

@router.get("/api/drive")
def drive_hoy(fecha: str = "", u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    _exigir_organiza(db, u)
    try:
        if fecha and fecha != hoy().isoformat():
            date.fromisoformat(fecha)
            d = re_drive.listar(db, fecha)
        else:
            d = re_drive.hoy(db)
    except ValueError:
        raise HTTPException(400, "Fecha inválida")
    except re_drive.REDriveError as e:
        raise HTTPException(502, str(e))
    archivos = [a for a in (d.get("archivos") or []) if a.get("tipo") in ("ml", "tn", "otro")]
    return {"fecha": d.get("fecha") or fecha, "url": d.get("url", ""), "existe": d.get("existe", True),
            "archivos": archivos}


class DriveIn(BaseModel):
    ids: list[str]
    nombre: str = ""


@router.post("/api/crear/drive")
def crear_drive(body: DriveIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    _exigir_organiza(db, u)
    if not body.ids:
        raise HTTPException(400, "Marcá al menos un archivo")
    try:
        datos = [re_drive.archivo(db, i) for i in body.ids[:20]]
    except re_drive.REDriveError as e:
        raise HTTPException(502, str(e))
    return _crear(db, u, datos, body.nombre, "drive")


async def _leer_subidos(archivos: list[UploadFile]) -> list[tuple[str, bytes]]:
    datos = []
    for f in archivos:
        b = await f.read()
        if len(b) > MAX_BYTES:
            raise HTTPException(400, f"{f.filename}: archivo demasiado grande")
        datos.append((f.filename or "archivo", b))
    return datos


@router.post("/api/crear/archivo")
async def crear_archivo(nombre: str = Form(""), archivos: list[UploadFile] = File(...),
                        u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    _exigir_organiza(db, u)
    return _crear(db, u, await _leer_subidos(archivos), nombre, "archivo")


def _crear(db: Session, u: Usuario, datos: list[tuple[str, bytes]], nombre: str, origen: str) -> dict:
    try:
        c, errores = svc.crear(db, u, datos, nombre, origen)
    except ValueError as e:
        raise HTTPException(400, str(e))
    registrar(db, u, "ecommerce_creada", f"RE-{c.id} · {c.nombre} · {len(c.paquetes)} paquetes", "ecommerce")
    return {"ok": True, "busqueda": svc.resumen(c, u), "errores": errores}


@router.post("/api/{cid}/agregar")
async def agregar(cid: int, archivos: list[UploadFile] = File(...), u: Usuario = Depends(acceso),
                  db: Session = Depends(get_db)):
    _exigir_organiza(db, u)
    c = _control(db, cid)
    _abierta(c)
    nuevos, act, errores = svc.sumar_archivos(db, c, await _leer_subidos(archivos))
    return {"ok": True, "nuevos": nuevos, "actualizados": act, "errores": errores, "busqueda": svc.resumen(c, u)}


@router.get("/api/lista")
def lista(u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return {"busquedas": [svc.resumen(c, u) for c in svc.lista(db)], "hoy": hoy().isoformat()}


@router.get("/api/{cid}")
def ver(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    c = _control(db, cid)
    return {"busqueda": svc.resumen(c, u)}


# ─────────────── equipo ───────────────

@router.post("/api/{cid}/sumarme")
def sumarme(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    c = _control(db, cid)
    _abierta(c)
    svc.sumarse(db, c, u)
    registrar(db, u, "ecommerce_sumado", f"RE-{c.id} · {c.nombre}", "ecommerce")
    return {"ok": True, "busqueda": svc.resumen(c, u)}


@router.post("/api/{cid}/salir")
def salir(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    c = _control(db, cid)
    svc.salir(db, c, u.id)
    return {"ok": True, "busqueda": svc.resumen(c, u)}


@router.post("/api/{cid}/sacar/{uid}")
def sacar(cid: int, uid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    _exigir_organiza(db, u)
    c = _control(db, cid)
    svc.salir(db, c, uid)
    return {"ok": True, "busqueda": svc.resumen(c, u)}


@router.post("/api/{cid}/repartir")
def repartir(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    _exigir_organiza(db, u)
    c = _control(db, cid)
    svc.repartir(db, c)
    db.commit()
    return {"ok": True, "busqueda": svc.resumen(c, u)}


# ─────────────── búsqueda ───────────────

@router.get("/api/{cid}/mia")
def mia(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    c = _control(db, cid)
    return {"grupos": svc.grupos_de(c, u.id, solo_pendientes=False), "busqueda": svc.resumen(c, u)}


@router.get("/api/{cid}/todo")
def todo(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    """La guía completa (todas las personas), para quien organiza."""
    c = _control(db, cid)
    return {"grupos": svc.grupos_de(c, None, solo_pendientes=False), "busqueda": svc.resumen(c, u)}


class CodigoIn(BaseModel):
    codigo: str


@router.post("/api/{cid}/encontre")
def encontre(cid: int, body: CodigoIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    c = _control(db, cid)
    _abierta(c)
    cod = body.codigo.strip()
    if not cod:
        raise HTTPException(400, "Código vacío")
    r = svc.escanear_busqueda(db, c, u, cod)
    if not r["ok"]:
        registrar(db, u, "ecommerce_error", f"RE-{c.id}: {r.get('escaneado') or cod} ({r['motivo']})", "ecommerce")
    return {**r, "busqueda": svc.resumen(c, u)}


class ItemsIn(BaseModel):
    item_ids: list[int]


def _lineas(db: Session, c: Control, ids: list[int]) -> list[ControlItem]:
    out = []
    for iid in ids[:50]:
        i = db.get(ControlItem, iid)
        if i and i.paquete.control_id == c.id:
            out.append(i)
    return out


@router.post("/api/{cid}/la-tengo")
def la_tengo(cid: int, body: ItemsIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    """+1 a mano. Solo para prendas sin vincular al catálogo (las demás se escanean para verificarlas)."""
    c = _control(db, cid)
    _abierta(c)
    ls = [i for i in _lineas(db, c, body.item_ids) if svc.pendiente_busqueda(i) > 0]
    if not ls:
        raise HTTPException(400, "Ya están todas")
    if ls[0].articulo_id and not _organiza(db, u):
        raise HTTPException(400, "Escaneá la prenda: la app verifica que sea la correcta")
    svc.marcar_buscado(db, ls[0], u, +1)
    return {"ok": True}


@router.post("/api/{cid}/deshacer")
def deshacer(cid: int, body: ItemsIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    """−1: me equivoqué al escanear (la prenda vuelve a su lugar)."""
    c = _control(db, cid)
    _abierta(c)
    ls = [i for i in _lineas(db, c, body.item_ids) if i.buscado > 0]
    if not ls:
        raise HTTPException(400, "No hay nada para restar")
    svc.marcar_buscado(db, ls[-1], u, -1)
    return {"ok": True}


class FaltanteIn(BaseModel):
    item_ids: list[int]
    nota: str = ""


@router.post("/api/{cid}/faltante")
def faltante(cid: int, body: FaltanteIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    """No la encuentra: marca como faltante las unidades que quedan de esa prenda (queda una tarea)."""
    from ..services import control as csvc
    c = _control(db, cid)
    _abierta(c)
    n = 0
    for iid in body.item_ids[:50]:
        i = db.get(ControlItem, iid)
        if i and i.paquete.control_id == c.id and svc.pendiente_busqueda(i) > 0:
            csvc.marcar_faltante(db, i, u, body.nota.strip())
            n += 1
    registrar(db, u, "ecommerce_faltante", f"RE-{c.id}: {n} línea(s)", "ecommerce")
    return {"ok": True, "lineas": n}


@router.post("/api/{cid}/quitar-faltante")
def quitar_faltante(cid: int, body: FaltanteIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    from ..services import control as csvc
    c = _control(db, cid)
    _abierta(c)
    for iid in body.item_ids[:50]:
        i = db.get(ControlItem, iid)
        if i and i.paquete.control_id == c.id and i.faltante:
            csvc.quitar_faltante(db, i, u)
    svc.repartir(db, c)
    db.commit()
    return {"ok": True}


# ─────────────── cierre ───────────────

@router.post("/api/{cid}/cerrar")
def cerrar(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    """Cierra la búsqueda y guarda el Excel del resultado en la carpeta del día (si Drive está conectado)."""
    _exigir_organiza(db, u)
    from ..db import ahora
    from .control import excel
    c = _control(db, cid)
    c.estado, c.cerrado_at = "cerrado", ahora()
    db.commit()
    aviso = ""
    if re_drive.configurado(db):
        try:
            x = excel(cid, u, db)
            r = re_drive.guardar(db, c.fecha.isoformat(), f"RE-{c.id} {c.nombre.replace('/', '-')}.xlsx", x.body)
            c.drive_resultado = (r.get("url") or "")[:300]
            db.commit()
        except re_drive.REDriveError as e:
            aviso = f"Se cerró, pero no se pudo guardar el Excel en Drive: {e}"
    registrar(db, u, "ecommerce_cerrada", f"RE-{c.id} · {c.nombre}", "ecommerce")
    return {"ok": True, "busqueda": svc.resumen(c, u), "aviso": aviso}


@router.post("/api/{cid}/reabrir")
def reabrir(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    _exigir_organiza(db, u)
    c = _control(db, cid)
    c.estado, c.cerrado_at = "abierto", None
    db.commit()
    return {"ok": True, "busqueda": svc.resumen(c, u)}


@router.delete("/api/{cid}")
def borrar(cid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    from .control import borrar as borrar_control
    c = _control(db, cid)
    if not u.es_admin and c.usuario_id != u.id:
        raise HTTPException(403, "Solo quien la creó (o un admin) puede borrarla")
    return borrar_control(cid, u, db)
