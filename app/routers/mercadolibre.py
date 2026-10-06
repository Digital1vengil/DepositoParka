"""Sección Mercado Libre: lo que ParkaHub ya calcula, visto desde PARKA Depósito (solo lectura).

Ventas, preguntas sin responder (con la sugerencia de ParkaHub), devoluciones por motivo/SKU, reputación,
y búsqueda de venta / publicación / envío. Todo pasa por services/parkahub.py: no hay token propio de ML.
"""
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import requiere
from ..models import Usuario
from ..services import parkahub
from ..templating import render

router = APIRouter(prefix="/ml")
acceso = requiere("ml")


def _leer(fn, *a, **k):
    try:
        return fn(*a, **k)
    except parkahub.ParkaHubError as e:
        raise HTTPException(400, str(e)) from None


@router.get("")
def pagina(request: Request, u: Usuario = Depends(acceso)):
    return render(request, "mercadolibre.html", u=u, conectado=parkahub.configurado())


@router.get("/api/resumen")
def resumen(u: Usuario = Depends(acceso)):
    """Lo de arriba de la pantalla. Cada parte por separado: si una falla, las demás se muestran igual."""
    out, errores = {}, {}
    for clave, fn in (("estado", parkahub.estado), ("reputacion", parkahub.reputacion),
                      ("ventas_hoy", lambda: parkahub.ventas_recientes(0, 50)),
                      ("preguntas", lambda: parkahub.preguntas_sin_responder(50))):
        try:
            out[clave] = fn()
        except parkahub.ParkaHubError as e:
            errores[clave] = str(e)
    out["errores"] = errores
    return out


@router.get("/api/ventas")
def ventas(dias: int = 0, u: Usuario = Depends(acceso)):
    return _leer(parkahub.ventas_recientes, max(0, min(dias, 30)), 50)


@router.get("/api/preguntas")
def preguntas(u: Usuario = Depends(acceso)):
    return {"pendientes": _leer(parkahub.preguntas_sin_responder, 100),
            "respondidas": _leer(parkahub.preguntas_respondidas, 30)}


@router.get("/api/devoluciones")
def devoluciones(dias: int = 30, u: Usuario = Depends(acceso)):
    return _leer(parkahub.devoluciones_ml, max(1, min(dias, 180)))


@router.get("/api/buscar")
def buscar(q: str, u: Usuario = Depends(acceso)):
    """Adivina qué es: MLA… → publicación; número largo → venta (y si no, envío); texto → publicaciones."""
    t = (q or "").strip()
    if not t:
        raise HTTPException(400, "Escribí algo para buscar")
    tu = t.upper().replace("-", "")
    if tu.startswith("MLA") and tu[3:].isdigit():
        return {"tipo": "publicacion", "dato": _leer(parkahub.ver_publicacion, tu)}
    dig = "".join(c for c in t if c.isdigit())
    if dig and len(dig) >= 8 and len(dig) == len(t.replace(" ", "")):
        try:
            return {"tipo": "venta", "dato": parkahub.buscar_venta(dig)}
        except parkahub.ParkaHubError:
            return {"tipo": "envio", "dato": _leer(parkahub.estado_envio, dig)}
    return {"tipo": "publicaciones", "dato": _leer(parkahub.buscar_publicaciones, t)}
