"""Modelos de datos de PARKA Depósito."""
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, ahora

ROLES = {
    "admin": "Admin",
    "despacho": "Despacho",
    "deposito": "Depósito",
}

TIPOS_LOTE = {
    "flex": "Flex",
    "colecta": "Colecta",
    "tiendanube": "Tienda Nube",
    "otro": "Otro",
}

ESTADOS_TAREA = {
    "pendiente": "Pendiente",
    "en_curso": "En curso",
    "hecha": "Hecha",
}


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60), unique=True)
    rol: Mapped[str] = mapped_column(String(20), default="despacho")
    pin_hash: Mapped[str] = mapped_column(String(200))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0)
    bloqueado_hasta: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ultimo_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    # Accesos personalizados (secciones y funciones separadas por coma). Vacío/None = los del rol.
    accesos: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def es_admin(self) -> bool:
        return self.rol == "admin"


class Seccion(Base):
    """Área de trabajo (Despacho, Depósito…). Cada rol ve las secciones que tiene permitidas."""
    __tablename__ = "secciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(30), unique=True)
    nombre: Mapped[str] = mapped_column(String(60))
    icono: Mapped[str] = mapped_column(String(10), default="📦")
    descripcion: Mapped[str] = mapped_column(String(200), default="")
    orden: Mapped[int] = mapped_column(Integer, default=0)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    roles: Mapped[str] = mapped_column(String(200), default="admin")  # lista separada por comas

    def permite(self, rol: str) -> bool:
        return rol == "admin" or rol in [r.strip() for r in self.roles.split(",")]


class Lote(Base):
    """Un archivo de despacho cargado (etiquetas ZPL o planilla) — flex, colecta o Tienda Nube."""
    __tablename__ = "lotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    tipo: Mapped[str] = mapped_column(String(20), default="flex")
    nombre: Mapped[str] = mapped_column(String(200))
    creado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    cerrado: Mapped[bool] = mapped_column(Boolean, default=False)
    drive_ids: Mapped[str] = mapped_column(Text, default="")  # ids de archivos de Drive usados (separados por coma)

    creado_por: Mapped[Optional[Usuario]] = relationship()
    paquetes: Mapped[list["Paquete"]] = relationship(back_populates="lote", cascade="all, delete-orphan")


class Tanda(Base):
    """Un período 'Iniciar despacho → Finalizar despacho' de un operario."""
    __tablename__ = "tandas"
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    inicio: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    fin: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    usuario: Mapped[Usuario] = relationship()
    paquetes: Mapped[list["Paquete"]] = relationship(back_populates="tanda")


class Paquete(Base):
    __tablename__ = "paquetes"
    id: Mapped[int] = mapped_column(primary_key=True)
    lote_id: Mapped[int] = mapped_column(ForeignKey("lotes.id"), index=True)
    tracking: Mapped[str] = mapped_column(String(60), index=True)
    venta_id: Mapped[str] = mapped_column(String(60), default="")
    comprador: Mapped[str] = mapped_column(String(120), default="")
    producto: Mapped[str] = mapped_column(Text, default="")
    sku: Mapped[str] = mapped_column(String(200), default="")
    cantidad: Mapped[int] = mapped_column(Integer, default=1)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    escaneado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    escaneado_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    codigo_raw: Mapped[str] = mapped_column(String(300), default="")
    tanda_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tandas.id"), nullable=True)
    tracking_alt: Mapped[str] = mapped_column(String(80), default="")  # TN: código de tracking del envío
    drive_enviado: Mapped[bool] = mapped_column(Boolean, default=False)  # ya registrado como despachado en Drive
    drive_pend_fecha: Mapped[Optional[date]] = mapped_column(Date, nullable=True)  # día en que se informó como pendiente

    lote: Mapped[Lote] = relationship(back_populates="paquetes")
    escaneado_por: Mapped[Optional[Usuario]] = relationship()
    tanda: Mapped[Optional[Tanda]] = relationship(back_populates="paquetes")


class Tarea(Base):
    __tablename__ = "tareas"
    id: Mapped[int] = mapped_column(primary_key=True)
    seccion_slug: Mapped[str] = mapped_column(String(30), index=True)
    titulo: Mapped[str] = mapped_column(String(200))
    detalle: Mapped[str] = mapped_column(Text, default="")
    origen: Mapped[str] = mapped_column(String(10), default="manual")  # auto | manual
    ref: Mapped[str] = mapped_column(String(60), default="", index=True)  # ej. "lote:12"
    prioridad: Mapped[str] = mapped_column(String(10), default="normal")  # normal | alta
    estado: Mapped[str] = mapped_column(String(20), default="pendiente", index=True)
    asignado_a_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creada_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    vence: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    hecha_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    hecha_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    asignado_a: Mapped[Optional[Usuario]] = relationship(foreign_keys=[asignado_a_id])
    creado_por: Mapped[Optional[Usuario]] = relationship(foreign_keys=[creado_por_id])
    hecha_por: Mapped[Optional[Usuario]] = relationship(foreign_keys=[hecha_por_id])

    def vencida(self, hoy_: date) -> bool:
        return self.estado != "hecha" and self.vence is not None and self.vence < hoy_


class Actividad(Base):
    """Registro de auditoría: quién hizo qué y cuándo."""
    __tablename__ = "actividad"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True, index=True)
    seccion_slug: Mapped[str] = mapped_column(String(30), default="")
    accion: Mapped[str] = mapped_column(String(40))
    detalle: Mapped[str] = mapped_column(Text, default="")

    usuario: Mapped[Optional[Usuario]] = relationship()


class Articulo(Base):
    """Catálogo de artículos: código de barras ↔ SKU. Se sincroniza desde Odoo (o del PDF de etiquetas)."""
    __tablename__ = "articulos"
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo_barras: Mapped[str] = mapped_column(String(20), default="", index=True)
    sku: Mapped[str] = mapped_column(String(80), default="", index=True)
    nombre: Mapped[str] = mapped_column(String(200), default="")
    articulo: Mapped[str] = mapped_column(String(120), default="", index=True)
    variante: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(40), default="")
    talle: Mapped[str] = mapped_column(String(20), default="")
    actualizado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    # Datos que llegan de la sincronización con Odoo (product.product)
    odoo_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    marca: Mapped[str] = mapped_column(String(40), default="")
    color_nombre: Mapped[str] = mapped_column(String(60), default="")
    categoria: Mapped[str] = mapped_column(String(80), default="")
    stock: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    meli_id: Mapped[str] = mapped_column(String(40), default="")
    inactivo: Mapped[bool] = mapped_column(Boolean, default=False)  # archivado en Odoo


class EnvioManual(Base):
    """SmartPost: envíos cargados a mano (como en el scanner). Se anotan en el LogDia del Apps Script."""
    __tablename__ = "envios_manuales"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    hora: Mapped[str] = mapped_column(String(5), default="")
    plataforma: Mapped[str] = mapped_column(String(2), default="ML")  # ML | TN
    nombre: Mapped[str] = mapped_column(String(120), default="")
    articulo: Mapped[str] = mapped_column(String(200), default="")
    numero: Mapped[str] = mapped_column(String(60), default="")
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    drive_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    drive_error: Mapped[str] = mapped_column(Text, default="")
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    usuario: Mapped[Optional[Usuario]] = relationship()


class Devolucion(Base):
    __tablename__ = "devoluciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    drive_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    drive_resp: Mapped[str] = mapped_column(Text, default="")  # respuesta del script (JSON) o error

    usuario: Mapped[Optional[Usuario]] = relationship()
    items: Mapped[list["DevolucionItem"]] = relationship(back_populates="devolucion", cascade="all, delete-orphan")


class DevolucionItem(Base):
    __tablename__ = "devolucion_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    devolucion_id: Mapped[int] = mapped_column(ForeignKey("devoluciones.id"), index=True)
    articulo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("articulos.id"), nullable=True)
    codigo_barras: Mapped[str] = mapped_column(String(20), default="")
    sku: Mapped[str] = mapped_column(String(80), default="")
    articulo: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(60), default="")
    talle: Mapped[str] = mapped_column(String(20), default="")
    estado: Mapped[str] = mapped_column(String(10), default="OPTIMO")  # OPTIMO | FALLA
    cantidad: Mapped[int] = mapped_column(Integer, default=1)

    devolucion: Mapped[Devolucion] = relationship(back_populates="items")


class Conteo(Base):
    __tablename__ = "conteos"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    nota: Mapped[str] = mapped_column(String(200), default="")  # ubicación / referencia opcional
    total: Mapped[int] = mapped_column(Integer, default=0)

    usuario: Mapped[Optional[Usuario]] = relationship()
    items: Mapped[list["ConteoItem"]] = relationship(back_populates="conteo", cascade="all, delete-orphan")


class ConteoItem(Base):
    __tablename__ = "conteo_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    conteo_id: Mapped[int] = mapped_column(ForeignKey("conteos.id"), index=True)
    articulo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("articulos.id"), nullable=True)
    codigo_barras: Mapped[str] = mapped_column(String(20), default="")
    sku: Mapped[str] = mapped_column(String(80), default="")
    nombre: Mapped[str] = mapped_column(String(200), default="")
    articulo: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(60), default="")
    talle: Mapped[str] = mapped_column(String(20), default="")
    marca: Mapped[str] = mapped_column(String(20), default="")
    cantidad: Mapped[int] = mapped_column(Integer, default=1)

    conteo: Mapped[Conteo] = relationship(back_populates="items")


class Cierre(Base):
    __tablename__ = "cierres"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    enviado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    destinatario: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    automatico: Mapped[bool] = mapped_column(Boolean, default=False)


Index("ix_paquete_estado_lote", Paquete.lote_id, Paquete.estado)


class Ajuste(Base):
    """Configuración guardada desde la app (conexiones a ParkaHub, Gemini). Pisa lo que diga el .env."""
    __tablename__ = "ajustes"
    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")
    actualizado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora, onupdate=ahora)


class ChatMensaje(Base):
    """Historial del Asistente (Gemini), uno por usuario."""
    __tablename__ = "chat_mensajes"
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    rol: Mapped[str] = mapped_column(String(10))  # user | model
    texto: Mapped[str] = mapped_column(Text, default="")
    herramientas: Mapped[str] = mapped_column(Text, default="")  # nombres de las consultas que usó (para mostrar)
    ts: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)


# ─────────────── Mapeo del depósito ───────────────
class Ubicacion(Base):
    """Lugar físico del depósito: zona / pasillo / módulo (estantería) / nivel. El código va en la etiqueta."""
    __tablename__ = "ubicaciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # ej. "A-03-2"
    zona: Mapped[str] = mapped_column(String(40), default="")
    pasillo: Mapped[str] = mapped_column(String(10), default="")
    modulo: Mapped[int] = mapped_column(Integer, default=0)
    nivel: Mapped[int] = mapped_column(Integer, default=0)
    orden: Mapped[int] = mapped_column(Integer, default=0, index=True)  # orden de la ruta de recolección
    descripcion: Mapped[str] = mapped_column(String(200), default="")
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    asignaciones: Mapped[list["UbicacionArticulo"]] = relationship(back_populates="ubicacion",
                                                                   cascade="all, delete-orphan")


class UbicacionArticulo(Base):
    """Qué hay en cada ubicación: un modelo entero, un color de ese modelo o una variante puntual."""
    __tablename__ = "ubicacion_articulos"
    id: Mapped[int] = mapped_column(primary_key=True)
    ubicacion_id: Mapped[int] = mapped_column(ForeignKey("ubicaciones.id"), index=True)
    modelo: Mapped[str] = mapped_column(String(120), default="", index=True)  # Articulo.articulo
    color: Mapped[str] = mapped_column(String(60), default="")               # vacío = todos los colores
    articulo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("articulos.id"), nullable=True, index=True)
    nota: Mapped[str] = mapped_column(String(120), default="")
    creado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    ubicacion: Mapped[Ubicacion] = relationship(back_populates="asignaciones")
    articulo_ref: Mapped[Optional[Articulo]] = relationship()


class SkuVinculo(Base):
    """SKU que viene de ML / Tienda Nube ↔ artículo del catálogo (cuando no coincide solo)."""
    __tablename__ = "sku_vinculos"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku_externo: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    articulo_id: Mapped[int] = mapped_column(ForeignKey("articulos.id"))
    creado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)

    articulo: Mapped[Articulo] = relationship()


# ─────────────── Recolección (picking de las ventas del día) ───────────────
class Recoleccion(Base):
    __tablename__ = "recolecciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    nombre: Mapped[str] = mapped_column(String(200), default="")
    origen: Mapped[str] = mapped_column(String(20), default="archivo")  # drive | despacho | archivo
    tipo: Mapped[str] = mapped_column(String(20), default="")          # flex | colecta | tiendanube | mixto
    paquetes: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String(20), default="abierta")  # abierta | cerrada
    cerrada_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cerrada_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    usuario: Mapped[Optional[Usuario]] = relationship(foreign_keys=[usuario_id])
    cerrada_por: Mapped[Optional[Usuario]] = relationship(foreign_keys=[cerrada_por_id])
    items: Mapped[list["RecoleccionItem"]] = relationship(back_populates="recoleccion",
                                                          cascade="all, delete-orphan")


class RecoleccionItem(Base):
    __tablename__ = "recoleccion_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    recoleccion_id: Mapped[int] = mapped_column(ForeignKey("recolecciones.id"), index=True)
    sku: Mapped[str] = mapped_column(String(200), default="")       # tal como vino de ML / TN
    producto: Mapped[str] = mapped_column(Text, default="")         # título de la publicación
    articulo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("articulos.id"), nullable=True)
    articulo: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(60), default="")
    talle: Mapped[str] = mapped_column(String(20), default="")
    codigo_barras: Mapped[str] = mapped_column(String(20), default="")
    ubicacion: Mapped[str] = mapped_column(String(120), default="")  # códigos (puede haber más de uno)
    orden_ruta: Mapped[int] = mapped_column(Integer, default=999999)
    cantidad: Mapped[int] = mapped_column(Integer, default=1)
    recogido: Mapped[int] = mapped_column(Integer, default=0)
    ventas: Mapped[str] = mapped_column(Text, default="")            # nº de venta / envío, separados por coma
    recogido_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    recogido_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    recoleccion: Mapped[Recoleccion] = relationship(back_populates="items")


# ─────────────── Control de paquetes (verificación al armar cada paquete) ───────────────
class Control(Base):
    """Una tanda de control: los paquetes de un archivo (o varios) de etiquetas / ventas del día."""
    __tablename__ = "controles"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    creado_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    nombre: Mapped[str] = mapped_column(String(200), default="")
    origen: Mapped[str] = mapped_column(String(20), default="archivo")  # drive | despacho | archivo
    tipo: Mapped[str] = mapped_column(String(20), default="")          # flex | colecta | tiendanube
    archivos: Mapped[str] = mapped_column(Text, default="")            # nombres de los archivos cargados
    estado: Mapped[str] = mapped_column(String(20), default="abierto")  # abierto | cerrado
    cerrado_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    modo: Mapped[str] = mapped_column(String(20), default="")  # "" = control suelto | "ecommerce" = Recolección Ecommerce
    drive_resultado: Mapped[str] = mapped_column(String(300), default="")  # link al Excel guardado en Drive

    usuario: Mapped[Optional[Usuario]] = relationship()
    paquetes: Mapped[list["ControlPaquete"]] = relationship(back_populates="control", cascade="all, delete-orphan",
                                                            order_by="ControlPaquete.id")
    participantes: Mapped[list["ControlParticipante"]] = relationship(back_populates="control",
                                                                      cascade="all, delete-orphan",
                                                                      order_by="ControlParticipante.id")


class ControlPaquete(Base):
    __tablename__ = "control_paquetes"
    id: Mapped[int] = mapped_column(primary_key=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("controles.id"), index=True)
    tracking: Mapped[str] = mapped_column(String(60), index=True)       # nº de envío (lo que se escanea)
    tracking_alt: Mapped[str] = mapped_column(String(80), default="")
    venta_id: Mapped[str] = mapped_column(String(60), default="", index=True)
    comprador: Mapped[str] = mapped_column(String(120), default="")
    tipo: Mapped[str] = mapped_column(String(20), default="")  # flex | colecta | tiendanube | ml
    unidades_etiqueta: Mapped[int] = mapped_column(Integer, default=0)  # lo que dice la etiqueta de ML
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")  # pendiente | en_curso | completo | faltante
    errores: Mapped[int] = mapped_column(Integer, default=0)
    nota: Mapped[str] = mapped_column(String(300), default="")
    iniciado_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    controlado_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    controlado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)

    control: Mapped[Control] = relationship(back_populates="paquetes")
    controlado_por: Mapped[Optional[Usuario]] = relationship()
    items: Mapped[list["ControlItem"]] = relationship(back_populates="paquete", cascade="all, delete-orphan",
                                                      order_by="ControlItem.id")


class ControlItem(Base):
    __tablename__ = "control_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    paquete_id: Mapped[int] = mapped_column(ForeignKey("control_paquetes.id"), index=True)
    sku: Mapped[str] = mapped_column(String(200), default="")         # tal como vino de ML / TN
    producto: Mapped[str] = mapped_column(Text, default="")           # título de la publicación
    variante: Mapped[str] = mapped_column(String(200), default="")    # "Color: Negro · Talle: XL" (de la venta)
    articulo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("articulos.id"), nullable=True)
    aceptados: Mapped[str] = mapped_column(Text, default="")          # ids de artículos válidos (otras marcas)
    articulo: Mapped[str] = mapped_column(String(120), default="")
    color: Mapped[str] = mapped_column(String(60), default="")
    talle: Mapped[str] = mapped_column(String(20), default="")
    codigo_barras: Mapped[str] = mapped_column(String(20), default="")
    ubicacion: Mapped[str] = mapped_column(String(200), default="")
    orden_ruta: Mapped[int] = mapped_column(Integer, default=999999)
    cantidad: Mapped[int] = mapped_column(Integer, default=1)
    escaneado: Mapped[int] = mapped_column(Integer, default=0)
    faltante: Mapped[bool] = mapped_column(Boolean, default=False)
    # Recolección Ecommerce: a quién le toca buscarla y cuántas trajo
    asignado_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True, index=True)
    buscado: Mapped[int] = mapped_column(Integer, default=0)
    buscado_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    paquete: Mapped[ControlPaquete] = relationship(back_populates="items")
    asignado: Mapped[Optional[Usuario]] = relationship(foreign_keys=[asignado_id])


class ControlParticipante(Base):
    """Recolección Ecommerce: operario que se sumó a buscar las prendas de una tanda."""
    __tablename__ = "control_participantes"
    id: Mapped[int] = mapped_column(primary_key=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("controles.id"), index=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    unido_at: Mapped[datetime] = mapped_column(DateTime, default=ahora)
    salio_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    control: Mapped[Control] = relationship(back_populates="participantes")
    usuario: Mapped[Usuario] = relationship()


class ControlEvento(Base):
    """Lo que hay que mirar después: prendas equivocadas, sobrantes, faltantes, reinicios."""
    __tablename__ = "control_eventos"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=ahora, index=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("controles.id", ondelete="CASCADE"), index=True)
    paquete_id: Mapped[Optional[int]] = mapped_column(ForeignKey("control_paquetes.id", ondelete="CASCADE"),
                                                      nullable=True)
    usuario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id"), nullable=True)
    tipo: Mapped[str] = mapped_column(String(20))   # equivocado | sobra | desconocido | faltante | reinicio
    codigo: Mapped[str] = mapped_column(String(80), default="")
    detalle: Mapped[str] = mapped_column(Text, default="")

    usuario: Mapped[Optional[Usuario]] = relationship()
