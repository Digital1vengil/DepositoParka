"""Recolección Ecommerce: las ventas del día de ML (Flex / Colecta) y Tienda Nube.

1. El admin (o quien tenga Supervisión) crea la búsqueda con los archivos de la carpeta del día en Drive.
2. Los operarios se suman. La app corta la ruta del depósito en tramos con una cantidad pareja de
   unidades: cada uno busca solo lo suyo (si alguien se suma o se va, se reparte de nuevo lo que falta).
3. Al buscar, cada prenda se escanea: la app la compara con lo que le toca (pantalla verde / roja).
4. En la mesa de control se escanea la etiqueta del envío y las prendas (misma lógica que Control de paquetes).

Se guarda sobre las tablas de Control de paquetes (Control.modo = "ecommerce")."""
from __future__ import annotations

import re
from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import ahora, hoy
from ..models import Articulo, Control, ControlItem, ControlPaquete, ControlParticipante, Usuario
from . import control as csvc

MODO = "ecommerce"
NOMBRES_TIPO = {"flex": "Flex", "colecta": "Colecta", "tiendanube": "Tienda Nube", "ml": "Mercado Libre",
                "full": "Full"}


# ─────────────────────────── crear ───────────────────────────

def crear(db: Session, u: Usuario, archivos: list[tuple[str, bytes]], nombre: str = "",
          origen: str = "drive") -> tuple[Control, list[str]]:
    paquetes, errores = csvc.leer_archivos(archivos, solo_pendientes=True)
    if not paquetes:
        raise ValueError(" · ".join(errores) or "No hay ventas para buscar en esos archivos")
    tipos = sorted({p.get("tipo") or "" for p in paquetes} - {""})
    if not nombre.strip():
        nombre = " + ".join(NOMBRES_TIPO.get(t, t) for t in tipos) or "Ventas"
        nombre = f"{nombre} {hoy().strftime('%d/%m')}"
    c = Control(fecha=hoy(), usuario_id=u.id, origen=origen, modo=MODO, nombre=nombre.strip()[:200],
                tipo=(tipos[0] if len(tipos) == 1 else "mixto")[:20],
                archivos=", ".join(n for n, _ in archivos)[:2000])
    db.add(c)
    csvc.agregar_paquetes(db, c, paquetes)
    db.flush()
    repartir(db, c)
    db.commit()
    return c, errores


def sumar_archivos(db: Session, c: Control, archivos: list[tuple[str, bytes]]) -> tuple[int, int, list[str]]:
    paquetes, errores = csvc.leer_archivos(archivos, solo_pendientes=True)
    nuevos, act = csvc.agregar_paquetes(db, c, paquetes)
    c.archivos = (((c.archivos + ", ") if c.archivos else "") + ", ".join(n for n, _ in archivos))[:2000]
    db.flush()
    repartir(db, c)
    db.commit()
    return nuevos, act, errores


# ─────────────────────────── equipo ───────────────────────────

def activos(c: Control) -> list[ControlParticipante]:
    return [p for p in c.participantes if p.activo]


def participante(c: Control, u: Usuario) -> ControlParticipante | None:
    return next((p for p in c.participantes if p.usuario_id == u.id), None)


def sumarse(db: Session, c: Control, u: Usuario) -> None:
    p = participante(c, u)
    if p and p.activo:
        return
    if p:
        p.activo, p.unido_at, p.salio_at = True, ahora(), None
    else:
        c.participantes.append(ControlParticipante(usuario_id=u.id))
    db.flush()
    repartir(db, c)
    db.commit()


def salir(db: Session, c: Control, usuario_id: int) -> None:
    p = next((x for x in c.participantes if x.usuario_id == usuario_id), None)
    if not p or not p.activo:
        return
    p.activo, p.salio_at = False, ahora()
    db.flush()
    repartir(db, c)
    db.commit()


# ─────────────────────────── reparto ───────────────────────────

def _items(c: Control) -> list[ControlItem]:
    return [i for p in c.paquetes for i in p.items]


def pendiente_busqueda(i: ControlItem) -> int:
    """Unidades que todavía hay que traer de esa línea."""
    if i.faltante:
        return 0
    return max(0, i.cantidad - i.buscado)


def clave_grupo(i: ControlItem) -> str:
    """La misma prenda (mismo artículo o, sin vincular, mismo SKU) en varios paquetes se busca junta."""
    if i.articulo_id:
        return f"a{i.articulo_id}"
    return "s" + re.sub(r"[^A-Z0-9]", "_", (i.sku or i.producto or str(i.id)).upper())[:80]


def _orden(i: ControlItem) -> tuple:
    return (i.orden_ruta, (i.ubicacion or "").split(",")[0], i.articulo or "~", i.color, i.talle, i.sku, i.id)


def repartir(db: Session, c: Control) -> None:
    """Corta la ruta en tramos seguidos con unidades parejas, uno por operario activo (en el orden en que se
    sumaron). Lo que alguien ya empezó a traer sigue siendo suyo mientras esté activo. Sin operarios, nada
    queda asignado."""
    gente = [p.usuario_id for p in activos(c)]
    items = [i for i in _items(c) if pendiente_busqueda(i) > 0]
    if not gente:
        for i in items:
            i.asignado_id = None
        return
    fijos, libres = [], []
    for i in items:
        (fijos if i.buscado > 0 and i.asignado_id in gente else libres).append(i)
    # grupos (misma prenda) en orden de ruta: un grupo no se parte entre dos personas
    grupos: "OrderedDict[str, list[ControlItem]]" = OrderedDict()
    for i in sorted(libres, key=_orden):
        grupos.setdefault(clave_grupo(i), []).append(i)
    carga = {g: 0 for g in gente}
    for i in fijos:
        carga[i.asignado_id] += pendiente_busqueda(i)
    total = sum(carga.values()) + sum(pendiente_busqueda(i) for i in libres)
    objetivo = total / len(gente)
    k = 0
    # recorrido: se llena a cada uno hasta su parte justa y se pasa al siguiente
    for lista in grupos.values():
        unidades = sum(pendiente_busqueda(i) for i in lista)
        while k < len(gente) - 1 and carga[gente[k]] >= objetivo:
            k += 1
        # si este grupo la pasa mucho de su parte y el siguiente está más vacío, va al siguiente
        if (k < len(gente) - 1 and carga[gente[k]] > 0
                and carga[gente[k]] + unidades - objetivo > objetivo - carga[gente[k]]):
            k += 1
        for i in lista:
            i.asignado_id = gente[k]
        carga[gente[k]] += unidades


# ─────────────────────────── búsqueda (operario) ───────────────────────────

def grupos_de(c: Control, usuario_id: int | None = None, solo_pendientes: bool = True) -> list[dict]:
    """La lista de búsqueda agrupada por prenda y ordenada por la ruta. usuario_id=None: de todos."""
    out: "OrderedDict[str, dict]" = OrderedDict()
    for i in sorted(_items(c), key=_orden):
        if usuario_id is not None and i.asignado_id != usuario_id:
            continue
        if solo_pendientes and pendiente_busqueda(i) == 0 and not i.faltante:
            continue
        g = out.setdefault(clave_grupo(i), {
            "clave": clave_grupo(i), "item_ids": [], "articulo_id": i.articulo_id, "articulo": i.articulo,
            "color": i.color, "talle": i.talle, "codigo_barras": i.codigo_barras, "sku": i.sku,
            "producto": i.producto, "variante": i.variante, "ubicacion": i.ubicacion, "vinculado": bool(i.articulo_id),
            "cantidad": 0, "buscado": 0, "faltan": 0, "faltante": 0, "paquetes": [],
            "asignado": i.asignado.nombre if i.asignado else ""})
        g["item_ids"].append(i.id)
        g["cantidad"] += i.cantidad
        g["buscado"] += min(i.buscado, i.cantidad)
        g["faltan"] += pendiente_busqueda(i)
        if i.faltante:
            g["faltante"] += i.cantidad - min(i.buscado, i.cantidad)
        tipo = NOMBRES_TIPO.get(i.paquete.tipo, i.paquete.tipo)
        g["paquetes"].append(f"{tipo + ' ' if tipo else ''}{i.paquete.tracking}")
    return list(out.values())


def progreso(c: Control) -> dict:
    """Cuánto falta buscar, en total y por operario."""
    items = _items(c)
    por: "OrderedDict[int, dict]" = OrderedDict()
    for p in c.participantes:
        por[p.usuario_id] = {"usuario_id": p.usuario_id, "nombre": p.usuario.nombre if p.usuario else "?",
                             "activo": p.activo, "unidades": 0, "buscadas": 0, "faltantes": 0,
                             "unido": p.unido_at.strftime("%H:%M") if p.unido_at else ""}
    sin_asignar = 0
    for i in items:
        falta = pendiente_busqueda(i)
        if i.asignado_id in por:
            d = por[i.asignado_id]
            d["unidades"] += i.cantidad
            d["buscadas"] += min(i.buscado, i.cantidad)
            d["faltantes"] += (i.cantidad - min(i.buscado, i.cantidad)) if i.faltante else 0
        elif falta:
            sin_asignar += falta
    total = sum(i.cantidad for i in items)
    buscadas = sum(min(i.buscado, i.cantidad) for i in items)
    return {"unidades": total, "buscadas": buscadas, "faltan": sum(pendiente_busqueda(i) for i in items),
            "faltantes": sum(i.cantidad - min(i.buscado, i.cantidad) for i in items if i.faltante),
            "sin_asignar": sin_asignar, "equipo": list(por.values())}


def resumen(c: Control, u: Usuario | None = None) -> dict:
    r = csvc.resumen(c)
    pr = progreso(c)
    tipos: dict[str, int] = {}
    for p in c.paquetes:
        t = NOMBRES_TIPO.get(p.tipo, p.tipo or "Otro")
        tipos[t] = tipos.get(t, 0) + 1
    yo = participante(c, u) if u else None
    return {**r, "fecha": c.fecha.isoformat(), "busqueda": pr, "tipos": tipos, "drive_resultado": c.drive_resultado,
            "me_sume": bool(yo and yo.activo)}


def marcar_buscado(db: Session, i: ControlItem, u: Usuario, delta: int) -> None:
    i.buscado = max(0, min(i.cantidad, i.buscado + delta))
    i.buscado_at = ahora()
    if delta > 0 and i.asignado_id is None:
        i.asignado_id = u.id
    db.commit()


def escanear_busqueda(db: Session, c: Control, u: Usuario, codigo: str) -> dict:
    """El operario escanea la prenda que encontró: ¿es una de las que le tocan?"""
    art = csvc.articulo_de_codigo(db, codigo)
    if not art:
        return {"ok": False, "motivo": "desconocido",
                "mensaje": f"El código {codigo} no está en el catálogo de artículos. Dejala donde estaba."}
    nombre = csvc.nombre_art(art)
    lineas = [i for i in sorted(_items(c), key=_orden)
              if i.articulo_id and art.id in csvc._ids(i.aceptados)]
    mias = [i for i in lineas if i.asignado_id == u.id and pendiente_busqueda(i) > 0]
    if mias:
        i = mias[0]
        marcar_buscado(db, i, u, +1)
        g = next((x for x in grupos_de(c, u.id, solo_pendientes=False) if i.id in x["item_ids"]), None)
        return {"ok": True, "escaneado": nombre, "item_id": i.id, "grupo": g,
                "mensaje": f"OK · {nombre}" + (f" · faltan {g['faltan']}" if g and g["faltan"] else "")}
    libres = [i for i in lineas if pendiente_busqueda(i) > 0]
    if libres:
        quien = libres[0].asignado.nombre if libres[0].asignado else "nadie"
        return {"ok": False, "motivo": "otro", "escaneado": nombre,
                "mensaje": f"{nombre} no está en tu lista: la busca {quien}. Dejala donde estaba."}
    if lineas:
        return {"ok": False, "motivo": "sobra", "escaneado": nombre,
                "mensaje": f"Ya se juntaron todas las unidades de {nombre}. Esta sobra: dejala donde estaba."}
    # ¿Es la misma prenda en otra marca (No Brand) o en otro talle/color?
    return {"ok": False, "motivo": "equivocado", "escaneado": nombre,
            "mensaje": f"{nombre} no está en las ventas de esta búsqueda. Revisá talle, color y marca (tiene que ser Parka)."}


def articulo_json(a: Articulo) -> dict:
    return {"id": a.id, "nombre": csvc.nombre_art(a), "codigo_barras": a.codigo_barras, "sku": a.sku}


def lista(db: Session, limite: int = 30) -> list[Control]:
    return list(db.scalars(select(Control).where(Control.modo == MODO)
                           .order_by(Control.id.desc()).limit(limite)).all())


def paquetes_pendientes_hoy(db: Session) -> int:
    from sqlalchemy import func
    return db.scalar(select(func.count()).select_from(ControlPaquete).join(Control).where(
        Control.fecha == hoy(), Control.modo == MODO,
        ControlPaquete.estado.in_(("pendiente", "en_curso", "faltante")))) or 0
