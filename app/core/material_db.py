"""Banco de materiais do laboratório (sem Qt).

Um arquivo JSON versionado (``materiais.json``) que pode ficar numa pasta compartilhada do laboratório:
todos os PCs usam os mesmos parâmetros. Para cada material: espessura, chapa padrão, margem e espaçamento
recomendados, kerf medido, data do último teste e quem validou, flag "proibido" e, por camada de laser
(corte, vinco, gravação vetorial, raster), modo, velocidade, potência mín./máx., passadas e scan.

Campos de chapa com 0 significam "use o que está no painel" (assim o banco nunca muda o encaixe de surpresa).
Gravação: lê, aplica a mudança e grava de novo (o arquivo de outro PC não é sobrescrito às cegas), com
troca atômica do arquivo.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Callable, Optional

from .fileutil import replace_file
from .material_safety import normalize

SCHEMA = "sindri-materiais"
VERSION = 1
FILE_NAME = "materiais.json"
STALE_DAYS = 90               # teste mais velho que isso: refazer a grade (o tubo perde potência)
LAYER_OPS = ("corte", "vinco", "gravacao_vetorial", "gravacao_raster")
OP_KEYS = ("mode", "speed", "power", "power_min", "passes", "interval", "bidirectional", "air", "color")


class MaterialDBError(Exception):
    pass


@dataclass
class Material:
    name: str
    thickness: float = 0.0
    sheet_width: float = 0.0          # 0 = usar o tamanho do painel
    sheet_height: float = 0.0
    margin: float = -1.0              # < 0 = usar a do painel
    spacing: float = 0.0              # 0 = usar o do painel
    kerf: float = 0.0
    grain: bool = False               # tem veio: só 0° e 180°
    min_part: float = 10.0            # menor medida (mm) que não cai na colmeia
    forbidden: bool = False
    forbidden_reason: str = ""
    acrylic_type: str = ""            # "cast" | "extrudado"
    color: int = 0                    # cor do corte no modo "uma cor por material" (0 = automática)
    tested: str = ""                  # AAAA-MM-DD do último teste (grade de teste)
    validated_by: str = ""
    notes: str = ""
    ops: dict = field(default_factory=dict)   # {camada: {mode, speed, power, power_min, passes, …}}

    @staticmethod
    def from_json(d: dict) -> "Material":
        names = {f.name for f in fields(Material)}
        m = Material(str(d.get("name", "")).strip())
        for k, v in d.items():
            if k in names and k != "name":
                setattr(m, k, v)
        m.ops = {op: {k: v for k, v in (o or {}).items() if k in OP_KEYS}
                 for op, o in (d.get("ops") or {}).items() if op in LAYER_OPS}
        m.validate()
        return m

    def to_json(self) -> dict:
        return asdict(self)

    def validate(self) -> None:
        if not self.name:
            raise MaterialDBError("material sem nome")
        for k in ("thickness", "sheet_width", "sheet_height", "spacing", "kerf", "min_part"):
            v = getattr(self, k)
            if not isinstance(v, (int, float)) or v < 0 or v != v:
                raise MaterialDBError(f"{self.name}: {k} inválido")
        if not isinstance(self.margin, (int, float)) or self.margin != self.margin:
            raise MaterialDBError(f"{self.name}: margem inválida")
        if self.tested:
            try:
                _dt.date.fromisoformat(str(self.tested))
            except ValueError as e:
                raise MaterialDBError(f"{self.name}: data do teste inválida ({self.tested})") from e

    # ---- formato usado pela interface (o corte na raiz, as outras camadas em "ops")
    def laser_cfg(self) -> dict:
        out = dict(self.ops.get("corte") or {})
        if self.color:
            out["color"] = int(self.color)
        out["forbidden"] = bool(self.forbidden)
        out["forbidden_reason"] = self.forbidden_reason
        out["ops"] = {op: dict(o) for op, o in self.ops.items() if op != "corte"}
        return out

    def set_laser(self, op: str, values: dict) -> None:
        if op not in LAYER_OPS:
            raise MaterialDBError(f"camada desconhecida: {op}")
        if op == "corte" and "color" in values:
            self.color = int(values["color"] or 0)
            values = {k: v for k, v in values.items() if k != "color"}
        cur = dict(self.ops.get(op) or {})
        cur.update({k: v for k, v in values.items() if k in OP_KEYS})
        self.ops[op] = cur

    def nest_overrides(self) -> dict:
        """Só o que o material define (o resto fica com o painel)."""
        out: dict = {}
        if self.sheet_width > 0 and self.sheet_height > 0:
            out.update(sheet_width=float(self.sheet_width), sheet_height=float(self.sheet_height))
        if self.margin >= 0:
            out["margin"] = float(self.margin)
        if self.spacing > 0:
            out["spacing"] = float(self.spacing)
        if self.grain:
            out["grain"] = True
        return out

    def test_age_days(self, today: Optional[_dt.date] = None) -> Optional[int]:
        if not self.tested:
            return None
        return ((today or _dt.date.today()) - _dt.date.fromisoformat(str(self.tested))).days

    def is_stale(self, today: Optional[_dt.date] = None, days: int = STALE_DAYS) -> bool:
        age = self.test_age_days(today)
        return age is None or age > days


def key(name: str) -> str:
    """'MDF 3 mm' e 'mdf 3mm' são o mesmo material."""
    return re.sub(r"[\s_\-]+", "", normalize(name))


def seeds() -> list[Material]:
    """Pontos de partida (sem velocidade/potência: cada laboratório preenche depois da grade de teste)."""
    return [
        Material("MDF 3mm", thickness=3, min_part=10,
                 notes="Ponto de partida. Faça a grade de teste e preencha velocidade/potência."),
        Material("MDF 6mm", thickness=6, spacing=3, min_part=10,
                 ops={"corte": {"mode": "corte", "passes": 2}},
                 notes="Muitas vezes corta melhor em 2 passadas mais rápidas."),
        Material("Acrílico cast 3mm", thickness=3, spacing=3, acrylic_type="cast", min_part=10,
                 notes="Acrílico: espaçamento ≥ espessura para a ponte entre cortes não derreter."),
        Material("Compensado 3mm", thickness=3, grain=True, min_part=10, notes="Tem veio: só 0° e 180°."),
    ]


@dataclass
class MaterialDB:
    path: str
    materials: list[Material] = field(default_factory=list)
    mtime: float = 0.0

    # ------------------------------------------------------------------ leitura
    @staticmethod
    def load(path: str, create: bool = True, migrate: Optional[Callable[[], dict]] = None) -> "MaterialDB":
        """Lê o banco. Se não existir: cria com os pontos de partida (+ ``migrate()``: os valores que
        estavam salvos só neste PC). Arquivo inválido -> MaterialDBError (não é sobrescrito)."""
        if not os.path.isfile(path):
            db = MaterialDB(path, seeds())
            if migrate:
                for name, cfg in (migrate() or {}).items():
                    m = db.find(name) or db.add(Material(name))
                    _import_cfg(m, cfg or {})
            if create:
                try:
                    db.save(merge=False)
                except OSError:
                    pass
            return db
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, UnicodeError, json.JSONDecodeError) as e:
            raise MaterialDBError(f"não consegui ler o banco de materiais ({path}): {e}") from e
        if not isinstance(data, dict) or data.get("format") != SCHEMA:
            raise MaterialDBError(f"{path} não é um banco de materiais do Sindri")
        if int(data.get("version", 0)) > VERSION:
            raise MaterialDBError("banco de materiais de uma versão mais nova do Sindri: atualize o programa")
        mats = [Material.from_json(d) for d in data.get("materials", [])]
        names = [key(m.name) for m in mats]
        if len(set(names)) != len(names):
            raise MaterialDBError("banco de materiais com nomes repetidos")
        return MaterialDB(path, mats, os.path.getmtime(path))

    def find(self, name: str) -> Optional[Material]:
        k = key(name)
        return next((m for m in self.materials if key(m.name) == k), None) if k else None

    def names(self) -> list[str]:
        return [m.name for m in self.materials]

    # ------------------------------------------------------------------ gravação
    def add(self, m: Material) -> Material:
        m.validate()
        if self.find(m.name):
            raise MaterialDBError(f"já existe um material chamado {m.name}")
        self.materials.append(m)
        return m

    def to_json(self) -> dict:
        return {"format": SCHEMA, "version": VERSION,
                "materials": [m.to_json() for m in sorted(self.materials, key=lambda m: key(m.name))]}

    def save(self, merge: bool = True) -> None:
        """Grava de forma atômica. ``merge``: materiais que outro PC criou depois da nossa leitura são
        mantidos (os que nós temos prevalecem)."""
        if merge and os.path.isfile(self.path):
            try:
                other = MaterialDB.load(self.path, create=False)
                mine = {key(m.name) for m in self.materials}
                deleted = getattr(self, "_deleted", set())
                self.materials += [m for m in other.materials if key(m.name) not in mine
                                   and key(m.name) not in deleted]
            except MaterialDBError:
                pass
        for m in self.materials:
            m.validate()
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_json(), fh, ensure_ascii=False, indent=1)
        replace_file(tmp, self.path)
        self.mtime = os.path.getmtime(self.path)

    def update(self, name: str, change: Callable[[Material], None], create: bool = True) -> Material:
        """Relê o arquivo (outro PC pode ter mudado), aplica ``change`` no material e grava."""
        if os.path.isfile(self.path):
            fresh = MaterialDB.load(self.path, create=False)
            self.materials, self.mtime = fresh.materials, fresh.mtime
        m = self.find(name)
        if m is None:
            if not create:
                raise MaterialDBError(f"material não encontrado: {name}")
            m = self.add(Material(name.strip()))
        change(m)
        m.validate()
        self.save()
        return m

    def remove(self, name: str) -> None:
        k = key(name)
        self.materials = [m for m in self.materials if key(m.name) != k]
        self.__dict__.setdefault("_deleted", set()).add(k)
        self.save()

    # ------------------------------------------------------------------ uso
    def laser_cfg(self) -> dict:
        """{material: {"color", "speed", "power", …, "ops": {…}}} — o formato do painel do laser."""
        return {m.name: m.laser_cfg() for m in self.materials}

    def nest_overrides(self, materials) -> dict:
        """{material (como está nas peças): chapa/margem/espaçamento/veio} para NestParams.material_sheets."""
        out = {}
        for name in materials:
            m = self.find(name or "")
            if m is not None:
                o = m.nest_overrides()
                if o:
                    out[name] = o
        return out


def _import_cfg(m: Material, cfg: dict) -> None:
    """Valores antigos (QSettings: {"color", "speed", "power", "ops"}) para o banco."""
    corte = {k: cfg[k] for k in OP_KEYS if k in cfg and k != "color"}
    if corte:
        m.set_laser("corte", corte)
    if cfg.get("color"):
        m.color = int(cfg["color"])
    for op, o in (cfg.get("ops") or {}).items():
        if op in LAYER_OPS and isinstance(o, dict):
            m.set_laser(op, o)


def default_path() -> str:
    """Documentos/Sindri/materiais.json (ou a pasta de dados dos testes)."""
    from .intranet import default_base_folder
    return os.path.join(os.path.dirname(default_base_folder()), FILE_NAME)
