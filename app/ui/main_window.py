"""Main window: upload masterlist, preview sample data, run controls, log."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services import csv_loader
from app.services.csv_loader import is_done, mark_done, preview_columns
from app.workers.selenium_worker import SeleniumWorker

STATUS_COL = "Status"

COLOR_PENDING = QColor("#ffffff")
COLOR_FILLING = QColor("#cfe2ff")
COLOR_DONE = QColor("#d4edda")
COLOR_FAILED = QColor("#f8d7da")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("T2MIS Encoder")
        self.resize(1100, 750)

        self._path = None
        self._fieldnames = []
        self._rows = []  # full csv rows (list of dict)
        self._model = QStandardItemModel(self)
        self._idx_to_model_row = {}  # csv idx -> model row (preview only)
        self._status_col = 0
        self._worker = None
        self._awaiting_idx = None  # csv idx currently awaiting review
        self._awaiting_ok = False
        self._total_pending = 0
        self._done_count = 0

        root = QWidget(self)
        layout = QVBoxLayout(root)

        # --- file row ---
        file_row = QHBoxLayout()
        self.open_btn = QPushButton("Open Masterlist CSV")
        self.open_btn.clicked.connect(self.open_csv)
        file_row.addWidget(self.open_btn)
        self.file_label = QLabel("No file loaded.")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        file_row.addWidget(self.file_label, 1)
        self.count_label = QLabel("")
        file_row.addWidget(self.count_label)
        layout.addLayout(file_row)

        # --- preview table ---
        layout.addWidget(QLabel("Preview (first 500 rows):"))
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        layout.addWidget(self.table, 3)

        # --- run controls ---
        ctrl_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start_run)
        self.next_btn = QPushButton("Next (mark done)")
        self.next_btn.clicked.connect(self.on_next)
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.clicked.connect(self.on_retry)
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.clicked.connect(self.on_skip)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.on_stop)
        for b in (self.start_btn, self.next_btn, self.retry_btn, self.skip_btn, self.stop_btn):
            ctrl_row.addWidget(b)
        self.progress = QProgressBar()
        ctrl_row.addWidget(self.progress, 1)
        layout.addLayout(ctrl_row)
        self.current_label = QLabel("Idle.")
        layout.addWidget(self.current_label)

        # --- log ---
        layout.addWidget(QLabel("Log:"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 2)

        self.setCentralWidget(root)
        self._refresh_buttons()

    # ---------- file handling ----------
    def open_csv(self):
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.warning(self, "Busy", "Stop the current run before opening a file.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Masterlist CSV", str(Path.cwd() / "data"), "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            fieldnames, rows, _preview = csv_loader.load_csv(path)
        except Exception as e:
            QMessageBox.critical(self, "Invalid file", str(e))
            return
        self._path = path
        self._fieldnames = fieldnames
        self._rows = rows
        self._awaiting_idx = None
        self._populate_preview()
        self.file_label.setText(path)
        self._update_counts()
        self.log(f"Loaded {len(rows)} rows from {path}.")
        self._refresh_buttons()

    def _populate_preview(self):
        cols = preview_columns(self._fieldnames) + [STATUS_COL]
        self._model.clear()
        self._model.setHorizontalHeaderLabels(cols)
        self._status_col = len(cols) - 1
        self._idx_to_model_row = {}
        limit = min(len(self._rows), 500)
        for model_row in range(limit):
            row = self._rows[model_row]
            for col, name in enumerate(cols):
                if name == STATUS_COL:
                    text = "done" if is_done(row) else "queued"
                else:
                    text = str(row.get(name, ""))
                item = QStandardItem(text)
                if name == STATUS_COL:
                    item.setBackground(COLOR_DONE if is_done(row) else COLOR_PENDING)
                self._model.setItem(model_row, col, item)
            self._idx_to_model_row[model_row] = model_row
        self.table.resizeColumnsToContents()

    def _update_counts(self):
        remaining = sum(1 for r in self._rows if not is_done(r))
        self.count_label.setText(f"{len(self._rows)} rows ({remaining} remaining)")

    # ---------- run control ----------
    def start_run(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._rows:
            QMessageBox.information(self, "No file", "Open a masterlist CSV first.")
            return
        pending = [(i, dict(r)) for i, r in enumerate(self._rows) if not is_done(r)]
        if not pending:
            QMessageBox.information(self, "Nothing to do", "All rows are marked Done.")
            return
        self._total_pending = len(pending)
        self._done_count = 0
        self.progress.setMaximum(self._total_pending)
        self.progress.setValue(0)
        self._worker = SeleniumWorker(pending)
        self._worker.login_required.connect(self.on_login_required)
        self._worker.row_started.connect(self.on_row_started)
        self._worker.row_filled.connect(self.on_row_filled)
        self._worker.row_failed.connect(self.on_row_failed)
        self._worker.log.connect(self.log)
        self._worker.finished.connect(self.on_finished)
        self._worker.start()
        self.current_label.setText("Starting browser — log in to T2MIS when Chrome opens.")
        self._refresh_buttons()

    def on_login_required(self):
        box = QMessageBox(self)
        box.setWindowTitle("Log in to T2MIS")
        box.setText("Log in to T2MIS in the opened Chrome window, then press Continue.")
        box.addButton("Continue", QMessageBox.AcceptRole)
        box.exec()
        if self._worker is not None:
            self._worker.confirm_login()
            self.current_label.setText("Logged in — filling rows.")

    def on_row_started(self, idx):
        self._awaiting_idx = None
        row = self._rows[idx]
        name = f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip()
        self.current_label.setText(f"Filling row {idx + 1}/{len(self._rows)}: {name} …")
        self._paint_status(idx, "filling", COLOR_FILLING)
        self._refresh_buttons()

    def on_row_filled(self, idx, result):
        self._awaiting_idx = idx
        self._awaiting_ok = True
        self._paint_status(idx, "review", COLOR_FILLING)
        self.table.scrollTo(self._model.index(self._idx_to_model_row.get(idx, 0), 0))
        self.current_label.setText(
            f"Row {idx + 1} filled — review + submit in Chrome, then press Next."
        )
        self._refresh_buttons()

    def on_row_failed(self, idx, error):
        self._awaiting_idx = idx
        self._awaiting_ok = False
        self._paint_status(idx, "failed", COLOR_FAILED)
        self.current_label.setText(f"Row {idx + 1} failed: {error} — Retry, Skip, or Stop.")
        self._refresh_buttons()

    def on_next(self):
        """User reviewed + submitted manually: persist Done and advance."""
        if self._worker is None or self._awaiting_idx is None:
            return
        idx = self._awaiting_idx
        try:
            mark_done(self._path, self._fieldnames, self._rows, idx)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not update Done flag: {e}")
            return
        self._done_count += 1
        self.progress.setValue(self._done_count)
        self._paint_status(idx, "done", COLOR_DONE)
        self._update_counts()
        self.log(f"Flagged row {idx + 1} Done.")
        self._awaiting_idx = None
        self._worker.confirm_next()
        self.current_label.setText("Advancing …")
        self._refresh_buttons()

    def on_retry(self):
        if self._worker is None or self._awaiting_idx is None:
            return
        idx = self._awaiting_idx
        self.log(f"Retrying row {idx + 1} …")
        self._awaiting_idx = None
        self._worker.retry_row()
        self._refresh_buttons()

    def on_skip(self):
        if self._worker is None or self._awaiting_idx is None:
            return
        idx = self._awaiting_idx
        self.log(f"Skipped row {idx + 1} (not marked Done).")
        self._paint_status(idx, "queued", COLOR_PENDING)
        self._awaiting_idx = None
        self._worker.skip_row()
        self.current_label.setText("Advancing …")
        self._refresh_buttons()

    def on_stop(self):
        if self._worker is not None and self._worker.isRunning():
            self.log("Stopping after current step …")
            self._worker.request_stop()
            self.current_label.setText("Stopping …")

    def on_finished(self, summary):
        self.log(
            f"Finished: {summary.get('ok', 0)} filled, {summary.get('failed', 0)} failed"
            + (" (stopped)" if summary.get("stopped") else "")
        )
        self.current_label.setText("Idle.")
        self._awaiting_idx = None
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._refresh_buttons()

    # ---------- helpers ----------
    def _paint_status(self, idx, text, color):
        model_row = self._idx_to_model_row.get(idx)
        if model_row is None:
            return
        item = self._model.item(model_row, self._status_col)
        if item is not None:
            item.setText(text)
            item.setBackground(color)

    def log(self, message):
        self.log_view.append(message)

    def _running(self):
        return self._worker is not None and self._worker.isRunning()

    def _refresh_buttons(self):
        running = self._running()
        awaiting = running and self._awaiting_idx is not None
        has_pending = bool(self._rows) and any(not is_done(r) for r in self._rows)
        self.open_btn.setEnabled(not running)
        self.start_btn.setEnabled(not running and has_pending)
        self.next_btn.setEnabled(bool(awaiting and self._awaiting_ok))
        self.retry_btn.setEnabled(bool(awaiting))
        self.skip_btn.setEnabled(bool(awaiting))
        self.stop_btn.setEnabled(running)

    def closeEvent(self, event):
        if self._running():
            self._worker.request_stop()
            self._worker.wait(8000)
        super().closeEvent(event)
