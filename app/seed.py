"""Datos iniciales: admin y secciones base."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_pin
from .config import ADMIN_NOMBRE, ADMIN_PIN
from .models import Seccion, Usuario

SECCIONES_BASE = [
    dict(slug="despacho", nombre="Despacho", icono="🚚", orden=1, activa=True,
         roles="admin,despacho,deposito",
         descripcion="Flex, colecta y Tienda Nube: cargá el lote y escaneá los paquetes"),
    dict(slug="conteo", nombre="Conteo", icono="🔢", orden=3, activa=True,
         roles="admin,deposito",
         descripcion="Elegí el artículo, cargá la cantidad de cada color y talle, y descargá el Excel del conteo"),
    dict(slug="tareas", nombre="Tareas", icono="✅", orden=7, activa=True,
         roles="admin,despacho,deposito",
         descripcion="Tareas de todas las áreas: asignadas, pendientes y vencidas"),
    dict(slug="articulos", nombre="Artículos", icono="🏷️", orden=8, activa=True,
         roles="admin,despacho,deposito",
         descripcion="Buscá por código de barras, SKU o nombre"),
    dict(slug="panel", nombre="Panel PARKA", icono="📊", orden=9, activa=True, roles="admin",
         descripcion="Tu panel con el agente IA: preguntas, posventa, reclamos, descuentos (solo admin)"),
    dict(slug="asistente", nombre="Asistente", icono="🤖", orden=10, activa=True, roles="admin",
         descripcion="Preguntale por ventas, publicaciones, preguntas de ML, devoluciones, envíos y tareas (Gemini)"),
    dict(slug="ml", nombre="Mercado Libre", icono="🛒", orden=11, activa=True, roles="admin",
         descripcion="Ventas, preguntas sin responder, devoluciones y reputación (datos de ParkaHub, solo lectura)"),
    dict(slug="ecommerce", nombre="Recolección Ecommerce", icono="🛍️", orden=4, activa=True,
         roles="admin,despacho,deposito",
         descripcion="Ventas del día de ML y Tienda Nube: sumate a la búsqueda, juntá tu tramo y controlá cada paquete"),
    dict(slug="mapeo", nombre="Mapeo del depósito", icono="🗺️", orden=6, activa=True,
         roles="admin,deposito",
         descripcion="Ubicaciones (pasillo, módulo, nivel), qué hay en cada una, mapa y etiquetas"),
    dict(slug="devoluciones", nombre="Devoluciones", icono="↩️", orden=2, activa=True,
         roles="admin,deposito",
         descripcion="Buscá el artículo, elegí color, talle y cantidad, y confirmá: queda en Drive y salen las etiquetas"),
]


def cargar_catalogo_inicial(db: Session) -> None:
    """Si no hay artículos, carga el catálogo incluido (app/data/articulos.csv)."""
    import csv
    from pathlib import Path
    from .models import Articulo
    if db.scalar(select(Articulo.id).limit(1)):
        return
    f = Path(__file__).parent / "data" / "articulos.csv"
    if not f.exists():
        return
    with f.open(encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))
    db.bulk_insert_mappings(Articulo, [{k: (r.get(k) or "") for k in
                                        ("codigo_barras", "sku", "nombre", "articulo", "variante", "color", "talle")}
                                       for r in filas])
    db.commit()


def sembrar(db: Session) -> None:
    # Versiones anteriores: la sección "Depósito / stock" pasa a ser "Devoluciones"
    vieja = db.scalar(select(Seccion).where(Seccion.slug == "deposito"))
    if vieja and not db.scalar(select(Seccion).where(Seccion.slug == "devoluciones")):
        vieja.slug, vieja.nombre, vieja.icono, vieja.activa, vieja.orden = "devoluciones", "Devoluciones", "↩️", True, 2
        vieja.descripcion = next(d["descripcion"] for d in SECCIONES_BASE if d["slug"] == "devoluciones")
        for us in db.scalars(select(Usuario).where(Usuario.accesos.is_not(None))).all():
            us.accesos = ",".join("devoluciones" if a == "deposito" else a for a in us.accesos.split(","))
        db.flush()
    for datos in SECCIONES_BASE:
        if not db.scalar(select(Seccion).where(Seccion.slug == datos["slug"])):
            db.add(Seccion(**datos))
    # Recolección y Control de paquetes se unieron en "Recolección Ecommerce" (v2026.10.06-2)
    viejas = {"recoleccion", "control"}
    for us in db.scalars(select(Usuario).where(Usuario.accesos.is_not(None))).all():
        acc = [a for a in us.accesos.split(",") if a]
        if viejas & set(acc):
            nuevos = [a for a in acc if a not in viejas]
            if "ecommerce" not in nuevos:
                nuevos.append("ecommerce")
            us.accesos = ",".join(nuevos)
    from .models import Tarea
    for t in db.scalars(select(Tarea).where(Tarea.seccion_slug.in_(viejas))).all():
        t.seccion_slug = "ecommerce"
    for vieja in db.scalars(select(Seccion).where(Seccion.slug.in_(viejas))).all():
        db.delete(vieja)
    # SmartPost ya está dentro de Despacho (scanner): se quita la sección aparte
    for vieja in db.scalars(select(Seccion).where(Seccion.slug == "smartpost")).all():
        db.delete(vieja)
    orden = {"despacho": 1, "devoluciones": 2, "conteo": 3, "ecommerce": 4, "mapeo": 6,
             "tareas": 7, "articulos": 8, "panel": 9, "asistente": 10, "ml": 11}
    for sec in db.scalars(select(Seccion)).all():  # versiones anteriores: reordenar
        if sec.slug in orden and sec.orden in range(1, 12) and sec.orden != orden[sec.slug]:
            sec.orden = orden[sec.slug]
    if not db.scalar(select(Usuario).where(Usuario.rol == "admin")):
        db.add(Usuario(nombre=ADMIN_NOMBRE, rol="admin", pin_hash=hash_pin(ADMIN_PIN)))
    db.commit()
    cargar_catalogo_inicial(db)
