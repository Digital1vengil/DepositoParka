import io
import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, HTTPException
from pathlib import Path

from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse, RedirectResponse
from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..actividad import registrar
from ..auth import requiere_seccion, SinPermiso, puede
from ..db import get_db, hoy, ahora
from ..models import Lote, Paquete, Tanda, Usuario, TIPOS_LOTE
from ..parsers import leer_archivo
from ..services import despacho as svc, drive, parkahub
from ..services.tareas import tareas_de_seccion, tarea_json
from ..templating import render

router = APIRouter(prefix="/despacho")
acceso = requiere_seccion("despacho")
MAX_BYTES = 15 * 1024 * 1024


async def _leer(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    out = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_BYTES:
            raise HTTPException(400, f"{f.filename}: archivo demasiado grande")
        out.append((f.filename or "archivo", data))
    return out


# ─────────────── Scanner (igual que la app PARKA Despacho) ───────────────
# Hasta conectar la API de Mercado Libre, Despacho funciona exactamente como el scanner:
# se sirve la misma app (app/scanner/index.html) y habla directo con el Apps Script de Drive.
SCANNER_DIR = Path(__file__).resolve().parent.parent / "scanner"
SCANNER_ARCHIVOS = {"styles.css", "manifest.json", "icon-192.png", "icon-512.png", "tema-parka.css", "ml-semaforo.js", "control-despacho.js"}
# Colores del scanner original (verde) → paleta del Panel PARKA (violeta / turquesa / celeste)
_COLORES = {"#2f5d3a": "#3b1f6b", "#43592d": "#3b1f6b", "#547038": "#5b3a99", "#6a8c47": "#0d9488",
            "#374a25": "#2a1650", "#263524": "#24163f", "#f4f7f0": "#eef6fd"}


def _scanner_html() -> str:
    html = (SCANNER_DIR / "index.html").read_text(encoding="utf-8")
    for viejo, nuevo in _COLORES.items():
        html = html.replace(viejo, nuevo).replace(viejo.upper(), nuevo)
    tema = '<link rel="stylesheet" href="tema-parka.css">\n<script src="ml-semaforo.js"></script>\n<script src="control-despacho.js"></script>'
    return html.replace("</body>", tema + "\n</body>", 1) if "</body>" in html else html + tema


@router.get("")
def pagina(request: Request, u: Usuario = Depends(acceso)):
    return render(request, "despacho_scanner.html", u=u, ml_ok=parkahub.configurado())


@router.get("/scanner/")
def scanner_raiz(u: Usuario = Depends(acceso)):
    return HTMLResponse(_scanner_html(), headers={"Cache-Control": "no-cache"})


@router.get("/scanner/index.html")
def scanner_index(u: Usuario = Depends(acceso)):
    return HTMLResponse(_scanner_html(), headers={"Cache-Control": "no-cache"})


@router.get("/scanner/{archivo}")
def scanner_archivo(archivo: str, u: Usuario = Depends(acceso)):
    if archivo not in SCANNER_ARCHIVOS or not (SCANNER_DIR / archivo).exists():
        raise HTTPException(404)
    return FileResponse(SCANNER_DIR / archivo, headers={"Cache-Control": "no-cache"})


@router.get("/lotes")
def pagina_lotes(u: Usuario = Depends(acceso)):
    """El Despacho nuevo (beta) se quitó: Despacho funciona con el scanner hasta conectar la API de ML."""
    return RedirectResponse("/despacho", status_code=303)


@router.get("/escanear")
def escanear_pagina(request: Request, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return render(request, "escanear.html", u=u)


def _preview(archivos: list[tuple[str, bytes]], tipo: str, mp: dict | None) -> dict:
    resultados = []
    for nombre, data in archivos:
        try:
            r = leer_archivo(nombre, data, tipo, mp)
            resultados.append({"archivo": nombre, "formato": r["formato"], "headers": r["headers"],
                               "mapeo": r["mapeo"], "muestra": r["muestra"],
                               "cantidad": len(r["paquetes"]), "paquetes": r["paquetes"][:8]})
        except ValueError as e:
            resultados.append({"archivo": nombre, "error": str(e)})
    return {"resultados": resultados}


def _crear(db: Session, u: Usuario, archivos: list[tuple[str, bytes]], tipo: str, nombre: str,
           mp: dict | None, drive_ids: list[str] | None = None) -> dict:
    paquetes, nombres, errores = [], [], []
    for nom, data in archivos:
        try:
            r = leer_archivo(nom, data, tipo, mp)
        except ValueError as e:
            errores.append(f"{nom}: {e}")
            continue
        if not r["paquetes"]:
            errores.append(f"{nom}: no se encontraron paquetes (revisá la columna del número de envío)")
            continue
        paquetes.extend(r["paquetes"])
        nombres.append(nom)
    if not paquetes:
        raise HTTPException(400, " · ".join(errores) or "No se encontraron paquetes")
    vistos, unicos = set(), []
    for p in paquetes:
        if p["tracking"] not in vistos:
            vistos.add(p["tracking"])
            unicos.append(p)
    nombre_lote = nombre.strip() or (nombres[0] + (f" +{len(nombres)-1}" if len(nombres) > 1 else ""))
    lote = svc.crear_lote(db, u, tipo, nombre_lote, unicos, drive_ids)
    return {"ok": True, "lote_id": lote.id, "cantidad": len(unicos), "errores": errores}


@router.post("/api/preview")
async def preview(tipo: str = Form("flex"), mapeo: str = Form(""), archivos: list[UploadFile] = File(...),
                  u: Usuario = Depends(acceso)):
    return _preview(await _leer(archivos), tipo, json.loads(mapeo) if mapeo else None)


@router.post("/api/lotes")
async def crear_lotes(tipo: str = Form("flex"), nombre: str = Form(""), mapeo: str = Form(""),
                      archivos: list[UploadFile] = File(...), u: Usuario = Depends(acceso),
                      db: Session = Depends(get_db)):
    return _crear(db, u, await _leer(archivos), tipo, nombre, json.loads(mapeo) if mapeo else None)


# ─────────────── Google Drive (carpeta PARKA Despacho del scanner) ───────────────
def _drive_error(e: Exception):
    raise HTTPException(502, str(e))


@router.get("/api/drive/archivos")
def drive_archivos(tipo: str = "flex", u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    try:
        d = drive.listar(tipo)
    except drive.DriveError as e:
        _drive_error(e)
    usados = set()
    for (ids,) in db.execute(select(Lote.drive_ids).where(Lote.drive_ids != "")).all():
        usados.update(i for i in ids.split(",") if i)
    grupos = d.get("groups") or []
    for g in grupos:
        for f in g.get("files", []):
            f["cargado"] = f["id"] in usados
    return {"carpeta": d.get("rootFolderUrl", ""), "grupos": grupos[:15], "hoy": hoy().isoformat()}


class DriveSel(BaseModel):
    ids: list[str]
    tipo: str = "flex"
    nombre: str = ""
    mapeo: dict | None = None


def _bajar(ids: list[str]) -> list[tuple[str, bytes]]:
    if not ids:
        raise HTTPException(400, "Elegí al menos un archivo")
    try:
        return [drive.obtener_archivo(i) for i in ids[:20]]
    except drive.DriveError as e:
        _drive_error(e)


@router.post("/api/drive/preview")
def drive_preview(body: DriveSel, u: Usuario = Depends(acceso)):
    return _preview(_bajar(body.ids), body.tipo, body.mapeo)


@router.post("/api/drive/lotes")
def drive_crear(body: DriveSel, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return _crear(db, u, _bajar(body.ids), body.tipo, body.nombre, body.mapeo, body.ids)


@router.post("/api/drive/informar")
def drive_informar(u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    """Sube a Drive los despachados que falten (por si falló algún 'Finalizar despacho')."""
    try:
        return {"ok": True, "resultado": drive.enviar_despachados(db, _paquetes_abiertos_y_hoy(db), u)}
    except drive.DriveError as e:
        _drive_error(e)


def _paquetes_abiertos_y_hoy(db: Session) -> list[Paquete]:
    from sqlalchemy import or_
    lotes = db.scalars(select(Lote).where(or_(Lote.fecha == hoy(), Lote.cerrado.is_(False)))).all()
    return [p for l in lotes for p in l.paquetes]


@router.get("/api/estado")
def estado(u: Usuario = Depends(acceso), db: Session = Depends(get_db), lote: int | None = None):
    lotes = svc.lotes_abiertos(db)
    # también mostrar lotes cerrados hoy
    cerrados_hoy = db.scalars(select(Lote).where(Lote.fecha == hoy(), Lote.cerrado.is_(True))).all()
    lj = []
    for l in list(lotes) + list(cerrados_hoy):
        h, t = svc.progreso_lote(db, l.id)
        lj.append({"id": l.id, "tipo": l.tipo, "tipo_nombre": TIPOS_LOTE.get(l.tipo, l.tipo), "nombre": l.nombre,
                   "fecha": l.fecha.strftime("%d/%m"), "hoy": l.fecha == hoy(), "cerrado": l.cerrado,
                   "hechos": h, "total": t,
                   "creado_por": l.creado_por.nombre if l.creado_por else "",
                   "hora": l.creado_at.strftime("%H:%M")})
    ids = [l.id for l in lotes] if lote is None else [lote]
    paquetes = []
    if ids:
        paquetes = [svc.paquete_json(p) for p in db.scalars(
            select(Paquete).where(Paquete.lote_id.in_(ids)).order_by(Paquete.escaneado_at.desc().nulls_first(),
                                                                    Paquete.id)).all()]
    t = svc.tanda_activa(db, u)
    tanda = None
    if t:
        tanda = {"id": t.id, "inicio": t.inicio.strftime("%H:%M"), "cantidad": len(t.paquetes)}
    tareas = [tarea_json(x) for x in tareas_de_seccion(db, "despacho")]
    sin_drive = sum(1 for p in _paquetes_abiertos_y_hoy(db) if p.estado == "despachado" and not p.drive_enviado)
    return {"lotes": lj, "paquetes": paquetes, "tanda": tanda, "tareas": tareas, "usuario": u.nombre,
            "es_admin": u.es_admin, "drive": drive.configurado(), "sin_drive": sin_drive}


class Escaneo(BaseModel):
    codigo: str


@router.post("/api/scan")
def scan(body: Escaneo, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    return svc.escanear(db, u, body.codigo)


class Marcar(BaseModel):
    despachado: bool


@router.post("/api/paquetes/{pid}/marcar")
def marcar(pid: int, body: Marcar, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    p = db.get(Paquete, pid)
    if not p:
        raise HTTPException(404, "Paquete inexistente")
    if not body.despachado and not u.es_admin and p.escaneado_por_id != u.id:
        raise SinPermiso()
    svc.marcar_manual(db, u, p, body.despachado)
    return {"ok": True, "paquete": svc.paquete_json(p)}


@router.post("/api/tanda/iniciar")
def tanda_iniciar(u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    t = svc.tanda_activa(db, u)
    if not t:
        t = Tanda(usuario_id=u.id)
        db.add(t)
        registrar(db, u, "tanda_inicio", "", "despacho", commit=False)
        db.commit()
    return {"ok": True, "id": t.id}


@router.post("/api/tanda/finalizar")
def tanda_finalizar(u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    t = svc.tanda_activa(db, u)
    if not t:
        raise HTTPException(400, "No hay un despacho iniciado")
    t.fin = ahora()
    registrar(db, u, "tanda_fin", f"{len(t.paquetes)} paquetes", "despacho", commit=False)
    db.commit()
    res = {"ok": True, "id": t.id, "cantidad": len(t.paquetes), "drive": None, "drive_error": ""}
    if drive.configurado() and t.paquetes:
        try:
            res["drive"] = drive.enviar_despachados(db, list(t.paquetes), u)
        except drive.DriveError as e:
            res["drive_error"] = str(e)
            registrar(db, u, "drive_error", str(e)[:300], "despacho")
    return res


def _xlsx(filas: list[dict], nombre: str) -> StreamingResponse:
    wb = Workbook()
    ws = wb.active
    ws.title = "Despacho"
    if filas:
        ws.append(list(filas[0].keys()))
        for f in filas:
            ws.append(list(f.values()))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/tanda/{tid}.xlsx")
def tanda_excel(tid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    t = db.get(Tanda, tid)
    if not t or (t.usuario_id != u.id and not u.es_admin):
        raise HTTPException(404)
    filas = [{"Paquetes Despachados": i + 1, "Numero de Etiqueta": p.tracking, "Nombre de la Persona": p.comprador,
              "Cantidad de Prendas": p.cantidad, "SKU Despachados": p.sku or p.producto}
             for i, p in enumerate(sorted(t.paquetes, key=lambda p: p.escaneado_at or ahora()))]
    return _xlsx(filas, f"Despacho_{t.inicio.strftime('%Y-%m-%d_%H-%M')}.xlsx")


@router.get("/lotes/{lid}.xlsx")
def lote_excel(lid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    l = db.get(Lote, lid)
    if not l:
        raise HTTPException(404)
    filas = [{"Tracking": p.tracking, "Venta": p.venta_id, "Comprador": p.comprador, "Producto": p.producto,
              "SKU": p.sku, "Cantidad": p.cantidad, "Estado": "Despachado" if p.estado == "despachado" else "Pendiente",
              "Hora": p.escaneado_at.strftime("%d/%m %H:%M") if p.escaneado_at else "",
              "Operario": p.escaneado_por.nombre if p.escaneado_por else ""} for p in l.paquetes]
    return _xlsx(filas, f"lote_{l.id}_{l.tipo}_{l.fecha.isoformat()}.xlsx")


@router.post("/api/lotes/{lid}/cerrar")
def cerrar_lote(lid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    l = db.get(Lote, lid)
    if not l:
        raise HTTPException(404)
    l.cerrado = True
    h, t = svc.progreso_lote(db, l.id)
    registrar(db, u, "lote_cerrado", f"{l.nombre} · {h}/{t} despachados", "despacho", commit=False)
    db.commit()
    return {"ok": True}


@router.post("/api/lotes/{lid}/reabrir")
def reabrir_lote(lid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    l = db.get(Lote, lid)
    if not l:
        raise HTTPException(404)
    l.cerrado = False
    db.commit()
    return {"ok": True}


@router.post("/api/lotes/{lid}/borrar")
def borrar_lote(lid: int, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    if not u.es_admin:
        raise SinPermiso()
    l = db.get(Lote, lid)
    if not l:
        raise HTTPException(404)
    from ..models import Tarea
    for t in db.scalars(select(Tarea).where(Tarea.ref == f"lote:{l.id}")).all():
        db.delete(t)
    registrar(db, u, "lote_borrado", l.nombre, "despacho", commit=False)
    db.delete(l)
    db.commit()
    return {"ok": True}


# ─────────────── Mercado Libre vía ParkaHub: semáforo por escaneo y cotejo ───────────────
@router.get("/api/ml/estado")
def ml_estado(u: Usuario = Depends(acceso)):
    return {"conectado": parkahub.configurado()}


@router.get("/api/ml/verificar")
def ml_verificar(codigo: str, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    """Lo llama el scanner después de cada lectura (Flex/Colecta). Si ParkaHub no está, el scanner sigue igual."""
    if not parkahub.configurado():
        return {"ok": False, "sin_conexion": True}
    try:
        v = parkahub.verificar_envio(codigo)
    except parkahub.ParkaHubError as e:
        return {"ok": False, "error": str(e)}
    if v["veredicto"] in ("red", "yellow"):
        registrar(db, u, "escaneo_frenado_ml", f"{v['codigo']} · {v['mensaje']} · {v.get('motivo') or ''}", "despacho")
    return {"ok": True, **v}


class CotejoIn(BaseModel):
    tipo: str
    paquetes: list[dict]
    refrescar: bool = False


@router.post("/api/ml/cotejo")
def ml_cotejo(body: CotejoIn, u: Usuario = Depends(acceso), db: Session = Depends(get_db)):
    try:
        d = parkahub.listos_ml(body.tipo, body.refrescar)
    except parkahub.ParkaHubError as e:
        raise HTTPException(400, str(e)) from None
    r = parkahub.cotejar(body.paquetes[:3000], d.get("ready") or [], d.get("states") or {})
    r["frescura"] = d.get("fresh") or {}
    registrar(db, u, "cotejo_ml", f"{body.tipo} · coinciden {r['coinciden']} · faltan {len(r['solo_ml'])} · sobran {len(r['solo_lista'])}", "despacho")
    return r
