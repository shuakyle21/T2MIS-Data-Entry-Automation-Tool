"""Attendance sheet batch generator (Stage 3, offline, Qt-free).

Fills `attendance_template.docx` from the enrollees/masterlist CSV:
- Names come from Last Name / First Name / Middle Name (MI = initial).
- One sheet holds CAPACITY students; overflow creates continuation pages.
- One page per date; output is a single DOCX with page breaks between days.
- Placeholders replaced per page: {INSERT SCHOOL}, {INSERT PROGRAM}, INSERT DATE}.
"""

import copy
import math
from datetime import timedelta
from pathlib import Path

try:
    from docx import Document
    from docx.enum.text import WD_BREAK
except ImportError:  # surfaced as a friendly error at generate-time
    Document = None
    WD_BREAK = None

CAPACITY = 31  # student slots per sheet in the template
HOURS_PER_DAY = 8

SCHOOL_PLACEHOLDERS = ("{INSERT SCHOOL}",)
PROGRAM_PLACEHOLDERS = ("{INSERT PROGRAM}",)
# Template has a typo (missing opening brace): "Date\t\t: INSERT DATE}".
DATE_PLACEHOLDERS = ("{INSERT DATE}", "INSERT DATE}", "{INSERT DATE")


def names_from_rows(rows):
    """Extract + alphabetically sort [(last, first, mi)] from CSV row dicts."""
    names = []
    for r in rows:
        last = str(r.get("Last Name", "") or "").strip()
        first = str(r.get("First Name", "") or "").strip()
        middle = str(r.get("Middle Name", "") or "").strip()
        if not last and not first:
            continue
        mi = f"{middle[0].upper()}." if middle else ""
        names.append({"last": last, "first": first, "mi": mi})
    names.sort(key=lambda n: (n["last"].lower(), n["first"].lower()))
    return names


def expand_dates(start, end, weekdays):
    """Return [date,...] in [start, end] whose weekday() is in `weekdays`.

    weekdays: set of ints, Monday=0 .. Sunday=6. Empty set -> [].
    """
    if start is None or end is None or not weekdays:
        return []
    if end < start:
        return []
    out = []
    cur = start
    while cur <= end:
        if cur.weekday() in weekdays:
            out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def preview_counts(n_students, n_days):
    """Return (n_days, total_hours, n_sheets)."""
    hours = n_days * HOURS_PER_DAY
    chunks = math.ceil(n_students / CAPACITY) if n_students else 0
    sheets = n_days * chunks
    return n_days, hours, sheets


def format_date(d):
    """'January 5, 2026 (Monday)' — portable (no %-d)."""
    return f"{d.strftime('%B')} {d.day}, {d.year} ({d.strftime('%A')})"


def _replace_in_paragraph(para, old, new):
    """Replace `old` with `new` preserving runs when possible."""
    if old not in para.text:
        return False
    for run in para.runs:
        if old in run.text:
            run.text = run.text.replace(old, new)
            # remove duplicate occurrences across runs is handled by loop
    if old in para.text:
        # Placeholder was split across runs: rewrite whole paragraph.
        para.text = para.text.replace(old, new)
    return True


def _fill_page(paragraphs, table, names_chunk, school, program, date_str, cont=False, start_no=1):
    """Fill one copied page: placeholders + student rows."""
    label_school = school.strip() or "________________________"
    label_program = program.strip() or "________________________"
    label_date = date_str + ("  (p.2)" if cont else "")
    for para in paragraphs:
        for ph in SCHOOL_PLACEHOLDERS:
            _replace_in_paragraph(para, ph, label_school)
        for ph in PROGRAM_PLACEHOLDERS:
            _replace_in_paragraph(para, ph, label_program)
        for ph in DATE_PLACEHOLDERS:
            _replace_in_paragraph(para, ph, label_date)
    # Fill student rows: skip the 2 header rows.
    for i in range(CAPACITY):
        row = table.rows[2 + i]
        if i < len(names_chunk):
            n = names_chunk[i]
            row.cells[0].text = str(start_no + i)
            row.cells[1].text = n["last"]
            row.cells[2].text = n["first"]
            row.cells[3].text = n["mi"]
            row.cells[4].text = ""
            row.cells[5].text = ""
            row.cells[6].text = ""
        else:
            # Leave NO numbering, blank the rest (matches blank template rows).
            row.cells[1].text = ""
            row.cells[2].text = ""
            row.cells[3].text = ""
            row.cells[4].text = ""
            row.cells[5].text = ""
            row.cells[6].text = ""


def generate_attendance(template_path, names, school, program, dates, out_path):
    """Build a single DOCX with one sheet per date (overflow -> extra pages).

    Returns (n_sheets, total_hours). Raises ValueError on bad input,
    ImportError when python-docx is missing.
    """
    if Document is None:
        raise ImportError("python-docx is not installed (pip install python-docx).")
    names = list(names or [])
    dates = list(dates or [])
    if not names:
        raise ValueError("No students loaded — open a CSV first.")
    if not dates:
        raise ValueError("No dates selected — pick a range with a weekday checked.")
    template_path = Path(template_path)
    if not template_path.exists():
        raise ValueError(f"Template not found: {template_path}")

    chunks = [names[i : i + CAPACITY] for i in range(0, len(names), CAPACITY)]
    total_hours = len(dates) * HOURS_PER_DAY

    base = Document(str(template_path))
    base_paras = list(base.paragraphs)
    base_table = base.tables[0]
    # Snapshot the template body children for cloning later pages.
    template_clone = Document(str(template_path))
    clone_children = list(template_clone.element.body)

    first = True
    n_sheets = 0
    for d in dates:
        date_str = format_date(d)
        for ci, chunk in enumerate(chunks):
            start_no = ci * CAPACITY + 1
            if first:
                _fill_page(base_paras, base_table, chunk, school, program, date_str, cont=ci > 0, start_no=start_no)
                first = False
            else:
                # Page break, then deep-copy every template body element.
                pb_para = base.add_paragraph()
                run = pb_para.add_run()
                run.add_break(WD_BREAK.PAGE)
                new_paras_start = len(base.paragraphs)
                for child in clone_children:
                    base.element.body.append(copy.deepcopy(child))
                new_paras = base.paragraphs[new_paras_start:]
                new_table = base.tables[-1]
                _fill_page(new_paras, new_table, chunk, school, program, date_str, cont=ci > 0, start_no=start_no)
            n_sheets += 1

    out_path = Path(out_path)
    if out_path.parent and str(out_path.parent) not in ("", "."):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(str(out_path))
    return n_sheets, total_hours
