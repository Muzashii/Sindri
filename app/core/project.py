"""Salvar/abrir projeto (.sindri — antes .dxfnest —, JSON)."""
from __future__ import annotations

import json
import os
import hashlib
from dataclasses import dataclass, field, fields
from shapely import wkb
from typing import Optional

from .fileutil import replace_file
from .models import ImportReport, NestParams, NestResult, Part, Prim
from .part_builder import import_files

FORMAT = "dxfnest"
VERSION = 2


class ProjectError(Exception):
    pass


@dataclass
class Project:
    files: list[str]
    params: NestParams
    parts: list[Part] = field(default_factory=list)
    result: Optional[NestResult] = None
    report: Optional[ImportReport] = None
    warnings: list[str] = field(default_factory=list)
    multipliers: dict = field(default_factory=dict)
    label: Optional[str] = None
    materials: dict = field(default_factory=dict)
    request: Optional[dict] = None
    tags: dict = field(default_factory=dict)
    checklist: dict = field(default_factory=dict)
    units: dict = field(default_factory=dict)
    source_hashes: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)       # estado do laser/retalhos (veja save_project)


def source_hash(path):
    try:
        with open(path, "rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return None


def relative_path(path, base):
    try:
        return os.path.relpath(os.path.abspath(path), base)
    except ValueError:  # discos diferentes no Windows
        return os.path.abspath(path)


def _part_json(part):
    data = {f.name: getattr(part, f.name) for f in fields(part)}
    data.update(outer=part.outer.wkb_hex, holes=[h.wkb_hex for h in part.holes],
                prims=[p.to_json() for p in part.prims])
    return data


def _part_from_json(data):
    data = dict(data)
    data.update(outer=wkb.loads(data["outer"], hex=True),
                holes=[wkb.loads(h, hex=True) for h in data["holes"]],
                prims=[Prim.from_json(p) for p in data["prims"]])
    part = Part(**data)
    if type(part.quantity) is not int or part.quantity < 0 or part.file_quantity < 1:
        raise ValueError("Quantidade inválida no projeto.")
    if part.outer.is_empty or not part.outer.is_valid or part.outer.area <= 0:
        raise ValueError("Contorno inválido no projeto.")
    if any(type(i) is not int or not 0 <= i < len(part.prims) for i in part.outer_prim_idx):
        raise ValueError("Índice de geometria inválido no projeto.")
    return part


def _get(d: Optional[dict], f: str, default):
    if not d:
        return default
    return d.get(os.path.abspath(f), d.get(f, default))


def save_project(path: str, files: list[str], params: NestParams, parts: list[Part],
                 result: Optional[NestResult], multipliers: Optional[dict] = None,
                 label: Optional[str] = None, materials: Optional[dict] = None,
                 request: Optional[dict] = None, tags: Optional[dict] = None,
                 checklist: Optional[dict] = None, file_units: Optional[dict] = None,
                 source_hashes: Optional[dict] = None, report: Optional[ImportReport] = None,
                 extra: Optional[dict] = None) -> None:
    """``extra``: dados que só precisam voltar como estavam (JSON puro): operação de cada cor do
    arquivo, retalhos usados por placa etc."""
    params.validate()
    base = os.path.dirname(os.path.abspath(path))
    data = {
        "format": FORMAT,
        "version": VERSION,
        "files": [os.path.abspath(f) for f in files],
        "files_rel": [relative_path(f, base) for f in files],
        "params": params.to_json(),
        "parts": {p.id: {"quantity": p.quantity, "rotation_locked": p.rotation_locked,
                         "area": round(p.area, 2)} for p in parts},
        "result": result.to_json() if result else None,
        "multipliers": [[relative_path(k, base), int(v)]
                        for k, v in (multipliers or {}).items()],
        "label": label,
        "materials": [[relative_path(k, base), v] for k, v in (materials or {}).items()],
        "request": request,
        "checklist": checklist or {},
        "extra": extra or {},
        "tags": [[relative_path(k, base), v] for k, v in (tags or {}).items()],
        "geometry": [_part_json(p) for p in parts],
        "import_report": {"preview": [[p.to_json(), issue] for p, issue in report.preview],
                          "warnings": report.warnings, "unit_notes": report.unit_notes,
                          "extra": report.extra} if report else None,
        # por arquivo, na mesma ordem de "files" (não depende do nome: num lote, vários alunos
        # mandam arquivos com o mesmo nome)
        "file_info": [{"mult": int(_get(multipliers, f, 1)), "material": _get(materials, f, ""),
                       "tag": _get(tags, f, ""), "units": _get(file_units, f, None),
                       "sha256": _get(source_hashes, f, source_hash(f))} for f in files],
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, allow_nan=False)
    replace_file(tmp, path)


def load_project(path: str) -> Project:
    try:
        return _load_project(path)
    except ProjectError:
        raise
    except Exception as e:
        raise ProjectError(f"Projeto inválido ou incompleto: {e}") from e


def _load_project(path: str) -> Project:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise ProjectError(f"Não foi possível abrir o projeto: {e}") from e
    if data.get("format") != FORMAT:
        raise ProjectError("Este arquivo não é um projeto do Sindri.")
    version = data.get("version", 1)
    if type(version) is not int or not 1 <= version <= VERSION:
        raise ProjectError("Versão de projeto não suportada. Atualize o Sindri.")
    portable = version >= 2 and isinstance(data.get("geometry"), list)
    if version >= 2 and not portable:
        raise ProjectError("O projeto está sem sua geometria incorporada.")
    base = os.path.dirname(os.path.abspath(path))
    files = []
    warnings = []
    for i, fabs in enumerate(data.get("files", [])):
        rel = data.get("files_rel", [None] * len(data["files"]))[i]
        cand = [fabs] + ([os.path.join(base, rel)] if rel else []) + [os.path.join(base, os.path.basename(fabs))]
        found = next((c for c in cand if os.path.isfile(c)), None)
        if found is None and not portable:
            raise ProjectError(f"Arquivo de origem não encontrado: {os.path.basename(fabs)}. "
                               "Coloque-o na mesma pasta do projeto.")
        files.append(found or fabs)
    params = NestParams.from_json(data.get("params", {}))
    multipliers, materials, tags, units, hashes = {}, {}, {}, {}, {}
    info = data.get("file_info")
    if isinstance(info, list) and len(info) == len(files):
        for f, fi in zip(files, info):
            k = os.path.abspath(f)
            if int(fi.get("mult", 1)) != 1:
                multipliers[k] = int(fi["mult"])
            if fi.get("material"):
                materials[k] = fi["material"]
            if fi.get("tag"):
                tags[k] = fi["tag"]
            if fi.get("units") is not None:
                units[k] = int(fi["units"])
            hashes[k] = fi.get("sha256")
            if portable and (not os.path.isfile(f) or (hashes[k] and source_hash(f) != hashes[k])):
                warnings.append(f"{os.path.basename(f)}: origem ausente ou alterada; usando o desenho incorporado ao projeto.")
    else:                                   # projetos antigos: caminho exato, ou nome se for único
        def match(rel):
            p = os.path.abspath(os.path.join(base, rel))
            exact = [f for f in files if os.path.abspath(f) == p]
            if exact:
                return exact[0]
            same = [f for f in files if os.path.basename(f) == os.path.basename(p)]
            return same[0] if len(same) == 1 else None
        for key, dest, conv in (("multipliers", multipliers, int), ("materials", materials, str),
                                ("tags", tags, str)):
            for rel, v in data.get(key, []) or []:
                f = match(rel)
                if f:
                    dest[os.path.abspath(f)] = conv(v)
    if portable:
        parts = [_part_from_json(p) for p in data["geometry"]]
        if len({p.id for p in parts}) != len(parts):
            raise ProjectError("Identificadores de peça repetidos.")
        paths = dict(zip(data["files"], files))
        for p in parts:
            p.source_file = paths.get(p.source_file, p.source_file)
        report = ImportReport(parts, [(p, False) for part in parts for p in part.prims], [], files)
        saved_report = data.get("import_report")
        if saved_report:
            report.preview = [(Prim.from_json(p), bool(issue)) for p, issue in saved_report.get("preview", [])]
            report.warnings = list(saved_report.get("warnings", []))
            report.unit_notes = {paths.get(k, k): v for k, v in saved_report.get("unit_notes", {}).items()}
            report.extra = dict(saved_report.get("extra", {}))
    else:
        report = import_files(files, params.join_tolerance, params.curve_tolerance, **params.import_kwargs(),
                              multipliers=multipliers, file_materials=materials, file_tags=tags, file_units=units)
    saved = data.get("parts", {})
    for p in report.parts:
        s = saved.get(p.id)
        if s is None:
            continue
        if abs(s.get("area", p.area) - p.area) > max(1.0, 0.01 * p.area):
            warnings.append("Os arquivos de origem mudaram desde que o projeto foi salvo.")
        p.quantity = int(s.get("quantity", p.quantity))
        if type(s.get("quantity", p.quantity)) is not int or p.quantity < 0:
            raise ProjectError("Quantidade inválida no projeto.")
        p.rotation_locked = bool(s.get("rotation_locked", False))
    if set(saved) != {p.id for p in report.parts}:
        warnings.append("As peças dos arquivos de origem não correspondem exatamente ao projeto salvo.")
    result = NestResult.from_json(data["result"]) if data.get("result") else None
    if result is not None:
        ids = {p.id for p in report.parts}
        if any(pl.part_id not in ids for pl in result.placements):
            warnings.append("O resultado salvo não corresponde às peças atuais e foi descartado.")
            result = None
        elif not portable:
            warnings.append("Projeto antigo: o encaixe foi descartado porque não há identidade verificável dos desenhos. Execute Encaixar novamente.")
            result = None
        else:
            from .validate import validate_layout
            issues = validate_layout({p.id: p for p in report.parts}, result.placements, params)
            if issues:
                warnings.append("Encaixe salvo inválido; execute Encaixar novamente. " + issues[0])
                result = None
    return Project(files, params, report.parts, result, report, sorted(set(warnings)), multipliers,
                   data.get("label"), materials, data.get("request"), tags,
                   (data.get("checklist") or {}) if result else {}, units, hashes,
                   data.get("extra") if isinstance(data.get("extra"), dict) else {})
