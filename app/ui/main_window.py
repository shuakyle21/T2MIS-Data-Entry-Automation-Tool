"""Main window: upload masterlist, preview sample data, run controls, log."""

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QStyle,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services import attendance, csv_loader
from app.services.automation import DEFAULT_SCHED_ID
from app.services.csv_loader import (
    is_done,
    is_enrolled,
    mark_done,
    mark_enrolled,
    preview_columns,
    row_learner_id,
    row_to_enroll_data,
)
from app.ui.styles import APP_QSS
from app.workers.selenium_worker import SeleniumWorker

STATUS_COL = "Status"
STAGE_ENCODE = "Stage 1 — Encoding"
STAGE_ENROLL = "Stage 2 — Enrollment"
STAGE_ATTEND = "Stage 3 — Attendance"

ATTEND_TEMPLATE = Path(__file__).resolve().parents[2] / "attendance_template.docx"
WEEKDAY_LABELS = ["M", "T", "W", "Th", "F", "Sat", "Sun"]

STAGE_HINTS = {
    STAGE_ENCODE: "Fill registration forms row by row. You review + submit in Chrome.",
    STAGE_ENROLL: "Search learners by ID, click Enroll in Chrome, then Fill Form.",
    STAGE_ATTEND: "Offline batch: generate attendance DOCX from an enrollees CSV.",
}

COLOR_PENDING = QColor("#ffffff")
COLOR_FILLING = QColor("#dbeafe")
COLOR_DONE = QColor("#dcfce7")
COLOR_FAILED = QColor("#fee2e2")


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(10)
    return frame, lay


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("T2MIS Encoder")
        self.resize(1140, 800)
        self.setMinimumSize(820, 600)
        self.setStyleSheet(APP_QSS)

        self._path = None
        self._fieldnames = []
        self._rows = []  # full csv rows (list of dict)
        self._model = QStandardItemModel(self)
        self._idx_to_model_row = {}  # csv idx -> model row (preview only)
        self._status_col = 0
        self._worker = None
        self._awaiting_idx = None
        self._awaiting_ok = False
        self._awaiting_phase = ""  # Stage 2 result phase: search | details
        self._awaiting_ready = False  # Stage 2 search produced a match
        self._total_pending = 0
        self._done_count = 0

        root = QWidget(self)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(16, 14, 16, 12)
        root_lay.setSpacing(12)

        # --- header ---
        header = QHBoxLayout()
        header.setSpacing(12)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("T2MIS Encoder")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Assisted T2MIS data entry · fill-only, you submit")
        subtitle.setObjectName("AppSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)

        header.addWidget(QLabel("Stage:"))
        self.stage_box = QComboBox()
        self.stage_box.addItems([STAGE_ENCODE, STAGE_ENROLL, STAGE_ATTEND])
        self.stage_box.setMinimumWidth(210)
        self.stage_box.currentTextChanged.connect(self.on_stage_changed)
        header.addWidget(self.stage_box)
        self.stage_hint = QLabel(STAGE_HINTS[STAGE_ENCODE])
        self.stage_hint.setObjectName("AppSubtitle")
        self.stage_hint.setWordWrap(True)
        self.stage_hint.setMinimumWidth(0)
        self.stage_hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        header.addWidget(self.stage_hint, 2)
        root_lay.addLayout(header)

        # --- file card ---
        file_card, file_lay = _card()
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        file_row.addWidget(QLabel("Schedule:"))
        self.sched_edit = QLineEdit(DEFAULT_SCHED_ID)
        self.sched_edit.setPlaceholderText("SchSchedId")
        self.sched_edit.setMaximumWidth(150)
        self.sched_edit.setToolTip(
            "Default training schedule ID (per-row SchSchedId overrides this)."
        )
        file_row.addWidget(self.sched_edit)
        self.open_btn = QPushButton("  Open CSV")
        self.open_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogOpenButton))
        self.open_btn.setShortcut("Ctrl+O")
        self.open_btn.setToolTip("Open a CSV masterlist (Ctrl+O)")
        self.open_btn.clicked.connect(self.open_csv)
        file_row.addWidget(self.open_btn)
        self.file_label = QLabel("No file loaded.")
        self.file_label.setObjectName("FilePath")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.file_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.file_label.setMinimumWidth(0)
        self.file_label.setWordWrap(False)
        self.file_label.setToolTip("Loaded CSV path")
        file_row.addWidget(self.file_label, 1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("CountBadge")
        self.count_label.setVisible(False)
        file_row.addWidget(self.count_label)
        file_lay.addLayout(file_row)
        root_lay.addWidget(file_card)

        # --- main splitter: preview (top) + log (bottom) ---
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        splitter.setOpaqueResize(True)

        # Preview card
        preview_card, preview_lay = _card()
        preview_card.setMinimumHeight(220)
        preview_head = QHBoxLayout()
        preview_title = QLabel("PREVIEW · FIRST 500 ROWS")
        preview_title.setObjectName("SectionTitle")
        preview_head.addWidget(preview_title)
        preview_head.addStretch(1)
        self.preview_meta = QLabel("")
        self.preview_meta.setObjectName("AppSubtitle")
        preview_head.addWidget(self.preview_meta)
        preview_lay.addLayout(preview_head)

        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.horizontalHeader().setMinimumSectionSize(60)
        self.table.horizontalHeader().setStretchLastSection(True)
        preview_lay.addWidget(self.table)
        splitter.addWidget(preview_card)

        # Log card
        log_card, log_lay = _card()
        log_card.setMinimumHeight(120)
        log_head = QHBoxLayout()
        log_title = QLabel("LOG")
        log_title.setObjectName("SectionTitle")
        log_head.addWidget(log_title)
        log_head.addStretch(1)
        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setFlat(False)
        self.clear_log_btn.setMaximumWidth(90)
        self.clear_log_btn.setToolTip("Clear the log view")
        self.clear_log_btn.clicked.connect(lambda: self.log_view.clear())
        log_head.addWidget(self.clear_log_btn)
        log_lay.addLayout(log_head)
        self.log_view = QTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(80)
        self.log_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log_view.setPlaceholderText("Run progress and automation messages appear here…")
        log_lay.addWidget(self.log_view)
        splitter.addWidget(log_card)

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        self._splitter = splitter
        QTimer.singleShot(0, lambda: splitter.setSizes([520, 180]))
        root_lay.addWidget(splitter, 1)

        # --- action card ---
        action_card, action_lay = _card()
        self.current_label = QLabel("Idle — open a CSV to begin.")
        self.current_label.setObjectName("StatusBanner")
        self.current_label.setProperty("kind", "idle")
        self.current_label.setWordWrap(True)
        action_lay.addWidget(self.current_label)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        self.start_btn = QPushButton(" Start")
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.start_btn.setToolTip("Start the browser run (opens Chrome for T2MIS login)")
        self.start_btn.clicked.connect(self.start_run)

        self.next_btn = QPushButton(" Next ✓")
        self.next_btn.setObjectName("SuccessButton")
        self.next_btn.setShortcut("Ctrl+Return")
        self.next_btn.setToolTip("You submitted in Chrome — flag done and advance (Ctrl+Enter)")
        self.next_btn.clicked.connect(self.on_next)

        self.fill_btn = QPushButton(" Fill Form")
        self.fill_btn.setObjectName("PrimaryButton")
        self.fill_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        self.fill_btn.setToolTip(
            "Stage 2: after clicking Enroll in Chrome, autofill the details form."
        )
        self.fill_btn.clicked.connect(self.on_fill)

        self.retry_btn = QPushButton(" Retry")
        self.retry_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.retry_btn.setToolTip("Retry the current row (F5)")
        self.retry_btn.setShortcut("F5")
        self.retry_btn.clicked.connect(self.on_retry)

        self.skip_btn = QPushButton(" Skip")
        self.skip_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipForward))
        self.skip_btn.setToolTip("Skip the current row without marking it done")
        self.skip_btn.clicked.connect(self.on_skip)

        self.stop_btn = QPushButton(" Stop")
        self.stop_btn.setObjectName("DangerButton")
        self.stop_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_btn.setToolTip("Stop after the current step")
        self.stop_btn.clicked.connect(self.on_stop)

        for b in (self.start_btn, self.next_btn, self.fill_btn,
                  self.retry_btn, self.skip_btn, self.stop_btn):
            b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            ctrl_row.addWidget(b)
        ctrl_row.addStretch(1)
        action_lay.addLayout(ctrl_row)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m")
        self.progress.setMinimumWidth(0)
        self.progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.progress.setMinimumHeight(16)
        progress_row.addWidget(self.progress, 1)
        action_lay.addLayout(progress_row)
        root_lay.addWidget(action_card)

        # --- attendance panel (Stage 3, offline batch generation) ---
        self.attend_panel, attend = _card()
        attend_title = QLabel("ATTENDANCE SHEETS · OFFLINE")
        attend_title.setObjectName("SectionTitle")
        attend.addWidget(attend_title)
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("School:"))
        self.school_edit = QLineEdit()
        self.school_edit.setPlaceholderText("Type school name — fills {INSERT SCHOOL}")
        self.school_edit.textChanged.connect(lambda _t: self._update_attendance_preview())
        type_row.addWidget(self.school_edit, 1)
        type_row.addWidget(QLabel("Program:"))
        self.program_edit = QLineEdit()
        self.program_edit.setPlaceholderText("Type program — fills {INSERT PROGRAM}")
        self.program_edit.textChanged.connect(lambda _t: self._update_attendance_preview())
        type_row.addWidget(self.program_edit, 1)
        attend.addLayout(type_row)
        date_row = QHBoxLayout()
        today = QDate.currentDate()
        date_row.addWidget(QLabel("Start:"))
        self.start_date = QDateEdit(today)
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("MMM d, yyyy")
        self.start_date.dateChanged.connect(lambda _d: self._update_attendance_preview())
        date_row.addWidget(self.start_date)
        date_row.addWidget(QLabel("End:"))
        self.end_date = QDateEdit(today)
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("MMM d, yyyy")
        self.end_date.dateChanged.connect(lambda _d: self._update_attendance_preview())
        date_row.addWidget(self.end_date)
        self.day_checks = []
        for label in WEEKDAY_LABELS:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            cb.stateChanged.connect(lambda _s: self._update_attendance_preview())
            self.day_checks.append(cb)
            date_row.addWidget(cb)
        date_row.addStretch(1)
        attend.addLayout(date_row)
        prev_row = QHBoxLayout()
        self.attend_preview = QLabel("No file loaded.")
        self.attend_preview.setObjectName("FilePath")
        self.attend_preview.setMinimumWidth(0)
        self.attend_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        prev_row.addWidget(self.attend_preview, 1)
        self.generate_btn = QPushButton(" Generate DOCX")
        self.generate_btn.setObjectName("PrimaryButton")
        self.generate_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.generate_btn.clicked.connect(self.on_generate_attendance)
        prev_row.addWidget(self.generate_btn)
        attend.addLayout(prev_row)
        root_lay.addWidget(self.attend_panel)
        self.attend_panel.setVisible(False)

        self.setCentralWidget(root)

        self.statusbar = QStatusBar()
        self.statusbar.showMessage("Ready.")
        self.setStatusBar(self.statusbar)
        self._refresh_buttons()

    # ---------- status banner ----------
    def _set_status(self, text, kind="idle"):
        self.current_label.setText(text)
        self.current_label.setProperty("kind", kind)
        # Repolish so the [kind=...] selector reapplies.
        self.current_label.style().unpolish(self.current_label)
        self.current_label.style().polish(self.current_label)
        self.statusbar.showMessage(text, 8000)

    # ---------- file handling ----------
    def _is_enroll(self):
        return self.stage_box.currentText() == STAGE_ENROLL

    def _is_attendance(self):
        return self.stage_box.currentText() == STAGE_ATTEND

    def _flag_done(self, row):
        if self._is_attendance():
            return False
        return is_enrolled(row) if self._is_enroll() else is_done(row)

    def on_stage_changed(self, text):
        is_enroll = self._is_enroll()
        is_attend = self._is_attendance()
        self.stage_hint.setText(STAGE_HINTS.get(text, ""))
        self.sched_edit.setEnabled(is_enroll and not is_attend)
        if is_attend:
            self.open_btn.setText("  Open Enrollees CSV")
        else:
            self.open_btn.setText(
                "  Open Enrollees CSV" if is_enroll else "  Open Masterlist CSV"
            )
        self.next_btn.setText(
            " Next ✓ (enrolled)" if is_enroll else " Next ✓ (done)"
        )
        # Loaded rows belong to the other stage; clear to avoid flag mixups.
        self._path = None
        self._fieldnames = []
        self._rows = []
        self._clear_awaiting()
        self._model.clear()
        self.file_label.setText("No file loaded.")
        self.count_label.setVisible(False)
        self.preview_meta.setText("")
        self.attend_panel.setVisible(is_attend)
        self._set_status("Idle — open a CSV to begin.", "idle")
        self._update_attendance_preview()
        self._refresh_buttons()

    def open_csv(self):
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.warning(self, "Busy", "Stop the current run before opening a file.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CSV", str(Path.cwd() / "data"), "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            if self._is_enroll():
                fieldnames, rows, _preview = csv_loader.load_enrollees(path)
            elif self._is_attendance():
                try:
                    fieldnames, rows, _preview = csv_loader.load_enrollees(path)
                except ValueError:
                    # Attendance only needs names; accept plain masterlists too.
                    fieldnames, rows, _preview = csv_loader.load_csv(path)
            else:
                fieldnames, rows, _preview = csv_loader.load_csv(path)
        except Exception as e:
            QMessageBox.critical(self, "Invalid file", str(e))
            return
        self._path = path
        self._fieldnames = fieldnames
        self._rows = rows
        self._clear_awaiting()
        self._populate_preview()
        self.file_label.setText(path)
        self.file_label.setToolTip(path)
        self._update_counts()
        self.log(f"Loaded {len(rows)} rows from {path}.")
        self._set_status(f"Loaded {len(rows)} rows — press Start.", "ok")
        self._update_attendance_preview()
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
                    text = "done" if self._flag_done(row) else "queued"
                else:
                    text = str(row.get(name, ""))
                item = QStandardItem(text)
                item.setEditable(False)
                if name == STATUS_COL:
                    item.setBackground(COLOR_DONE if self._flag_done(row) else COLOR_PENDING)
                self._model.setItem(model_row, col, item)
            self._idx_to_model_row[model_row] = model_row
        # Size once for content, then stay Interactive so window/splitter
        # resizes don't re-measure 500 rows (resize lag / freeze).
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        shown = min(len(self._rows), 500)
        self.preview_meta.setText(f"{shown} of {len(self._rows)} rows shown")

    def _update_counts(self):
        remaining = sum(1 for r in self._rows if not self._flag_done(r))
        if self._rows:
            self.count_label.setText(f"{len(self._rows)} rows · {remaining} remaining")
            self.count_label.setVisible(True)
        else:
            self.count_label.setVisible(False)

    # ---------- run control ----------
    def start_run(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if not self._rows:
            QMessageBox.information(self, "No file", "Open a CSV first.")
            return
        is_enroll = self._is_enroll()
        if is_enroll:
            pending = [
                (i, dict(r)) for i, r in enumerate(self._rows) if not is_enrolled(r)
            ]
            if not pending:
                QMessageBox.information(
                    self, "Nothing to do", "All rows are marked Enrolled."
                )
                return
            # Validate learner IDs upfront; the schedule comes from the
            # form the operator opens after login (manual-schedule mode).
            try:
                for _, r in pending:
                    row_learner_id(r)
            except Exception as e:
                QMessageBox.critical(self, "Invalid file", str(e))
                return
        else:
            pending = [(i, dict(r)) for i, r in enumerate(self._rows) if not is_done(r)]
            if not pending:
                QMessageBox.information(self, "Nothing to do", "All rows are marked Done.")
                return
        self._total_pending = len(pending)
        self._done_count = 0
        self.progress.setMaximum(self._total_pending)
        self.progress.setValue(0)
        if is_enroll:
            self._worker = SeleniumWorker(
                pending, mode="enroll", schedule_id=self.sched_edit.text().strip()
            )
        else:
            self._worker = SeleniumWorker(pending)
        self._worker.login_required.connect(self.on_login_required)
        self._worker.schedule_required.connect(self.on_schedule_required)
        self._worker.row_started.connect(self.on_row_started)
        self._worker.row_filled.connect(self.on_row_filled)
        self._worker.row_failed.connect(self.on_row_failed)
        self._worker.log.connect(self.log)
        self._worker.finished.connect(self.on_finished)
        self._worker.start()
        self._set_status("Starting browser — log in to T2MIS when Chrome opens.", "working")
        self._refresh_buttons()

    def on_login_required(self):
        box = QMessageBox(self)
        box.setWindowTitle("Log in to T2MIS")
        box.setText("Log in to T2MIS in the opened Chrome window, then press Continue.")
        box.addButton("Continue", QMessageBox.AcceptRole)
        box.exec()
        if self._worker is not None:
            self._worker.confirm_login()
            self._set_status("Logged in — filling rows.", "working")

    def on_schedule_required(self):
        box = QMessageBox(self)
        box.setWindowTitle("Open the training schedule")
        box.setText(
            "In Chrome, open the training schedule (Register → schedule → "
            "Enroll) until the Search Student form is showing, then press Continue. "
            "Automation takes over the learner search from there."
        )
        box.addButton("Continue", QMessageBox.AcceptRole)
        box.exec()
        if self._worker is not None:
            self._worker.confirm_schedule()
            self._set_status("Schedule selected — searching learners.", "working")

    def on_row_started(self, idx):
        self._clear_awaiting()
        row = self._rows[idx]
        if self._is_enroll():
            try:
                enroll = row_to_enroll_data(row, self.sched_edit.text())
                name = enroll["display_name"] or enroll["learner_id"]
                action = "Searching"
            except Exception:
                name, action = "", "Searching"
        else:
            name = f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip()
            action = "Filling"
        self._set_status(f"{action} row {idx + 1}/{len(self._rows)}: {name} …", "working")
        self._paint_status(idx, "filling", COLOR_FILLING)
        self._refresh_buttons()

    def on_row_filled(self, idx, result):
        self._awaiting_idx = idx
        self._awaiting_ok = True
        self._awaiting_phase = result.get("phase", "")
        self._awaiting_ready = bool(result.get("enroll_ready", True))
        self._paint_status(idx, "review", COLOR_FILLING)
        self.table.scrollTo(self._model.index(self._idx_to_model_row.get(idx, 0), 0))
        if self._is_enroll():
            if self._awaiting_phase == "details":
                self._set_status(
                    f"Row {idx + 1} details filled — review + click Create "
                    f"in Chrome, then press Next.",
                    "review",
                )
            elif not result.get("enroll_ready"):
                self._paint_status(idx, "no match", COLOR_FAILED)
                self._set_status(
                    f"Row {idx + 1}: no learner match — check ID, Retry, or Skip.",
                    "error",
                )
            else:
                self._set_status(
                    f"Row {idx + 1} found — verify + click Enroll in Chrome, "
                    f"then press Fill Form.",
                    "review",
                )
        else:
            self._set_status(
                f"Row {idx + 1} filled — review + submit in Chrome, then press Next.",
                "review",
            )
        self._refresh_buttons()

    def on_row_failed(self, idx, error):
        self._awaiting_idx = idx
        self._awaiting_ok = False
        self._paint_status(idx, "failed", COLOR_FAILED)
        self._set_status(f"Row {idx + 1} failed: {error} — Retry, Skip, or Stop.", "error")
        self._refresh_buttons()

    def on_next(self):
        """User reviewed + submitted manually: persist flag and advance."""
        if self._worker is None or self._awaiting_idx is None:
            return
        idx = self._awaiting_idx
        is_enroll = self._is_enroll()
        try:
            if is_enroll:
                mark_enrolled(self._path, self._fieldnames, self._rows, idx)
            else:
                mark_done(self._path, self._fieldnames, self._rows, idx)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not update flag: {e}")
            return
        self._done_count += 1
        self.progress.setValue(self._done_count)
        self._paint_status(idx, "done", COLOR_DONE)
        self._update_counts()
        self.log(f"Flagged row {idx + 1} {'Enrolled' if is_enroll else 'Done'}.")
        self._clear_awaiting()
        self._worker.confirm_next()
        self._set_status("Advancing …", "working")
        self._refresh_buttons()

    def on_retry(self):
        if self._worker is None or self._awaiting_idx is None:
            return
        idx = self._awaiting_idx
        self.log(f"Retrying row {idx + 1} …")
        self._clear_awaiting()
        self._worker.retry_row()
        self._refresh_buttons()

    def _clear_awaiting(self):
        self._awaiting_idx = None
        self._awaiting_ok = False
        self._awaiting_phase = ""
        self._awaiting_ready = False

    def on_fill(self):
        """Stage 2: operator clicked Enroll — autofill the details form."""
        if self._worker is None or self._awaiting_idx is None:
            return
        self.log(f"Filling details form for row {self._awaiting_idx + 1} …")
        self._clear_awaiting()
        self._worker.confirm_fill()
        self._set_status("Filling details form …", "working")
        self._refresh_buttons()

    def on_skip(self):
        if self._worker is None or self._awaiting_idx is None:
            return
        idx = self._awaiting_idx
        self.log(f"Skipped row {idx + 1} (not marked {'Enrolled' if self._is_enroll() else 'Done'}).")
        self._paint_status(idx, "queued", COLOR_PENDING)
        self._clear_awaiting()
        self._worker.skip_row()
        self._set_status("Advancing …", "working")
        self._refresh_buttons()

    def on_stop(self):
        if self._worker is not None and self._worker.isRunning():
            self.log("Stopping after current step …")
            self._worker.request_stop()
            self._set_status("Stopping …", "idle")

    def on_finished(self, summary):
        self.log(
            f"Finished: {summary.get('ok', 0)} filled, {summary.get('failed', 0)} failed"
            + (" (stopped)" if summary.get("stopped") else "")
        )
        self._set_status("Idle — run finished.", "ok")
        self._clear_awaiting()
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
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _running(self):
        return self._worker is not None and self._worker.isRunning()

    def _refresh_buttons(self):
        running = self._running()
        awaiting = running and self._awaiting_idx is not None
        has_pending = bool(self._rows) and any(not self._flag_done(r) for r in self._rows)
        can_fill = (
            awaiting
            and self._awaiting_ok
            and self._is_enroll()
            and self._awaiting_phase == "search"
            and self._awaiting_ready
        )
        is_attend = self._is_attendance()
        self.stage_box.setEnabled(not running)
        self.sched_edit.setEnabled(not running and self._is_enroll() and not is_attend)
        self.open_btn.setEnabled(not running)
        self.start_btn.setEnabled(not running and has_pending and not is_attend)
        self.fill_btn.setVisible(self._is_enroll())
        self.next_btn.setEnabled(bool(awaiting and self._awaiting_ok and not is_attend))
        self.fill_btn.setEnabled(bool(can_fill))
        self.retry_btn.setEnabled(bool(awaiting and not is_attend))
        self.skip_btn.setEnabled(bool(awaiting and not is_attend))
        self.stop_btn.setEnabled(running)
        # Guide the operator toward the next action.
        self.next_btn.setDefault(bool(self.next_btn.isEnabled()))
        if is_attend:
            self._update_attendance_preview()

    # ---------- attendance (Stage 3) ----------
    def _attendance_weekdays(self):
        return {i for i, cb in enumerate(self.day_checks) if cb.isChecked()}

    def _attendance_dates(self):
        s = self.start_date.date()
        e = self.end_date.date()
        start = date(s.year(), s.month(), s.day())
        end = date(e.year(), e.month(), e.day())
        return attendance.expand_dates(start, end, self._attendance_weekdays())

    def _update_attendance_preview(self):
        if not hasattr(self, "attend_preview"):
            return
        if not self._is_attendance():
            return
        names = attendance.names_from_rows(self._rows) if self._rows else []
        dates = self._attendance_dates()
        _days, hours, sheets = attendance.preview_counts(len(names), len(dates))
        if not self._rows:
            self.attend_preview.setText("Open an enrollees CSV to list students.")
        elif not dates:
            self.attend_preview.setText(
                f"{len(names)} students · 0 days — pick a range with a weekday checked."
            )
        else:
            first = attendance.format_date(dates[0])
            last = attendance.format_date(dates[-1])
            span = first if len(dates) == 1 else f"{first} – {last}"
            self.attend_preview.setText(
                f"{len(names)} students · {len(dates)} days ({span}) · "
                f"{hours} hours · {sheets} sheet(s) @ 8 hrs/day"
            )
        self.generate_btn.setEnabled(bool(names and dates))

    def on_generate_attendance(self):
        names = attendance.names_from_rows(self._rows)
        if not names:
            QMessageBox.information(self, "No file", "Open an enrollees CSV first.")
            return
        dates = self._attendance_dates()
        if not dates:
            QMessageBox.information(
                self, "No dates", "Pick a start/end range with at least one weekday checked."
            )
            return
        if not ATTEND_TEMPLATE.exists():
            QMessageBox.critical(
                self, "Template missing", f"Template not found:\n{ATTEND_TEMPLATE}"
            )
            return
        _days, hours, sheets = attendance.preview_counts(len(names), len(dates))
        s = self.start_date.date()
        e = self.end_date.date()
        default = str(
            Path.cwd()
            / f"attendance_{s.toString('yyyyMMdd')}-{e.toString('yyyyMMdd')}.docx"
        )
        out, _ = QFileDialog.getSaveFileName(
            self, "Save Attendance DOCX", default, "Word files (*.docx)"
        )
        if not out:
            return
        try:
            n_sheets, total_hours = attendance.generate_attendance(
                ATTEND_TEMPLATE,
                names,
                self.school_edit.text(),
                self.program_edit.text(),
                dates,
                out,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Generate failed", str(exc))
            return
        self.log(f"Wrote {n_sheets} sheet(s) ({total_hours} hrs) → {out}")
        QMessageBox.information(
            self,
            "Done",
            f"Wrote {n_sheets} sheet(s) ({total_hours} hours) across {len(dates)} day(s).\n{out}",
        )

    def closeEvent(self, event):
        if self._running():
            self._worker.request_stop()
            self._worker.wait(8000)
        super().closeEvent(event)
