"""Reproducoes locais da revisao; nao altera codigo nem preferencias reais.

Executar da raiz: .venv\Scripts\python.exe docs/revisao_probes.py
As verificacoes registram o comportamento atual, inclusive defeitos.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Mesma ordem de carregamento usada no lancador e no conftest do projeto.
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QInputDialog
import PySide6.QtWebEngineWidgets

import ezdxf
from shapely.geometry import LineString, Polygon, box
from app.core.models import NestParams, NestResult, Part, Placement, Prim
from app.core.part_builder import build_parts_from_prims, group_identical, import_files, make_part
from app.core.project import save_project, load_project
from app.core.validate import fine_solid, validate_layout
from app.core.geometry import flatten_prim
from app.core.dxf_export import export_sheets
from app.core import updater


def rect(x, y, w, h, color=7, rgb=None):
    return Prim("POLY", {"pts": [[x, y, 0], [x+w, y, 0], [x+w, y+h, 0], [x, y+h, 0]],
                         "closed": True}, color=color, rgb=rgb)


def zip_data(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Sindri-probe/", b"")
        for name, value in files.items():
            z.writestr("Sindri-probe/" + name, value)
    return buf.getvalue()


def dxf(path, width=20, height=10, units=4):
    doc = ezdxf.new("R2000")
    doc.units = units
    doc.modelspace().add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height)], close=True)
    doc.saveas(path)


def run():
    out = {}
    with tempfile.TemporaryDirectory(prefix="revisao-", dir=ROOT / "docs") as tmp:
        tmp = Path(tmp)
        p = Part("P001", "quadrado", "", box(-5, -5, 5, 5), [], [], [], quantity=2)
        pls = [Placement(p.id, i, 0, 30, 30, 0) for i in range(2)]
        out["sobreposicao_com_espaco_zero"] = {
            "area_sobreposta_mm2": 100,
            "problemas_detectados": validate_layout({p.id:p}, pls, NestParams(spacing=0)),
        }

        raw, _, _ = build_parts_from_prims([rect(0,0,100,100), rect(200,0,100.2,100)], "", .05, .1)
        grouped = group_identical(raw, .1)
        out["dimensoes_diferentes_agrupadas"] = {
            "larguras_originais_mm": [100, 100.2], "grupos": len(grouped),
            "quantidades": [n for _, n in grouped],
        }
        raw, _, _ = build_parts_from_prims([rect(0,0,20,20,rgb=(255,0,0)),
                                           rect(50,0,20,20,rgb=(0,0,255))], "", .05, .1)
        out["cores_rgb_diferentes_agrupadas"] = {"grupos": len(group_identical(raw, .1))}
        holes_raw, _, _ = build_parts_from_prims([rect(0,0,100,100,rgb=(255,0,0)),
                                                  rect(30,30,30,30,rgb=(0,0,255))],"",.05,.1)
        out["cores_rgb_diferentes_agrupadas"]["contorno_interno_de_outra_cor_aceito_como_furo"] = len(holes_raw[0].holes)

        bow = Prim("POLY", {"pts":[[0,0,0],[40,40,0],[0,40,0],[40,0,0]], "closed":True})
        raw, _, warnings = build_parts_from_prims([bow], "", .05, .1)
        bowpart = make_part(raw[0], "P001", "auto-intersecao", 1)
        solid = fine_solid(bowpart)
        drawing = LineString(flatten_prim(bowpart.prims[0], .01))
        out["contorno_autointersectante"] = {
            "area_considerada_mm2": solid.area,
            "comprimento_exportado_fora_do_molde_mm": drawing.difference(solid.buffer(.02)).length,
            "avisos": warnings,
        }
        from shapely import make_valid
        lost = make_valid(Polygon(flatten_prim(bowpart.prims[0],.01))).difference(solid)
        loc = lost.representative_point()
        other = Part("P002","outra","",box(-1,-1,1,1),[],[],[])
        out["contorno_autointersectante"]["layout_com_peca_na_area_omitida"] = validate_layout(
            {bowpart.id:bowpart,other.id:other},
            [Placement(bowpart.id,0,0,60,60,0),Placement(other.id,0,0,60+loc.x,60+loc.y,0)],
            NestParams())

        root = tmp / "update"
        root.mkdir()
        (root / ".venv").mkdir()
        (root / ".venv" / "probe.txt").write_bytes(b"original")
        rejected = False
        try:
            updater.apply_zip(zip_data({"sindri.py":b"new", "app/main.py":b"new",
                                       "..\\outside.txt":b"outside"}),str(root),"probe")
        except ValueError:
            rejected = True
        updater.apply_zip(zip_data({"sindri.py":b"new", "app/main.py":b"new",
                                   ".VENV/probe.txt":b"changed"}),str(root),"probe")
        out["zip_caminhos_windows"] = {
            "escreveu_fora_da_raiz": (tmp / "outside.txt").exists(),
            "travessia_rejeitada":rejected,
            "sobrescreveu_pasta_protegida": (root / ".venv" / "probe.txt").read_bytes() == b"changed",
        }
        root2 = tmp / "update-failure"
        root2.mkdir()
        (root2 / "app").mkdir()
        for f in (root2 / "sindri.py", root2 / "app" / "main.py"):
            f.write_bytes(b"old")
        replace = os.replace
        def fail_second(src, dst):
            if Path(dst).name == "main.py":
                raise PermissionError("falha simulada de gravacao")
            return replace(src, dst)
        with patch.object(updater.os, "replace", fail_second):
            try:
                updater.apply_zip(zip_data({"sindri.py":b"new", "app/main.py":b"new"}), str(root2), "probe")
            except PermissionError:
                pass
        out["atualizacao_parcial"] = {
            "lancador": (root2 / "sindri.py").read_text(),
            "app_main": (root2 / "app" / "main.py").read_text(),
        }

        source = tmp / "original.dxf"
        dxf(source, 100, 50)
        rep = import_files([str(source)])
        pp = rep.parts[0]
        project = str(tmp / "a.sindri")
        result = NestResult([Placement(pp.id,0,0,70,50,0)],1,.1,0)
        save_project(project,[str(source)],NestParams(),rep.parts,result)
        dxf(source, 125, 40)  # mesma area, geometria diferente
        loaded = load_project(project)
        out["fonte_alterada_mesma_area"] = {
            "antes_mm":[100,50], "depois_mm":list(loaded.parts[0].size),
            "avisos":loaded.warnings, "layout_antigo_mantido":loaded.result is not None,
        }
        try:
            save_project(str(tmp / "cross-drive.sindri"),["Z:/arquivo.dxf"],NestParams(),[],None)
            cross_drive = "sem erro"
        except ValueError as exc:
            cross_drive = str(exc)
        out["projeto_em_outra_unidade"] = {"resultado":cross_drive}
        from contextlib import redirect_stdout
        from app.cli import main as cli_main
        cli_log = io.StringIO()
        cli_folder = tmp / "cli"
        with redirect_stdout(cli_log):
            cli_rc = cli_main([str(source),"--placa","10x10","--margem","0", "--tempo","0",
                               "--processos","0","--saida",str(cli_folder)])
        out["cli_sucesso_sem_pecas"] = {"exit_code":cli_rc,"arquivos_dxf":len(list(cli_folder.glob("*.dxf")))}

        import setuptools
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dist = setuptools.Distribution({"packages":config["tool"]["setuptools"]["packages"]})
        dist.script_name = "setup.py"
        build = dist.get_command_obj("build_py")
        build.ensure_finalized()
        modules = build.find_all_modules()
        out["empacotamento"] = {"modulos_mainwindow_listados":[p+"."+m for p,m,_ in modules if "mainwindow" in p]}

        host = Part("H", "host", "", box(-40,-40,40,40), [box(-25,-25,25,25)],
                    [rect(-40,-40,80,80,1),rect(-25,-25,50,50,2)],[0])
        child = Part("C", "child", "", box(-5,-5,5,5), [], [rect(-5,-5,10,10,3)],[0])
        cut = [Placement("H",0,0,50,50,0),Placement("C",0,0,55,55,0)]
        files = export_sheets([host,child],cut,NestParams(),str(tmp / "cut"),inner_first=True)
        out["ordem_peca_em_furo"] = {
            "cores_na_ordem_do_dxf":[e.dxf.color for e in ezdxf.readfile(files[0]).modelspace()],
            "significado":{"1":"externo hospedeira","2":"furo hospedeira","3":"peca dentro do furo"},
        }

        import app.ui.dialogs as dialogs
        app = QApplication.instance() or QApplication([])
        cfg = QSettings(str(tmp / "preferences.ini"), QSettings.IniFormat)
        with patch.object(dialogs,"settings",lambda:cfg):
            # Importar depois do patch isola todos os aliases de settings().
            from app.ui.main_window import MainWindow
            with patch.object(QMessageBox,"exec",lambda self:0), \
                 patch.object(QMessageBox,"question",lambda *a,**kw:QMessageBox.Yes), \
                 patch.object(QMessageBox,"warning",lambda *a,**kw:QMessageBox.Yes), \
                 patch.object(MainWindow,"autosave_path",staticmethod(lambda:str(tmp / "auto.sindri"))):
                w = MainWindow(workers=0)
                w.open_project(project)
                new_source = tmp / "outro-trabalho.dxf"
                dxf(new_source,30,10)
                w.load_files([str(new_source)])
                out["novo_dxf_mantem_projeto_anterior"] = {"mesmo_caminho":w.project_path == project}

                w.parts[0].quantity = 7
                w.load_files([str(source)],add=True)
                out["adicionar_arquivo_perde_quantidade"] = {
                    "quantidade_antes":7,
                    "quantidade_depois":next(p.quantity for p in w.parts if p.source_file==str(new_source)),
                }

                actual = tmp / "inch.dxf"
                wrong = tmp / "wrong-units.dxf"
                dxf(actual, 1, .5, units=1)
                dxf(wrong, 1000, 200, units=1)
                w.load_files([str(wrong),str(actual)])
                out["correcao_global_de_unidades"] = {
                    "largura_esperada_arquivo_valido_mm":25.4,
                    "largura_obtida_mm":next(p.size[0] for p in w.parts if p.source_file == str(actual)),
                }

                w.load_files([str(source)])
                p = w.parts[0]
                p.quantity = 3
                w.placements = [Placement(p.id,i,0,80+i*140,80,0) for i in range(3)]
                w.n_sheets = 1
                w.tabs.setCurrentIndex(1)
                w._redraw()
                with patch.object(QInputDialog,"getInt",return_value=(1,True)):
                    w.multiply_kits()
                out["reduzir_kits_mantem_copias"] = {"quantidade":p.quantity,"posicionadas":len(w.placements)}

                w.placements = w.placements[:1]
                p.quantity = 3
                w.on_sheet_cut(0,True)
                out["checklist_ignora_copias_sem_lugar"] = {
                    "quantidade_solicitada":p.quantity,"posicionadas":len(w.placements),
                    "tipo_marcado_feito":p.id in w.done_parts,
                }
                if getattr(w,"_autosave_timer",None):
                    w._autosave_timer.stop()
                w._redraw()
                w.canvas.part_items[0].setSelected(True)
                w.rotate_selected()
                out["rotacao_nao_agenda_autosave"] = {"timer_ativo":w._autosave_timer.isActive()}
                w.dirty = False
                w.close()
                app.processEvents()
        cfg.sync()
    dest = ROOT / "docs" / "revisao-reproducoes.json"
    dest.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(out,indent=2,ensure_ascii=True))


if __name__ == "__main__":
    run()
