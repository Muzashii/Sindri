"""Run blocking data operations outside the GUI thread with modal progress.

Callers retain synchronous return values; the nested Qt event loop paints progress
and processes accessibility events. The owner cannot be closed or edited mid-task.
"""
import time
from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import QProgressDialog

class TaskProgress(QProgressDialog):
    def reject(self):
        pass

    def closeEvent(self, event):
        event.ignore()


class TaskThread(QThread):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation()
        except Exception as error:
            self.error = error


def run_task(owner, label, operation):
    task = TaskThread(operation)
    progress = TaskProgress(label, "", 0, 0, owner)
    progress.setWindowTitle("Sindri · trabalho em andamento")
    progress.setCancelButton(None)
    progress.setWindowFlag(Qt.WindowCloseButtonHint, False)
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAccessibleName(label)
    started = time.perf_counter()
    timer = QTimer(progress)
    timer.timeout.connect(lambda: progress.setLabelText(f"{label} · {time.perf_counter() - started:.0f} s"))
    task.finished.connect(progress.accept)
    owner._ui_task_depth = getattr(owner, "_ui_task_depth", 0) + 1
    timer.start(250)
    task.start()
    try:
        progress.exec()
        # Escape may reject QProgressDialog; do not release snapshots while task is running.
        while task.isRunning():
            progress.exec()
        task.wait()
    finally:
        timer.stop()
        progress.close()
        owner._ui_task_depth -= 1
        task.deleteLater()
    if task.error is not None:
        raise task.error
    return task.result
