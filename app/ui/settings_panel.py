"""Painel de parâmetros da placa e do encaixe."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QSpinBox, QVBoxLayout, QWidget,
                               QPushButton, QScrollArea)
from ..core.geometry import aci_to_rgb

from ..core.models import NestParams

UNITS = [(-1, "Automática (do arquivo)"), (4, "Milímetros (mm)"), (5, "Centímetros (cm)"),
         (1, "Polegadas (pol)"), (6, "Metros (m)")]

ROTATION_CHOICES = [
    ("Não girar", 1, False),
    ("Só 0° e 180°", 2, False),
    ("90° (4 posições)", 4, False),
    ("45° (8 posições)", 8, False),
    ("Livre (passos de 15°)", 24, True),
]


class _Section(QFrame):
    """Cartão com cabeçalho (ícone + título) e corpo para um QFormLayout."""
    instances: list = []

    def __init__(self, title: str, icon_name: str):
        super().__init__()
        self.setObjectName("Card")
        self.icon_name = icon_name
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedSize(26, 26)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setObjectName("SectionIcon")
        t = QLabel(title)
        t.setObjectName("SectionHead")
        head.addWidget(self.icon)
        head.addWidget(t)
        head.addStretch(1)
        v.addLayout(head)
        self.body = QWidget()
        v.addWidget(self.body)
        _Section.instances.append(self)
        self.refresh_icon()

    def refresh_icon(self):
        from . import theme
        from .icons import pixmap
        self.icon.setPixmap(pixmap(self.icon_name, theme.tokens()["accent"], 15))


def _dspin(lo, hi, step, dec, suffix, tip):
    s = QDoubleSpinBox()
    s.setMinimumWidth(60)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setSuffix(suffix)
    s.setToolTip(tip)
    s.setKeyboardTracking(False)
    return s


class SettingsPanel(QWidget):
    paramsChanged = Signal()
    reimportNeeded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        from PySide6.QtCore import Qt
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 12, 12, 12)
        lay.setSpacing(4)
        title = QLabel("Parâmetros")
        title.setObjectName("SectionTitle")
        lay.addWidget(title)

        g0 = _Section("Arquivo DXF", "open")
        f0 = QFormLayout(g0.body)
        self.units = QComboBox()
        for code, name in UNITS:
            self.units.addItem(name, code)
        self.units.setToolTip("Unidade em que o desenho foi feito. Use 'Automática' para seguir o que o\n"
                              "arquivo declara. Se as peças aparecerem 10× ou 1000× maiores/menores,\n"
                              "escolha a unidade certa aqui (os arquivos são reprocessados).")
        f0.addRow("Unidade", self.units)
        self.ignore_text = QCheckBox("Ignorar textos (nomes e anotações)")
        self.ignore_text.setToolTip("Textos no DXF costumam ser o nome da peça ou anotações e não devem ser\n"
                                    "gravados. Desmarque se os textos fazem parte da gravação.")
        f0.addRow(self.ignore_text)
        lbl = QLabel("Camadas (desmarque o que não é para cortar):")
        self.layers_label = lbl
        lbl.setObjectName("Muted")
        lbl.setWordWrap(True)
        f0.addRow(lbl)
        self.layers = QListWidget()
        self.layers.setObjectName("LayerList")
        self.layers.setMaximumHeight(130)
        self.layers.setToolTip("Cada camada (cor) do DXF. Desmarque camadas de anotação, cotas, nomes etc.")
        self.layers.itemChanged.connect(lambda *_: self._layers_changed())
        self.layers.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        f0.addRow(self.layers)
        lay.addWidget(g0)

        g1 = _Section("Placa", "grid")
        f1 = QFormLayout(g1.body)
        self.w = _dspin(10, 5000, 10, 1, " mm", "Largura útil da placa de material (eixo X da máquina)")
        self.h = _dspin(10, 5000, 10, 1, " mm", "Altura da placa de material (eixo Y da máquina)")
        self.margin = _dspin(0, 200, 0.5, 1, " mm",
                             "Faixa perto da borda que não será usada (bordas danificadas, fixação da placa).")
        f1.addRow("Largura", self.w)
        f1.addRow("Altura", self.h)
        f1.addRow("Margem da borda", self.margin)
        lay.addWidget(g1)

        g2 = _Section("Encaixe", "layers")
        f2 = QFormLayout(g2.body)
        self.spacing = _dspin(0, 50, 0.5, 2, " mm",
                              "Distância mínima entre peças. Inclua a largura do corte do laser (kerf).\n"
                              "Muito pequeno pode queimar/derreter a borda vizinha.")
        self.rot = QComboBox()
        for name, _, _ in ROTATION_CHOICES:
            self.rot.addItem(name)
        self.rot.setToolTip("Quais rotações o programa pode usar. Mais opções = encaixe melhor, cálculo mais lento.\n"
                            "\"Livre\" testa 24 ângulos: use só quando 90° não der bom resultado.")
        self.mirror = QCheckBox("Permitir espelhar peças")
        self.mirror.setToolTip("Só ative se a peça puder ser virada (sem gravação de um lado só, sem chanfro).")
        self.pip = QCheckBox("Peças dentro de furos (part-in-part)")
        self.pip.setToolTip("Aproveita furos grandes de uma peça para encaixar peças pequenas dentro.")
        self.multi = QCheckBox("Abrir nova placa se não couber")
        self.multi.setToolTip("Quando as peças não cabem em uma placa, usa quantas forem necessárias.")
        f2.addRow("Espaço entre peças", self.spacing)
        f2.addRow("Rotações", self.rot)
        self.detail = QComboBox()
        for name in ("Preciso (mais lento)", "Equilibrado", "Rápido"):
            self.detail.addItem(name)
        self.detail.setToolTip(
            "Quanto detalhe do contorno é usado para calcular o encaixe (o arquivo exportado é sempre o original).\n"
            "Equilibrado: ignora dentes/rasgos estreitos da borda (até ~20 mm de abertura) — muito mais rápido.\n"
            "Preciso: usa cada dente do contorno; pode ficar muito lento em peças com encaixes tipo finger joint.")
        f2.addRow("Detalhe", self.detail)
        f2.addRow(self.mirror)
        f2.addRow(self.pip)
        f2.addRow(self.multi)
        lay.addWidget(g2)

        self.adv_btn = QPushButton("▸  Opções avançadas")
        self.adv_btn.setObjectName("ghost")
        self.adv_btn.setCheckable(True)
        self.adv_btn.setStyleSheet("text-align: left;")
        lay.addWidget(self.adv_btn)
        g3 = _Section("Avançado", "settings")
        self.adv_box = g3
        g3.setVisible(False)
        self.adv_btn.toggled.connect(lambda v: (g3.setVisible(v), self.adv_btn.setText(
            ("▾" if v else "▸") + "  Opções avançadas")))
        f3 = QFormLayout(g3.body)
        self.curve = _dspin(0.01, 2, 0.05, 2, " mm",
                            "Precisão usada no cálculo (não afeta o arquivo exportado, que mantém arcos e círculos).\n"
                            "Menor = mais preciso e mais lento.")
        self.join = _dspin(0.001, 5, 0.01, 3, " mm",
                           "Linhas cujas pontas estão mais próximas que isso são consideradas ligadas.\n"
                           "Aumente se o arquivo tiver contornos 'quase fechados'. Requer reprocessar.")
        self.pop = QSpinBox()
        self.pop.setRange(4, 200)
        self.pop.setToolTip("Quantas soluções o algoritmo testa por rodada. Maior = explora mais, cada rodada demora mais.")
        self.mut = _dspin(1, 80, 1, 0, " %", "Chance de alterar a ordem/rotação de cada peça a cada rodada.")
        self.stop_ni = QSpinBox()
        self.stop_ni.setRange(0, 3600)
        self.stop_ni.setSuffix(" s")
        self.stop_ni.setSingleStep(10)
        self.stop_ni.setSpecialValueText("nunca (até eu parar)")
        self.stop_ni.setToolTip("O encaixe para sozinho quando passa este tempo sem encontrar uma solução melhor.\n"
                                "0 = só para quando você clicar em Parar.")
        self.btn_reimport = QPushButton("Reprocessar arquivos")
        self.btn_reimport.setToolTip("Lê os DXF novamente com as tolerâncias acima.")
        self.btn_reimport.clicked.connect(self.reimportNeeded.emit)
        f3.addRow("Tolerância de curva", self.curve)
        f3.addRow("Tolerância de junção", self.join)
        f3.addRow("População", self.pop)
        f3.addRow("Mutação", self.mut)
        f3.addRow("Parar sozinho sem melhora", self.stop_ni)
        f3.addRow(self.btn_reimport)
        lay.addWidget(g3)
        lay.addStretch(1)

        hint = QLabel("Dica: passe o mouse sobre cada opção para ver a explicação.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        from PySide6.QtWidgets import QComboBox as _C, QFormLayout as _F
        for cb in (self.units, self.rot):
            cb.setSizeAdjustPolicy(_C.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
        for form in body.findChildren(_F):
            form.setFieldGrowthPolicy(_F.AllNonFixedFieldsGrow)
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
        for lab in body.findChildren(QLabel):
            if lab.buddy() is not None:
                lab.setObjectName("FieldLabel")
        emit = lambda *_: self.paramsChanged.emit()
        for wdg in (self.w, self.h, self.margin, self.spacing, self.curve, self.mut,
                    self.pop, self.stop_ni):
            wdg.valueChanged.connect(emit)
        self.rot.currentIndexChanged.connect(emit)
        self.detail.currentIndexChanged.connect(emit)
        for c in (self.mirror, self.pip, self.multi):
            c.toggled.connect(emit)
        self._excluded: list[str] = []
        self._filling = False
        self.set_layers({})
        self.units.currentIndexChanged.connect(lambda *_: self.reimportNeeded.emit())
        self.ignore_text.toggled.connect(lambda *_: self.reimportNeeded.emit())
        self._extra = {}
        self._excluded: list[str] = []
        self._filling = False

    def _layers_changed(self):
        if self._filling:
            return
        self._excluded = [self.layers.item(i).data(Qt.UserRole) for i in range(self.layers.count())
                          if self.layers.item(i).checkState() != Qt.Checked]
        self.reimportNeeded.emit()

    def set_layers(self, layers: dict):
        """Mostra as camadas encontradas (mantém as desmarcadas pelo usuário)."""
        self._filling = True
        self.layers.clear()
        names = sorted(set(layers) | set(self._excluded))
        for name in names:
            info = layers.get(name, {"color": 7, "count": 0, "texts": 0})
            pm = QPixmap(14, 14)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            c = aci_to_rgb(int(info["color"]))
            p.setBrush(QColor(*c) if c != (0, 0, 0) else QColor(140, 140, 140))
            p.setPen(Qt.NoPen)
            p.drawEllipse(1, 1, 12, 12)
            p.end()
            extra = f" · {info['texts']} texto(s)" if info.get("texts") else ""
            it = QListWidgetItem(QIcon(pm), f"{name}  ({info['count']}{extra})")
            it.setData(Qt.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if name in self._excluded else Qt.Checked)
            self.layers.addItem(it)
        self.layers.setFixedHeight(min(150, max(28, 26 * self.layers.count() + 6)))
        self.layers.setVisible(self.layers.count() > 0)
        self.layers_label.setVisible(self.layers.count() > 0)
        self._filling = False

    def reset_file_options(self):
        """Arquivo novo: volta unidade para automática e reativa todas as camadas."""
        self._excluded = []
        self.set_units(-1)

    def refresh_icons(self):
        for sec in self.findChildren(_Section):
            sec.refresh_icon()

    def set_units(self, code: int, emit: bool = False):
        idx = max(0, self.units.findData(int(code)))
        self.units.blockSignals(not emit)
        self.units.setCurrentIndex(idx)
        self.units.blockSignals(False)

    # ------------------------------------------------------------------
    def set_params(self, p: NestParams):
        widgets = [self.w, self.h, self.margin, self.spacing, self.curve, self.join, self.pop,
                   self.mut, self.stop_ni, self.rot, self.mirror, self.pip, self.multi]
        for wd in widgets:
            wd.blockSignals(True)
        self.w.setValue(p.sheet_width)
        self.h.setValue(p.sheet_height)
        self.margin.setValue(p.margin)
        self.spacing.setValue(p.spacing)
        self.curve.setValue(p.curve_tolerance)
        self.join.setValue(p.join_tolerance)
        self.pop.setValue(p.population)
        self.mut.setValue(p.mutation_rate * 100)
        self.stop_ni.setValue(int(getattr(p, "stop_after_seconds", 40)))
        idx = 2
        for i, (_, steps, free) in enumerate(ROTATION_CHOICES):
            if (free and p.free_rotation) or (not free and not p.free_rotation and steps == p.rotation_steps):
                idx = i
        self.rot.setCurrentIndex(idx)
        self.mirror.setChecked(p.allow_mirror)
        self.detail.blockSignals(True)
        self.detail.setCurrentIndex(max(0, min(2, int(p.detail))))
        self.detail.blockSignals(False)
        self.pip.setChecked(p.part_in_part)
        self.multi.setChecked(p.multi_sheet)
        self._extra = {"max_sheets": p.max_sheets}
        self.set_units(p.units_override)
        self.ignore_text.blockSignals(True)
        self.ignore_text.setChecked(p.ignore_text)
        self.ignore_text.blockSignals(False)
        self._excluded = list(p.excluded_layers)
        for wd in widgets:
            wd.blockSignals(False)

    def params(self) -> NestParams:
        _, steps, free = ROTATION_CHOICES[self.rot.currentIndex()]
        return NestParams(
            sheet_width=self.w.value(), sheet_height=self.h.value(), margin=self.margin.value(),
            spacing=self.spacing.value(), rotation_steps=steps, free_rotation=free,
            allow_mirror=self.mirror.isChecked(), part_in_part=self.pip.isChecked(),
            multi_sheet=self.multi.isChecked(), max_sheets=self._extra.get("max_sheets", 50),
            curve_tolerance=self.curve.value(), join_tolerance=self.join.value(),
            population=self.pop.value(), mutation_rate=self.mut.value() / 100.0,
            max_generations_without_improvement=0,
            stop_after_seconds=float(self.stop_ni.value()),
            units_override=int(self.units.currentData()),
            ignore_text=self.ignore_text.isChecked(), detail=self.detail.currentIndex(), excluded_layers=list(self._excluded))
