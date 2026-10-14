"""QThread worker that runs the Selenium fill/search loop off the GUI thread.

Protocol (mirrors the CLI script):
- Worker opens one Chrome session, navigates to T2MIS home, emits
  login_required, and blocks until the UI calls confirm_login().
- Stage 1 (encode): fills the registration form (fill-only, never submits),
  emits row_filled, then blocks until the UI calls confirm_next()
  (user reviewed + submitted manually), retry_row(), or skip_row().
- Stage 2 (enroll, manual-schedule mode): after login the worker emits
  schedule_required and blocks until the operator has opened the training
  schedule's Search Student page in Chrome and the UI calls
  confirm_schedule(). The worker validates the search form is showing,
  reads the actual SchSchedId from the form, then per row:
  (a) fills LearnerId + clicks Search, emits row_filled, blocks;
  (b) after the operator clicks Enroll in Chrome and the UI calls
  confirm_fill(), autofills the details form (fill-only, never Creates),
  emits row_filled again, and blocks for Next/Retry/Skip.
- Failures emit row_failed and block for retry/skip/stop.
- request_stop() halts cleanly; the driver always quits in the worker.
"""

import threading

from PySide6.QtCore import QThread, Signal
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.services import automation, browser
from app.services.csv_loader import (
    row_to_data,
    row_to_enroll_data,
    row_to_enroll_details,
)


class SeleniumWorker(QThread):
    login_required = Signal()
    schedule_required = Signal()  # Stage 2: operator must open the schedule page
    row_started = Signal(int)  # csv index
    row_filled = Signal(int, dict)  # csv index, fill result
    row_failed = Signal(int, str)  # csv index, error text
    log = Signal(str)
    finished = Signal(dict)  # {"ok": n, "failed": n, "stopped": bool}

    def __init__(self, pending, parent=None, mode="encode", schedule_id=""):
        """pending: list of (csv_index, row_dict_snapshot).

        mode: "encode" (Stage 1 fill) or "enroll" (Stage 2 search).
        schedule_id: default SchSchedId for enroll rows without override.
        """
        super().__init__(parent)
        self._pending = list(pending)
        self._mode = mode
        self._schedule_id = str(schedule_id or "").strip()
        self._actual_sched = None  # Stage 2: SchSchedId read from the open form
        self._stop = False
        self._login_event = threading.Event()
        self._schedule_event = threading.Event()
        self._review_event = threading.Event()
        self._action = "next"  # next | retry | skip

    # ---- UI-thread controls ----
    def request_stop(self):
        self._stop = True
        self._login_event.set()
        self._schedule_event.set()
        self._review_event.set()

    def confirm_login(self):
        self._login_event.set()

    def confirm_schedule(self):
        self._schedule_event.set()

    def confirm_next(self):
        self._action = "next"
        self._review_event.set()

    def confirm_fill(self):
        """Stage 2: operator clicked Enroll; autofill the details form."""
        self._action = "fill"
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
            elif self._mode == "enroll":
                if not self._wait_for_schedule(driver, wait):
                    stopped = True
                else:
                    ok, failed = self._run_rows(driver, wait)
                    if self._stop:
                        stopped = True
            else:
                ok, failed = self._run_rows(driver, wait)
                if self._stop:
                    stopped = True
        except Exception as e:
            self.log.emit(f"Worker error: {type(e).__name__}: {e}")
        finally:
            try:
                if driver is not None:
                    driver.quit()
            except Exception:
                pass
            self.finished.emit({"ok": ok, "failed": failed, "stopped": stopped})

    # ---- helpers (worker thread) ----
    def _wait_for_schedule(self, driver, wait):
        """Block until the operator opens the schedule search form.

        Returns True once the Search Student form is showing; False on stop.
        The form's hidden SchSchedId becomes the run's source of truth.
        """
        while not self._stop:
            self.log.emit(
                "In Chrome, open the training schedule (Register → schedule → "
                "Enroll) until the Search Student form is showing, then press Continue."
            )
            self.schedule_required.emit()
            self._schedule_event.clear()
            while not self._schedule_event.wait(0.2):
                if self._stop:
                    return False
            if self._stop:
                return False
            try:
                WebDriverWait(driver, 3, poll_frequency=0.1).until(
                    EC.visibility_of_element_located(
                        (By.XPATH, automation.LEARNER_ID_XPATH)
                    )
                )
            except Exception:
                self.log.emit(
                    "Search Student form not found — navigate to the schedule's "
                    "Enroll page and press Continue again."
                )
                continue
            actual = automation.read_form_schedule(driver)
            self._actual_sched = actual or self._schedule_id
            self.log.emit(
                f"Schedule selected (SchSchedId={self._actual_sched}). "
                "Starting learner search — automation continues from here."
            )
            return True
        return False

    def _await_decision(self):
        """Block for Next / Fill / Retry / Skip / Stop. Returns action or None."""
        self._review_event.clear()
        while not self._review_event.wait(0.2):
            if self._stop:
                return None
        if self._stop:
            return None
        action = self._action
        self._action = "next"
        return action

    def _run_rows(self, driver, wait):
        """Fill/search loop shared by both modes. Returns (ok, failed)."""
        ok = failed = 0
        i = 0
        while i < len(self._pending):
            if self._stop:
                break
            idx, row = self._pending[i]
            if self._mode == "enroll":
                action = self._run_enroll_row(driver, wait, idx, row)
                if action is None:
                    break
                if action == "ok":
                    ok += 1
                elif action == "failed":
                    failed += 1
                elif action == "retry":
                    continue
                i += 1
            else:
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
                action = self._await_decision()
                if action is None:
                    break
                if action == "retry":
                    continue  # same i
                i += 1  # next or skip both advance; UI persists flags only on Next
        return ok, failed

    def _run_enroll_row(self, driver, wait, idx, row):
        """Two-phase enroll for one row.

        Returns "ok" (advanced), "failed" (advanced), "retry" (same row),
        or None (stop). ok/failed count the row once, at search time.
        """
        enroll = row_to_enroll_data(row, self._actual_sched or self._schedule_id)
        override = enroll["sched_override"]
        if override and self._actual_sched and override != self._actual_sched:
            msg = (
                f"Row targets schedule {override} but the open "
                f"schedule is {self._actual_sched}."
            )
            self.row_failed.emit(idx, msg)
            self.log.emit(f"FAILED row {idx + 1}: {msg}")
            action = self._await_decision()
            if action is None:
                return None
            return "retry" if action == "retry" else "failed"
        label = enroll["display_name"] or enroll["learner_id"]
        self.log.emit(f"--- Row {idx + 1}: {label} (ID {enroll['learner_id']}) ---")
        self.row_started.emit(idx)
        try:
            result = automation.search_learner_for_enroll(
                driver,
                wait,
                enroll["learner_id"],
                self._actual_sched or enroll["sched_id"],
                navigate=False,
            )
        except Exception as e:
            self.row_failed.emit(idx, f"{type(e).__name__}: {e}")
            self.log.emit(f"FAILED row {idx + 1}: {e}")
            action = self._await_decision()
            if action is None:
                return None
            return "retry" if action == "retry" else "failed"
        self.row_filled.emit(idx, result)
        self.log.emit(
            f"Searched ID {result['learner_id']} on "
            f"schedule {self._actual_sched or enroll['sched_id']}: "
            f"{'match found — verify + click Enroll in Chrome, then Fill Form' if result['enroll_ready'] else 'NO match — check ID/schedule'}"
        )
        action = self._await_decision()
        if action is None:
            return None
        if action == "retry":
            return "retry"
        if action == "fill":
            if not result.get("enroll_ready"):
                self.log.emit("No match to fill from — search again or Skip.")
                return "failed"
            try:
                details = automation.fill_enroll_details(
                    driver,
                    wait,
                    row_to_enroll_details(row),
                    expected_sched=self._actual_sched or enroll["sched_id"],
                    expected_uli_id=result.get("uli_id"),
                )
            except Exception as e:
                self.row_failed.emit(idx, f"{type(e).__name__}: {e}")
                self.log.emit(f"FAILED row {idx + 1}: {e}")
                action = self._await_decision()
                if action is None:
                    return None
                return "retry" if action == "retry" else "failed"
            self.row_filled.emit(idx, details)
            self.log.emit(f"Details filled: {details['set']}")
            if details["manual"]:
                self.log.emit(f"Left for manual entry: {details['manual']}")
            self.log.emit("Review + click Create manually, then press Next.")
            action = self._await_decision()
            if action is None:
                return None
            return "retry" if action == "retry" else "ok"
        # next or skip: UI persists Enrolled only on Next.
        return "ok" if action == "next" else "failed"
