"""Ajustes guardados en la base (los carga el admin desde Conexiones). Si no hay, se usa el .env."""
import os

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Ajuste

# clave -> (variable del .env, valor por defecto, es_secreto)
CLAVES = {
    "parkahub_url": ("PARKAHUB_URL", "https://parkahub.pages.dev", False),
    "parkahub_client_id": ("PARKAHUB_CLIENT_ID", "", True),
    "parkahub_client_secret": ("PARKAHUB_CLIENT_SECRET", "", True),
    "gemini_api_key": ("GEMINI_API_KEY", "", True),
    "gemini_modelo": ("GEMINI_MODELO", "gemini-2.5-flash", False),
    # Odoo: catálogo de artículos y códigos de barra (solo lectura, usuario técnico con API key)
    "odoo_url": ("ODOO_URL", "https://magontex.odoo.com", False),
    "odoo_db": ("ODOO_DB", "oncompetence-magontex-main-28955103", False),
    "odoo_usuario": ("ODOO_USUARIO", "", False),
    "odoo_api_key": ("ODOO_API_KEY", "", True),
    "odoo_sync_minutos": ("ODOO_SYNC_MINUTOS", "60", False),
    # Recolección Ecommerce: Apps Script "Recolección Ecommerce — Backend Drive" (carpeta del día)
    "re_url": ("RE_URL", "", False),
    "re_token": ("RE_TOKEN", "", True),
}


def leer(clave: str, db: Session | None = None) -> str:
    env, defecto, _ = CLAVES[clave]
    propia = db is None
    db = db or SessionLocal()
    try:
        a = db.get(Ajuste, clave)
        if a and a.valor:
            return a.valor
    except Exception:  # noqa: BLE001 — tabla todavía no creada
        pass
    finally:
        if propia:
            db.close()
    return os.environ.get(env, "") or defecto


def guardar(db: Session, clave: str, valor: str) -> None:
    if clave not in CLAVES:
        raise KeyError(clave)
    a = db.get(Ajuste, clave)
    if a is None:
        db.add(Ajuste(clave=clave, valor=valor))
    else:
        a.valor = valor
    db.commit()


def enmascarar(v: str) -> str:
    return "" if not v else ("•" * 6 + v[-4:] if len(v) > 8 else "•" * len(v))


def resumen(db: Session) -> dict:
    """Para la pantalla: los secretos solo se muestran enmascarados."""
    out = {}
    for k, (_, _, secreto) in CLAVES.items():
        v = leer(k, db)
        out[k] = {"valor": enmascarar(v) if secreto else v, "cargado": bool(v), "secreto": secreto}
    return out
