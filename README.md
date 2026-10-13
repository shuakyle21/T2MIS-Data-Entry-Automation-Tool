# T2MIS Encoder

Assisted data-entry tool for the TESDA T2MIS learner registration portal.
It reads a verified masterlist CSV, fills the registration form row by row
with Selenium, and leaves review and submission to the operator.

Two front ends share one proven fill engine:

- **Desktop app** (`app/`, PySide6) — upload a masterlist, preview the data,
  then Start / Next / Retry / Skip / Stop through the queue.
- **CLI script** (`main.py`) — the original terminal-driven filler.

Fill-only by design: the tool never clicks `sendRegister`. Every row is
reviewed and submitted manually.

## Requirements

- macOS with Google Chrome installed
- Python 3.12 (managed with `uv`; see `pyproject.toml` / `uv.lock`)
- T2MIS account (login is manual — credentials are never stored)

Dependencies: `selenium` (chromedriver via Selenium Manager, nothing to
install), `PySide6` (desktop app only).

## Quickstart

```sh
uv sync
./run_app.sh            # desktop app — or: .venv/bin/python -m app.main
.venv/bin/python main.py  # CLI script
```

1. Place the verified masterlist at `data/registrants.csv`
   (schema reference: `data/registrants.sample.csv`).
2. Start the app or script and log in to T2MIS in the opened Chrome window.
3. The tool fills one pending row, you review and submit it in the browser,
   then confirm to advance. Progress persists per row in the `Done` column,
   so an interrupted run resumes where it left off.

Real masterlists contain learner PII and are git-ignored; only the redacted
sample is committed.

## How it works

| Step | Detail |
| :--- | :--- |
| Load | CSV parsed with `utf-8-sig`; required headers validated; `Done` flag honored case-insensitively |
| Fill | Text fields via `send_keys` + `input`/`change` dispatch; dropdowns by exact → token-weighted → `difflib` match |
| Cascades | `Region → Province → City` (birth and present address) plus `City → District`, each with explicit AJAX waits |
| Normalization | Dates zero-padded to `mm/dd/yyyy`; numeric districts mapped to Roman (`3 → III`) |
| Consent | `Allow=True` / `Continue=Yes` radios clicked natively so the page `check()` validator fires |
| Resilience | Stale-element retries, click-intercept fallback, per-row `Done=TRUE` persistence |

## Project structure

```
app/
  main.py                   # QApplication entry
  ui/main_window.py         # upload / preview / controls / log
  services/automation.py    # Selenium fill engine (Qt-free)
  services/csv_loader.py    # CSV load, validation, Done persistence
  services/browser.py       # webdriver factory
  workers/selenium_worker.py# QThread bridge (signals only, no widgets)
main.py                     # CLI filler (standalone)
data/registrants.sample.csv# schema reference (fictitious rows)
```

## Packaging

For a distributable `.app`, bundle with PyInstaller
(`--windowed --onedir`) and wrap in a DMG. Distribution outside the
App Store requires a Developer ID signature and notarization for Gatekeeper.
