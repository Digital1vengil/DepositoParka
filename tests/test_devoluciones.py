import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import devoluciones as svc
from app.services.etiquetas import pdf_etiquetas, ean13_valido


def _login(c, nombre="Vengil", pin="1234"):
    uid = re.search(r"elegir\((\d+), '" + nombre + r"'\)", c.get("/login").text).group(1)
    assert c.post("/login", data={"usuario_id": uid, "pin": pin}).json()["ok"]


def test_ean_y_pdf():
    assert ean13_valido("2000000000909") and not ean13_valido("2000000000900")
    pdf = pdf_etiquetas([{"articulo": "00AXE", "color": "Navystripes", "talle": "XXL", "sku": "1-CAMI-M-00AXE-NVST-XXL-NOBR",
                          "codigo_barras": "2000000000909"}] * 45, desde=3, guias=True)
    assert pdf[:4] == b"%PDF" and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 2


def test_flujo_devolucion(monkeypatch):
    enviados = []

    def fake_enviar(dev):
        enviados.append(dev)
        return {"ok": True, "id": "D-001", "credito": "Crédito D-001", "reingreso": "Reingreso D-001", "total": dev.total}
    monkeypatch.setattr(svc, "enviar", fake_enviar)
    with TestClient(app) as c:
        _login(c)
        assert "Devoluciones" in c.get("/").text
        # solo Parka o Puffers
        r = c.get("/devoluciones/api/buscar?q=abba 02003").json()["modelos"]
        m = next(x for x in r if x["articulo"] == "ABBA 02003")
        assert {v["marca"] for v in m["variantes"]} == {"Parka"}
        assert all(v["sku"].endswith("-PARK") for v in m["variantes"])
        assert c.get("/devoluciones/api/buscar?q=00axe").json()["modelos"] == []   # 00AXE es sin marca
        v = next(x for x in m["variantes"] if x["color"] == "BLACK" and x["talle"] == "M")
        assert v["codigo_barras"] == "2000000035925"
        r2 = c.get("/devoluciones/api/buscar?q=2000000035925").json()["modelos"]
        assert r2[0]["preseleccion"] == v["id"]
        assert c.get("/devoluciones/api/buscar?q=2000000035918").json()["modelos"] == []  # mismo talle, sin marca
        res = c.post("/devoluciones/api/confirmar", json={"items": [
            {"articulo_id": v["id"], "cantidad": 3, "estado": "OPTIMO"},
            {"articulo_id": v["id"], "cantidad": 1, "estado": "FALLA"}]}).json()
        d = res["devolucion"]
        assert d["drive_ok"] and d["total"] == 4 and d["drive"]["id"] == "D-001"
        assert enviados[0].items[1].estado == "FALLA" and enviados[0].items[0].talle == "M"
        pdf = c.get(f"/devoluciones/{d['id']}/etiquetas.pdf?desde=5")
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
        assert c.get("/devoluciones/api/historial").json()["devoluciones"][0]["id"] == d["id"]
        assert c.post("/devoluciones/api/confirmar", json={"items": []}).status_code == 400
        # un artículo sin marca no se acepta aunque manden el id a mano
        from app.db import SessionLocal
        from app.models import Articulo
        with SessionLocal() as db:
            nobr = db.query(Articulo).filter(Articulo.codigo_barras == "2000000035918").one().id
        assert c.post("/devoluciones/api/confirmar", json={"items": [{"articulo_id": nobr, "cantidad": 1}]}).status_code == 400
        # Puffers
        p = c.get("/devoluciones/api/buscar?q=axis 01155").json()["modelos"][0]
        assert "Puffers" in {x["marca"] for x in p["variantes"]} and {x["marca"] for x in p["variantes"]} <= {"Parka", "Puffers"}


def test_drive_falla_y_reintento(monkeypatch):
    def falla(dev):
        raise RuntimeError("sin conexión")
    monkeypatch.setattr(svc, "enviar", falla)
    with TestClient(app) as c:
        _login(c)
        v = c.get("/devoluciones/api/buscar?q=2000000035901").json()["modelos"][0]["variantes"][0]
        d = c.post("/devoluciones/api/confirmar", json={"items": [{"articulo_id": v["id"], "cantidad": 1}]}).json()["devolucion"]
        assert not d["drive_ok"] and "sin conexión" in d["drive_error"]
        monkeypatch.setattr(svc, "enviar", lambda dev: {"ok": True, "id": "D-2"})
        assert c.post(f"/devoluciones/api/{d['id']}/reintentar").json()["devolucion"]["drive_ok"]


def test_color_talle_casos():
    from app.models import Articulo
    assert svc.color_talle(Articulo(variante="Navystripes, XXL", talle="XXL")) == ("Navystripes", "XXL")
    assert svc.color_talle(Articulo(variante="XL, Parka", talle="XL", color="BLCK")) == ("BLCK", "XL")
    assert svc.color_talle(Articulo(variante="parca", talle="")) == ("Único", "Único")
    assert svc.color_talle(Articulo(variante="BLACK, M, nobrand", talle="M")) == ("BLACK", "M")


def test_marca_parka_puffers():
    from app.models import Articulo
    assert svc.marca(Articulo(sku="1-CAMP-M-01155-GREN-L-PUFE", variante="Green, L, Puffers")) == "Puffers"
    assert svc.marca(Articulo(sku="1-CAMP-W-02003-BLCK-M-PARK", variante="BLACK, M, Parka")) == "Parka"
    assert svc.marca(Articulo(sku="1-CAMP-W-02003-BLCK-M-NOBR", variante="BLACK, M, nobrand")) == "No Brand"
    assert svc.color_talle(Articulo(variante="Green, L, Puffers", talle="L")) == ("Green", "L")


def test_celular_https(monkeypatch):
    with TestClient(app) as c:
        assert "no está activa" in c.get("/celular").text
        monkeypatch.setenv("PARKA_HTTPS_PUERTO", "8443")
        monkeypatch.setenv("PARKA_URL_CELULAR", "https://192.168.0.15:8443")
        r = c.get("/despacho?x=1", follow_redirects=False)       # un celular por http → https
        assert r.status_code == 307 and r.headers["location"] == "https://testserver:8443/despacho?x=1"
        assert c.get("/salud").json()["ok"]                      # el lanzador sigue viendo /salud por http
    with TestClient(app, base_url="https://testserver") as c:
        t = c.get("/celular?ir=/despacho").text
        assert "https://192.168.0.15:8443/despacho" in t and "<svg" in t


def test_etiquetas_html_ean_y_code128():
    from app.services.etiquetas_html import html_etiquetas, _svg_barras
    h = html_etiquetas([{"nombre": "ALPHA 01159 (BLACK, S, Parka)", "sku": "1-CAMP-M-01159-BLCK-S-PARK",
                         "codigo_barras": "2000000023496"}] * 41, desde=1)
    assert h.count('class="hoja"') == 2 and h.count('class="et') == 41
    assert "window.print()" in h and "2000000023496" in h
    assert "<rect" in _svg_barras("A-03-2")
