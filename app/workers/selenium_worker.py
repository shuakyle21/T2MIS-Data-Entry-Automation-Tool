"""QThread worker that runs the Selenium fill loop off the GUI thread.

Protocol (mirrors the CLI script):
- Worker opens one Chrome session, navigates to T2MIS home, emits
  login_required, and blocks until the UI calls confirm_login().
- For each pending row it fills the form (fill-only, never submits),
  emits row_filled, then blocks until the UI calls confirm_next()
  (user reviewed + submitted manually), retry_row(), or skip_row().
- Failures emit row_failed and block for retry/skip/stop.
- request_stop() halts cleanly; the driver always quits in the worker.
"""

import threading

from PySide6.QtCore import QThread, Signal

from app.services import automation, browser
from app.services.csv_loader import row_to_data


class SeleniumWorker(QThread):
    login_required = Signal()
    row_started = Signal(int)  # csv index
    row_filled = Signal(int, dict)  # csv index, fill result
    row_failed = Signal(int, str)  # csv index, error text
    log = Signal(str)
    finished = Signal(dict)  # {"ok": n, "failed": n, "stopped": bool}

    def __init__(self, pending, parent=None):
        """pending: list of (csv_index, row_dict_snapshot)."""
        super().__init__(parent)
        self._pending = list(pending)
        self._stop = False
        self._login_event = threading.Event()
        self._review_event = threading.Event()
        self._action = "next"  # next | retry | skip

    # ---- UI-thread controls ----
    def request_stop(self):
        self._stop = True
        self._login_event.set()
        self._review_event.set()

    def confirm_login(self):
        self._login_event.set()

    def confirm_next(self):
        self._action = "next"
        self._review_event.set()

    def retry_row(self):
        self._action = "retry"
        self._review_event.set()

    def skip_row(self):
        self._action = "skip"
        self._review_event.set()

    # ---- worker thread ----
    def run(self):
        ok = failed = 0
        stopped = False
        driver = None
        try:
            driver, wait = browser.create_driver()
            try:
                driver.get(automation.HOME_URL)
            except Exception as e:
                self.log.emit(f"Could not open T2MIS home: {e}")
            self.log.emit("Log in to T2MIS in the opened Chrome window, then press Continue.")
            self.login_required.emit()
            self._login_event.wait()
            if self._stop:
                stopped = True
            else:
                i = 0
                while i < len(self._pending):
                    if self._stop:
                        stopped = True
                        break
                    idx, row = self._pending[i]
                    data = row_to_data(row)
                    self.log.emit(
                        f"--- Row {idx + 1}: {data['first_name']} {data['last_name']} ---"
                    )
                    self.row_started.emit(idx)
                    try:
                        result = automation.fill_registration(driver, wait, data)
                        ok += 1
                        self.row_filled.emit(idx, result)
                        self.log.emit(
                            f"Birth -> {result['birth']} | Present -> {result['present']} "
                            f"| Sex -> {result['sex']} | District -> {result['district']}"
                        )
                        if result.get("dob_mismatch"):
                            self.log.emit(result["dob_mismatch"])
                        self.log.emit("Review + submit manually, then press Next.")
                    except Exception as e:
                        failed += 1
                        self.row_failed.emit(idx, f"{type(e).__name__}: {e}")
                        self.log.emit(f"FAILED row {idx + 1}: {e}")
                    # Block until the user decides (Next / Retry / Skip / Stop).
                    self._review_event.clear()
                    while not self._review_event.wait(0.2):
                        if self._stop:
                            stopped = True
                            break
                    if self._stop:
                        stopped = True
                        break
                    action = self._action
                    self._action = "next"
                    if action == "retry":
                        continue  # same i
                    i += 1  # next or skip both advance; UI persists Done only on Next
        except Exception as e:
            self.log.emit(f"Worker error: {type(e).__name__}: {e}")
        finally:
            try:
                if driver is not None:
                    driver.quit()
            except Exception:
                pass
            self.finished.emit({"ok": ok, "failed": failed, "stopped": stopped})
