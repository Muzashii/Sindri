"""Salvar/abrir projeto (.sindri — antes .dxfnest —, JSON)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .models import ImportReport, NestParams, NestResult, Part
from .part_builder import import_files

FORMAT = "dxfnest"
VERSION = 1


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


def _get(d: Optional[dict], f: str, default):
    if not d:
        return default
    return d.get(os.path.abspath(f), d.get(f, default))


def save_project(path: str, files: list[str], params: NestParams, parts: list[Part],
                 result: Optional[NestResult], multipliers: Optional[dict] = None,
                 label: Optional[str] = None, materials: Optional[dict] = None,
                 request: Optional[dict] = None, tags: Optional[dict] = None,
                 checklist: Optional[dict] = None) -> None:
    base = os.path.dirname(os.path.abspath(path))
    data = {
        "format": FORMAT,
        "version": VERSION,
        "files": [os.path.abspath(f) for f in files],
        "files_rel": [os.path.relpath(os.path.abspath(f), base) for f in files],
        "params": params.to_json(),
        "parts": {p.id: {"quantity": p.quantity, "rotation_locked": p.rotation_locked,
                         "area": round(p.area, 2)} for p in parts},
        "result": result.to_json() if result else None,
        "multipliers": [[os.path.relpath(os.path.abspath(k), base), int(v)]
                        for k, v in (multipliers or {}).items()],
        "label": label,
        "materials": [[os.path.relpath(os.path.abspath(k), base), v] for k, v in (materials or {}).items()],
        "request": request,
        "checklist": checklist or {},
        "tags": [[os.path.relpath(os.path.abspath(k), base), v] for k, v in (tags or {}).items()],
        # por arquivo, na mesma ordem de "files" (não depende do nome: num lote, vários alunos
        # mandam arquivos com o mesmo nome)
        "file_info": [{"mult": int(_get(multipliers, f, 1)), "material": _get(materials, f, ""),
                       "tag": _get(tags, f, "")} for f in files],
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_project(path: str) -> Project:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ProjectError(f"Não foi possível abrir o projeto: {e}") from e
    if data.get("format") != FORMAT:
        raise ProjectError("Este arquivo não é um projeto do Sindri.")
    base = os.path.dirname(os.path.abspath(path))
    files = []
    warnings = []
    for i, fabs in enumerate(data.get("files", [])):
        rel = data.get("files_rel", [None] * len(data["files"]))[i]
        cand = [fabs] + ([os.path.join(base, rel)] if rel else [])
        found = next((c for c in cand if os.path.isfile(c)), None)
        if found is None:
            raise ProjectError(f"Arquivo de origem não encontrado: {os.path.basename(fabs)}. "
                               "Coloque-o na mesma pasta do projeto.")
        files.append(found)
    params = NestParams.from_json(data.get("params", {}))
    multipliers, materials, tags = {}, {}, {}
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
    report = import_files(files, params.join_tolerance, params.curve_tolerance, **params.import_kwargs(),
                          multipliers=multipliers, file_materials=materials, file_tags=tags)
    saved = data.get("parts", {})
    for p in report.parts:
        s = saved.get(p.id)
        if s is None:
            continue
        if abs(s.get("area", p.area) - p.area) > max(1.0, 0.01 * p.area):
            warnings.append("Os arquivos de origem mudaram desde que o projeto foi salvo.")
        p.quantity = int(s.get("quantity", p.quantity))
        p.rotation_locked = bool(s.get("rotation_locked", False))
    if set(saved) != {p.id for p in report.parts}:
        warnings.append("As peças dos arquivos de origem não correspondem exatamente ao projeto salvo.")
    result = NestResult.from_json(data["result"]) if data.get("result") else None
    if result is not None:
        ids = {p.id for p in report.parts}
        if any(pl.part_id not in ids for pl in result.placements):
            warnings.append("O resultado salvo não corresponde às peças atuais e foi descartado.")
            result = None
    return Project(files, params, report.parts, result, report, sorted(set(warnings)), multipliers,
                   data.get("label"), materials, data.get("request"), tags, data.get("checklist") or {})
