"""Sección Panel PARKA: abre el panel del agente IA dentro de esta app. Solo admin."""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import config
from ..actividad import registrar
from ..auth import requiere
solo_admin = requiere("panel")
from ..db import get_db
from ..models import Usuario
from ..templating import render

router = APIRouter(prefix="/panel")


def _url_local() -> str:
    return config.PANEL_URL.rstrip("/") if config.PANEL_URL else f"http://127.0.0.1:{config.PANEL_PUERTO}"


def _ngrok() -> str:
    f = Path(config.PANEL_CARPETA) / "ngrok_dominio.txt"
    try:
        d = f.read_text(encoding="utf-8").strip()
        return f"https://{d}" if d and not d.startswith("http") else d
    except OSError:
        return ""


def _bat() -> Path:
    return Path(config.PANEL_CARPETA) / "iniciar_panel.bat"


@router.get("")
def pagina(request: Request, u: Usuario = Depends(solo_admin)):
    return render(request, "panel.html", u=u, panel_url=config.PANEL_URL, puerto=config.PANEL_PUERTO,
                  ngrok=_ngrok(), puede_iniciar=sys.platform == "win32" and _bat().exists())


@router.get("/api/estado")
def estado(u: Usuario = Depends(solo_admin)):
    try:
        with urllib.request.urlopen(_url_local() + "/", timeout=3) as r:
            return {"abierto": r.status < 500}
    except Exception:  # noqa: BLE001
        return {"abierto": False}


@router.post("/api/iniciar")
def iniciar(u: Usuario = Depends(solo_admin), db=Depends(get_db)):
    bat = _bat()
    if sys.platform != "win32" or not bat.exists():
        raise HTTPException(400, f"No encontré {bat}. Abrí el panel a mano con iniciar_panel.bat")
    subprocess.Popen(["cmd", "/c", "start", "", str(bat)], cwd=str(bat.parent),
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    registrar(db, u, "panel_iniciado", str(bat.parent))
    return {"ok": True}
