"""Modo bancada: tela cheia para a hora do corte.

Mostra só o que o técnico precisa ao lado da máquina — qual placa, de que material, o que conferir no
RDWorks, quanto tempo deve levar — e três botões grandes. Toda a lógica continua na janela principal;
aqui só se chama o que já existe (abrir a placa, marcar como cortada, ir para a próxima).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import laser
from ..core.machine_time import fmt_clock, fmt_duration


class BenchMode(QWidget):
    def __init__(self, window):
        super().__init__(window, Qt.Window)
        self.win = window
        self.setWindowTitle("Sindri — modo bancada")
        self.setObjectName("BenchMode")
        self.si: int | None = None

        self.title = QLabel()
        self.title.setObjectName("BenchTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.sub = QLabel()
        self.sub.setObjectName("BenchSub")
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setWordWrap(True)
        self.checks = QLabel()
        self.checks.setObjectName("BenchChecks")
        self.checks.setTextFormat(Qt.RichText)
        self.checks.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.clock = QLabel()
        self.clock.setObjectName("BenchClock")
        self.clock.setAlignment(Qt.AlignCenter)

        self.btn_open = QPushButton("Abrir no RDWorks")
        self.btn_cut = QPushButton("Cortada ✓")
        self.btn_next = QPushButton("Próxima placa")
        self.btn_exit = QPushButton("Sair (Esc)")
        self.btn_open.setObjectName("primary")
        self.btn_cut.setObjectName("success")
        for b in (self.btn_open, self.btn_cut, self.btn_next):
            b.setMinimumHeight(84)
            b.setCursor(Qt.PointingHandCursor)
        self.btn_exit.setObjectName("ghost")
        self.btn_open.clicked.connect(self.open_current)
        self.btn_cut.clicked.connect(self.mark_cut)
        self.btn_next.clicked.connect(self.go_next)
        self.btn_exit.clicked.connect(self.close)

        row = QHBoxLayout()
        row.setSpacing(16)
        for b in (self.btn_open, self.btn_cut, self.btn_next):
            row.addWidget(b, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 24)
        lay.setSpacing(14)
        lay.addWidget(self.btn_exit, 0, Qt.AlignRight)
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addWidget(self.clock)
        lay.addWidget(self.checks, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        lay.addLayout(row)
        self.setStyleSheet(
            "#BenchTitle { font-size: 34pt; font-weight: 600; }"
            "#BenchSub { font-size: 16pt; }"
            "#BenchClock { font-size: 26pt; font-weight: 600; }"
            "#BenchChecks { font-size: 13pt; }"
            "#BenchMode QPushButton { font-size: 16pt; }")

        QShortcut(QKeySequence("Esc"), self, activated=self.close)
        QShortcut(QKeySequence("F11"), self, activated=self.close)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ abrir / atualizar
    def open_fullscreen(self):
        self.refresh(self.win.next_uncut_sheet() if self._current() in self.win.cut_sheets else self._current())
        self.showFullScreen()
        self.timer.start()

    def _current(self) -> int:
        return self.win.canvas.current_sheet()

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)

    def refresh(self, si: int | None = None):
        w = self.win
        idx = w.sheet_index()
        if si is None:
            si = self.si if self.si in idx.ordered else w.next_uncut_sheet()
        self.si = si
        total = len(idx.ordered)
        left = [s for s in idx.ordered if s not in w.cut_sheets]
        if si is None or not total:
            self.title.setText("Todas as placas foram cortadas")
            self.sub.setText("Feche o modo bancada (Esc) para voltar ao encaixe.")
            self.checks.setText("")
            self.clock.setText("")
            for b in (self.btn_open, self.btn_cut, self.btn_next):
                b.setEnabled(False)
            return
        n = idx.number.get(si, si + 1)
        material = idx.material.get(si, "") or "sem material definido"
        self.title.setText(f"Placa {n} de {total} · {material}")
        pieces = sum(1 for pl in w.placements if pl.sheet_index == si)
        done = si in w.cut_sheets
        self.sub.setText(f"{pieces} peça(s) · " + ("já cortada" if done else f"faltam {len(left)} placa(s)"))
        try:
            plan = w._laser_export_plan({si})
            self.checks.setText("<b>Confira no RDWorks antes do Start</b><br>"
                                + laser.checklist_html(w._layer_checks(si, plan)))
            t = w._sheet_times(plan).get(si)
            self._estimate = float(t.seconds) if t else 0.0
        except Exception:                               # a conferência é informativa: nunca impede o corte
            from ..core.fileutil import log_exception
            log_exception("modo bancada: montar a conferência das camadas")
            self.checks.setText("")
            self._estimate = 0.0
        self.btn_open.setEnabled(not done)
        self.btn_cut.setEnabled(not done)
        self.btn_next.setEnabled(len(left) > (0 if done else 1))
        self._tick()

    def _tick(self):
        if self.si is None:
            return
        est = getattr(self, "_estimate", 0.0)
        text = f"Tempo estimado ~{fmt_duration(est)}" if est else ""
        elapsed = self.win.sheet_elapsed(self.si)
        if elapsed is not None and self.si not in self.win.cut_sheets:
            text = (text + " · " if text else "") + f"aberta há {fmt_clock(elapsed)}"
        self.clock.setText(text)

    # ------------------------------------------------------------------ botões
    def open_current(self):
        if self.si is not None:
            self.win.goto_sheet(self.si)
            self.win.open_sheet_in_rdworks(self.si)
            self.refresh(self.si)

    def mark_cut(self):
        if self.si is None:
            return
        self.win.goto_sheet(self.si)
        nxt = self.win.mark_cut_and_advance(self.si)
        self.refresh(nxt)

    def go_next(self):
        nxt = self.win.next_uncut_sheet(self.si)
        if nxt is not None and nxt != self.si:
            self.win.goto_sheet(nxt)
        self.refresh(nxt)
