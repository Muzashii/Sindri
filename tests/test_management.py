"""Gestão: tempo de máquina por placa, rateio por solicitação, CSV do mês, ordem física e relatório."""
import csv
import datetime as dt
import os

import pytest
from shapely.geometry import box

from app.core.machine_time import (TimeModel, append_monthly_csv, request_shares, sheet_time)
from app.core.models import Part, Placement, Prim


def _rect(pid, w, h, material="MDF 3mm", tag="", color=7, extra=()):
    prims = [Prim("POLY", {"pts": [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)],
                           "closed": True}, "CORTE", color)] + list(extra)
    p = Part(pid, pid, "x.dxf", box(-w / 2, -h / 2, w / 2, h / 2), [], prims, [0], material=material, tag=tag)
    return p


def test_tempo_de_corte_e_passadas():
    a = _rect("A", 80, 50)
    pmap = {"A": a}
    pls = [Placement("A", 0, 0, 100, 100, 0)]
    model = TimeModel(travel_speed=100, start_overhead=0.5)
    t = sheet_time(pmap, pls, lambda m, op: {"speed": 20, "passes": 1}, model=model)
    assert t.cut_length == pytest.approx(260, rel=1e-3)
    assert t.seconds == pytest.approx(260 / 20 + 0.5 + (100 * 2 ** 0.5) / 100, rel=1e-3)
    t2 = sheet_time(pmap, pls, lambda m, op: {"speed": 20, "passes": 2}, model=model)
    assert t2.cut_length == pytest.approx(520, rel=1e-3) and not t2.fallback
    t3 = sheet_time(pmap, pls, lambda m, op: {}, model=model)        # sem velocidade cadastrada
    assert t3.fallback == {"corte"} and "velocidade padrão" in t3.text


def test_tempo_de_scan():
    fill = Prim("POLY", {"pts": [(-10, -5), (10, -5), (10, 5), (-10, 5)], "closed": True}, "RASTER", 2)
    a = _rect("A", 80, 50, extra=[fill])
    ops = {("MDF 3mm", 2): "gravacao_raster"}
    params = {"corte": {"speed": 20}, "gravacao_raster": {"speed": 200, "interval": 0.1}}
    t = sheet_time({"A": a}, [Placement("A", 0, 0, 0, 0, 0)], lambda m, op: params[op], ops,
                   TimeModel(start_overhead=0, scan_turnaround=0.05))
    # 10 mm / 0,1 = 100 linhas × (20 mm / 200 mm/s + 0,05 s)
    assert t.by_op["gravacao_raster"] == pytest.approx(100 * (20 / 200 + 0.05), rel=1e-3)


def test_rateio_por_solicitacao():
    pmap = {"A": _rect("A", 100, 100, tag="10"), "B": _rect("B", 100, 50, tag="11")}
    pls = [Placement("A", 0, 0, 60, 60, 0), Placement("B", 0, 0, 200, 60, 0), Placement("B", 1, 1, 60, 60, 0)]
    shares = request_shares(pmap, pls, {0: 240000.0, 1: 240000.0}, {0: 12.0, 1: 6.0}, {1: 1000.0})
    a, b = shares[("10", "MDF 3mm")], shares[("11", "MDF 3mm")]
    assert a.sheet_area == pytest.approx(240000 * 2 / 3) and a.minutes == pytest.approx(8.0)
    assert b.sheet_area == pytest.approx(240000 / 3 + 240000) and b.minutes == pytest.approx(4.0 + 6.0)
    assert b.saved == pytest.approx(1000) and b.copies == 2 and b.sheets == {0, 1}


def test_csv_do_mes_sem_duplicar(tmp_path):
    row = {"data": "2026-10-10", "lote": "lote1", "solicitacao": "10", "aluno": "Ana", "material": "MDF 3mm",
           "placas": "1", "copias": 2, "area_pecas_cm2": "100,0", "area_chapa_cm2": "160,0",
           "minutos_maquina": "8,0", "aproveitamento_pct": "62,5", "retalho_salvo_cm2": "0,0"}
    path = append_monthly_csv(str(tmp_path), [row], dt.date(2026, 10, 10))
    append_monthly_csv(str(tmp_path), [{**row, "minutos_maquina": "9,0"}], dt.date(2026, 10, 10))
    append_monthly_csv(str(tmp_path), [{**row, "lote": "lote2"}], dt.date(2026, 10, 10))
    assert os.path.basename(path) == "2026-10.csv"
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig"), delimiter=";"))
    assert [(r["lote"], r["minutos_maquina"]) for r in rows] == [("lote1", "9,0"), ("lote2", "8,0")]


def test_ordem_pequenas_primeiro():
    from app.core.dxf_export import order_placements
    pmap = {"G": _rect("G", 200, 150), "P": _rect("P", 20, 20), "M": _rect("M", 60, 40)}
    pls = [Placement("G", 0, 0, 110, 90, 0), Placement("P", 0, 0, 400, 300, 0), Placement("M", 0, 0, 400, 50, 0)]
    assert [p.part_id for p in order_placements(pmap, pls)] == ["G", "M", "P"]
    assert [p.part_id for p in order_placements(pmap, pls, mode="pequenas")] == ["P", "M", "G"]


def test_exportacao_com_tempo_csv_e_relatorio(tmp_path, monkeypatch):
    from app.ui.dialogs import settings
    from app.ui.main_window import MainWindow
    w = MainWindow(workers=0)
    w.parts = [_rect("A", 100, 60, tag="8787"), _rect("B", 80, 40, tag="8790")]
    w.pmap = {p.id: p for p in w.parts}
    w.files = [str(tmp_path / "x.dxf")]
    w.request_info = {"batch": True, "materials": ["MDF 3mm"],
                      "requests": [{"code": "8787", "nome": "Ana"}, {"code": "8790", "nome": "Bia"}]}
    w.placements = [Placement("A", 0, 0, 70, 50, 0), Placement("B", 0, 0, 200, 50, 0)]
    w.n_sheets = 1
    w.request_label = "lote9"
    w.set_material_params("MDF 3mm", "corte", {"speed": 20, "power": 60, "passes": 1})
    st = settings()
    st.setValue("export/last_dir", str(tmp_path / "out"))
    st.setValue("export/open_rdworks", "false")
    os.makedirs(tmp_path / "out")
    w.export()
    assert "Tempo estimado: Placa 1 ~" in w.banner_text.text()
    csv_path = os.path.join(w.management_folder(), f"{dt.date.today():%Y-%m}.csv")
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig"), delimiter=";"))
    assert sorted(r["solicitacao"] for r in rows) == ["8787", "8790"]
    assert {r["aluno"] for r in rows} == {"Ana", "Bia"} and all(r["lote"] == "lote9" for r in rows)
    # o relatório traz tempo, camadas e o campo de quem cortou
    from pypdf import PdfReader
    text = " ".join(" ".join(pg.extract_text() or "" for pg in
                             PdfReader(str(tmp_path / "out" / "lote9_relatorio.pdf")).pages).split())
    assert "Cortado por" in text and "tempo estimado" in text and "Camadas no RDWorks" in text
    assert "Uso de material e máquina por solicitação" in text
    # guardar a sobra atualiza o CSV (a sobra conta como não-perda)
    w.on_sheet_cut(0, True)
    w.save_leftover(0)
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig"), delimiter=";"))
    assert len(rows) == 2 and any(r["retalho_salvo_cm2"] != "0,0" for r in rows)
    w.dirty = False
    w.close()
