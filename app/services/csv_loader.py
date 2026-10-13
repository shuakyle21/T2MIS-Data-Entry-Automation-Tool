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

# Columns shown first in the preview table; the rest follow alphabetically.
PREVIEW_PRIORITY = [
    "Last Name",
    "First Name",
    "Middle Name",
    "Birthdate",
    "Sex",
    "Contact No.:",
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


def preview_columns(fieldnames):
    """Order columns for the preview table: priority first, rest as-is."""
    ordered = [c for c in PREVIEW_PRIORITY if c in fieldnames]
    ordered += [c for c in fieldnames if c not in ordered]
    return ordered
