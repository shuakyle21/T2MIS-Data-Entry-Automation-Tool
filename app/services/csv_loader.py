"""CSV loading, header validation, and row normalization (Qt-free)."""

import csv
from datetime import datetime

REQUIRED_COLUMNS = {
    "Last Name",
    "First Name",
    "Birthdate",
    "Region",
    "Province",
    "City/Municipality",
    "Region - ADDRESS",
    "Province - ADDRESS",
    "City/Municipality - ADDRESS",
    "Sex",
}

OPTIONAL_COLUMNS = {
    "Middle Name",
    "Purok/Street/Block/Lot",
    "Barangay",
    "District",
    "Contact No.:",
}

DONE_VALUES = {"true", "1", "yes", "done", "x"}

# Stage 2 (enrollment) input: the learner key column may be labeled any of
# these depending on the Sheet export. First match wins.
ENROLL_ID_ALIASES = [
    "Learner's ID",
    "Learner ID",
    "LearnerId",
    "ULI",
    "ULI No",
    "ULI No.",
]

# Per-row schedule override columns (fallback: GUI schedule-ID textbox).
ENROLL_SCHED_ALIASES = ["SchSchedId", "Schedule ID", "ScheduleID"]

ENROLLED_COL = "Enrolled"

# Columns shown first in the preview table; the rest follow alphabetically.
PREVIEW_PRIORITY = [
    "Learner's ID",
    "ULI",
    "LearnerId",
    "Last Name",
    "First Name",
    "Middle Name",
    "Birthdate",
    "Sex",
    "Contact No.:",
    "SchSchedId",
    "Enrolled",
    "Purok/Street/Block/Lot",
    "Barangay",
    "District",
    "City/Municipality - ADDRESS",
    "Province - ADDRESS",
    "Region - ADDRESS",
    "City/Municipality",
    "Province",
    "Region",
    "Done",
]


def format_dob(raw):
    """Normalize '3/13/2009' -> '03/13/2009' for the mm/dd/yyyy field."""
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    parts = [p.strip() for p in raw.replace("-", "/").split("/")]
    if len(parts) == 3:
        m, d, y = parts
        return f"{int(m):02d}/{int(d):02d}/{y}"
    return raw


def row_to_data(row):
    """Map a CSV row dict to form fields (mirrors the proven script)."""
    return {
        "last_name": row["Last Name"].strip(),
        "first_name": row["First Name"].strip(),
        "middle_name": row.get("Middle Name", "").strip(),
        "dob": format_dob(row["Birthdate"]),
        "b_region": row["Region"].strip(),
        "b_province": row["Province"].strip(),
        "b_city": row["City/Municipality"].strip(),
        "region": row["Region - ADDRESS"].strip(),
        "province": row["Province - ADDRESS"].strip(),
        "city": row["City/Municipality - ADDRESS"].strip(),
        "sex": row["Sex"].strip().capitalize(),
        "mobile": row.get("Contact No.:", "").strip(),
        "street": row.get("Purok/Street/Block/Lot", "").strip(),
        "barangay": row.get("Barangay", "").strip(),
        "district": row.get("District", "").strip(),
        "allow": "True",
        "continue": "Yes",
    }


def is_done(row):
    """Check the Done flag column (case-insensitive)."""
    return row.get("Done", "").strip().lower() in DONE_VALUES


def is_enrolled(row):
    """Check the Enrolled flag column (case-insensitive)."""
    return row.get(ENROLLED_COL, "").strip().lower() in DONE_VALUES


def _resolve_alias(row, aliases):
    """Return the first non-empty value among alias column names."""
    for name in aliases:
        if name in row and str(row[name]).strip():
            return str(row[name]).strip()
    return ""


def row_learner_id(row):
    """Return the learner ID from alias columns; raise on missing."""
    learner_id = _resolve_alias(row, ENROLL_ID_ALIASES)
    if not learner_id:
        raise ValueError(
            f"Missing learner ID (looked for {ENROLL_ID_ALIASES}). "
            f"Available: {sorted(row.keys())}"
        )
    return learner_id


def row_to_enroll_details(row):
    """Map masterlist columns to the post-Enroll details form fields."""
    return {
        "civil": row.get("Civil Status", "").strip(),
        "educ": row.get("Educational Attainment", "").strip(),
        "client_type": row.get("Learner/Trainee/classification", "").strip(),
        "disability": row.get("Type of Disability", "").strip(),
        "emp_type": row.get("Employment Status", "").strip(),
    }


def row_to_enroll_data(row, default_sched_id=""):
    """Map an enrollee CSV row to {learner_id, sched_id, sched_override, ...}."""
    learner_id = row_learner_id(row)
    override = _resolve_alias(row, ENROLL_SCHED_ALIASES)
    sched_id = override or str(default_sched_id).strip()
    if not sched_id:
        raise ValueError("Missing schedule ID (textbox or SchSchedId column).")
    name = f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip()
    return {
        "learner_id": learner_id,
        "sched_id": sched_id,
        "sched_override": override,
        "display_name": name,
    }


def load_csv(path, preview_limit=500):
    """Load a masterlist CSV. Returns (fieldnames, rows, preview_rows).

    Raises ValueError on missing required headers.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - set(fieldnames)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")
        rows = list(reader)

    if "Done" not in fieldnames:
        fieldnames = fieldnames + ["Done"]
        for r in rows:
            r.setdefault("Done", "")
    return fieldnames, rows, rows[:preview_limit]


def mark_done(path, fieldnames, rows, idx):
    """Persist Done=TRUE for one row immediately (crash-safe progress)."""
    rows[idx]["Done"] = "TRUE"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def load_enrollees(path, preview_limit=500):
    """Load a Stage 2 enrollee CSV: the masterlist schema plus learner IDs.

    Same required columns as Stage 1 (so the same file flows through both
    stages) with one addition: a learner-ID column from ENROLL_ID_ALIASES
    (e.g. "Learner's ID", filled from the Sheet between stages).
    Returns (fieldnames, rows, preview_rows); ensures Enrolled flag exists.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - set(fieldnames)
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")
        if not any(c in fieldnames for c in ENROLL_ID_ALIASES):
            raise ValueError(
                f"Missing learner-ID column (need one of {ENROLL_ID_ALIASES}). "
                f"Found: {fieldnames}"
            )
        rows = list(reader)

    if ENROLLED_COL not in fieldnames:
        fieldnames = fieldnames + [ENROLLED_COL]
        for r in rows:
            r.setdefault(ENROLLED_COL, "")
    return fieldnames, rows, rows[:preview_limit]


def mark_enrolled(path, fieldnames, rows, idx):
    """Persist Enrolled=TRUE for one row immediately (crash-safe progress)."""
    rows[idx][ENROLLED_COL] = "TRUE"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def preview_columns(fieldnames):
    """Order columns for the preview table: priority first, rest as-is."""
    ordered = [c for c in PREVIEW_PRIORITY if c in fieldnames]
    ordered += [c for c in fieldnames if c not in ordered]
    return ordered
