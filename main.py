import csv
import difflib
import math
import re
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

REGISTER_URL = "https://t2mis.tesda.gov.ph/Registration/Register"
SEARCH_URL = "https://t2mis.tesda.gov.ph/Registration/SearchStudent"
HOME_URL = "https://t2mis.tesda.gov.ph/"
CSV_PATH = Path.cwd() / "data" / "registrants.csv"


def fill_text(driver, wait, field_id, value, retries=3):
    """Fill a text input and dispatch input/change events for validation."""
    for attempt in range(retries):
        try:
            el = wait.until(EC.visibility_of_element_located((By.ID, field_id)))
            el.clear()
            el.send_keys(value)
            # Trigger site validation logic that listens for JS events.
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                el,
            )
            return el
        except StaleElementReferenceException:
            if attempt == retries - 1:
                raise
    return el


def wait_for_dependent_options(wait, field_id, min_options=2, timeout=10):
    """Wait until an AJAX-dependent SELECT is populated (fast poll)."""
    dep_wait = WebDriverWait(wait._driver, timeout, poll_frequency=0.1)

    def _populated(d):
        try:
            return len(Select(d.find_element(By.ID, field_id)).options) >= min_options
        except StaleElementReferenceException:
            return False  # DOM mid-replacement; keep polling

    dep_wait.until(_populated)


def int_to_roman(n):
    """Convert 1-39 to Roman numerals (district dropdown uses I, II, III...)."""
    vals = [
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    res = ""
    for v, sym in vals:
        while n >= v:
            res += sym
            n -= v
    return res


def normalize_district(target):
    """Map numeric district ('3', '3rd') to dropdown form ('iii')."""
    t = target.strip()
    if re.fullmatch(r"\d{1,2}\s*(st|nd|rd|th)?", t.lower()):
        return int_to_roman(int(re.search(r"\d+", t).group())).lower()
    return t.lower()


def select_by_fuzzy_text(driver, wait, field_id, target, retries=3):
    """Select dropdown option by exact, token-weighted, then difflib match.

    T2MIS option text (e.g. 'XII - SOCCSKSARGEN') may not equal the
    spreadsheet value (e.g. 'REGION XII'), so exact match often fails.
    Token weighting avoids XI-vs-XII confusion: rare tokens (XII) weigh
    more than common ones (region).

    Retries on StaleElementReferenceException: selecting an option fires
    an onchange AJAX refresh that can detach the <select> node before the
    follow-up change-event dispatch reads it.
    """
    if field_id == "District":
        target_norm = normalize_district(target)
    else:
        target_norm = target.strip().lower()
    last_exc = None
    for _ in range(retries):
        try:
            el = wait.until(EC.element_to_be_clickable((By.ID, field_id)))
            select = Select(el)
            options = [(o.text, o.text.strip().lower()) for o in select.options]

            # 1. Exact case-insensitive match.
            chosen = None
            for text, norm in options:
                if norm == target_norm:
                    select.select_by_visible_text(text)
                    chosen = text
                    break
            else:
                # 2. Token-weighted overlap (IDF-style): rare shared tokens win.
                def tokens(s):
                    return set(re.findall(r"[a-z0-9]+", s))

                target_toks = tokens(target_norm)
                opt_toks = [(text, tokens(norm)) for text, norm in options]
                doc_freq = {}
                for _, toks in opt_toks:
                    for t in toks:
                        doc_freq[t] = doc_freq.get(t, 0) + 1
                n_opts = max(len(opt_toks), 1)
                best, best_score = None, 0.0
                for (text, norm), (_, toks) in zip(options, opt_toks):
                    shared = target_toks & toks
                    if not shared:
                        continue
                    score = sum(math.log(n_opts / doc_freq[t]) for t in shared)
                    # Small bonus for shorter options (fewer stray tokens).
                    score -= 0.05 * len(toks)
                    if score > best_score:
                        best, best_score = text, score
                if best is None:
                    # 3. difflib fallback for typos/abbreviations.
                    norms = [norm for _, norm in options]
                    close = difflib.get_close_matches(target_norm, norms, n=1, cutoff=0.5)
                    if close:
                        best = next(text for text, norm in options if norm == close[0])
                if best is None:
                    available = [o.text for o in select.options]
                    raise ValueError(
                        f"No match for {field_id}={target!r}. Available: {available}"
                    )
                select.select_by_visible_text(best)
                chosen = best
            try:
                # Re-find: select_by_visible_text fires onchange AJAX that may
                # replace the <select> node, staling `el`.
                el = driver.find_element(By.ID, field_id)
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", el
                )
            except StaleElementReferenceException:
                pass  # selection already applied; stale dispatch is harmless
            return chosen
        except StaleElementReferenceException as e:
            last_exc = e
            continue
    raise last_exc


def select_radio_by_name_value(driver, wait, name, value, retries=3):
    """Click a radio via name+value XPath (avoids duplicate-ID conflicts).

    The Allow/Continue radios run a check() validator on click, so a
    native click is used (not JS) to fire the handler, then selection
    is asserted.
    """
    xpath = f"//input[@name='{name}'][@value='{value}']"
    last_exc = None
    for _ in range(retries):
        try:
            el = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", el
            )
            try:
                el.click()
            except ElementClickInterceptedException:
                # Covered by .form-group overlay: JS click still fires
                # the inline onclick="check()" handler.
                driver.execute_script("arguments[0].click();", el)
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", el
                )
                assert el.is_selected(), f"Radio {name}={value} did not select."
            except StaleElementReferenceException:
                # Re-find after click(): validator may have re-rendered the node.
                el = driver.find_element(By.XPATH, xpath)
                assert el.is_selected(), f"Radio {name}={value} did not select."
            return True
        except StaleElementReferenceException as e:
            last_exc = e
            continue
    raise last_exc


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
    """Map registrants.csv columns to form fields."""
    return {
        # B-prefix fields are birthplace (vary per row), not ADDRESS (all Surallah).
        "last_name": row["Last Name"].strip(),
        "first_name": row["First Name"].strip(),
        "middle_name": row.get("Middle Name", "").strip(),
        "dob": format_dob(row["Birthdate"]),
        "b_region": row["Region"].strip(),
        "b_province": row["Province"].strip(),
        "b_city": row["City/Municipality"].strip(),
        # Present address (Contact Information section).
        "region": row["Region - ADDRESS"].strip(),
        "province": row["Province - ADDRESS"].strip(),
        "city": row["City/Municipality - ADDRESS"].strip(),
        "sex": row["Sex"].strip().capitalize(),  # 'FIlipino'-style typos exist nearby
        # Permanent mailing address + contact (present-city dependent).
        "mobile": row.get("Contact No.:", "").strip(),
        "street": row.get("Purok/Street/Block/Lot", "").strip(),
        "barangay": row.get("Barangay", "").strip(),
        "district": row.get("District", "").strip(),
        # Mandatory constants: enable #sendRegister, never clicked (fill-only).
        "allow": "True",
        "continue": "Yes",
    }


def open_register_menu(driver, wait):
    """Click the REGISTER dropdown toggle (text-filtered; multiple .dropdown-toggle exist)."""
    toggle = wait.until(EC.element_to_be_clickable((
        By.XPATH,
        "//a[contains(@class, 'dropdown-toggle') and contains(normalize-space(.), 'REGISTER')]",
    )))
    toggle.click()
    # Wait until its parent li opens / submenu becomes visible.
    wait.until(EC.visibility_of_element_located((
        By.XPATH,
        "//li[contains(@class, 'open')]//a[contains(@href, 'SearchStudent')]",
    )))
    return toggle


def goto_search_learner(driver, wait):
    """Navigate to SEARCH LEARNER via menu, falling back to direct URL."""
    try:
        open_register_menu(driver, wait)
        link = wait.until(EC.element_to_be_clickable((
            By.XPATH, "//a[contains(@href, 'SearchStudent')]",
        )))
        link.click()
        wait.until(EC.url_contains("SearchStudent"))
    except Exception:
        driver.get(SEARCH_URL)
    return driver.current_url


def goto_register_form(driver, wait):
    """Navigate to the Register form. Skips reload if already there."""
    if "Registration/Register" in (driver.current_url or ""):
        try:
            WebDriverWait(driver, 2, poll_frequency=0.1).until(
                EC.visibility_of_element_located((By.ID, "Family_name"))
            )
            return driver.current_url
        except Exception:
            pass
    driver.get(REGISTER_URL)
    try:
        wait.until(EC.visibility_of_element_located((By.ID, "Family_name")))
        return driver.current_url
    except Exception:
        pass
    # Fallback: HOME -> REGISTER menu -> register link / direct GET.
    driver.get(HOME_URL)
    try:
        open_register_menu(driver, wait)
        reg_link = wait.until(EC.element_to_be_clickable((
            By.XPATH,
            "//a[contains(@href, 'Registration/Register') or normalize-space(.)='REGISTER LEARNER']",
        )))
        reg_link.click()
    except Exception:
        driver.get(REGISTER_URL)
    wait.until(EC.visibility_of_element_located((By.ID, "Family_name")))
    return driver.current_url


def fill_registration(driver, wait, data):
    """Fill the form inside `.row.col-xs-12`. Fill-only: never submits."""
    goto_register_form(driver, wait)

    # Text inputs (Middle_name skipped when blank)
    fill_text(driver, wait, "Family_name", data["last_name"])
    fill_text(driver, wait, "First_name", data["first_name"])
    if data["middle_name"]:
        fill_text(driver, wait, "Middle_name", data["middle_name"])
    fill_text(driver, wait, "DateOfBirth", data["dob"])
    dob_actual = driver.find_element(By.ID, "DateOfBirth").get_attribute("value")
    if dob_actual != data["dob"]:
        print(f"  DOB mismatch: entered {data['dob']!r}, field shows {dob_actual!r}")

    # Birth chain (Personal Information): BRegionId -> BProvinceId -> BCityId (AJAX)
    actual_b_region = select_by_fuzzy_text(driver, wait, "BRegionId", data["b_region"])
    wait_for_dependent_options(wait, "BProvinceId")
    actual_b_province = select_by_fuzzy_text(driver, wait, "BProvinceId", data["b_province"])
    wait_for_dependent_options(wait, "BCityId")
    actual_b_city = select_by_fuzzy_text(driver, wait, "BCityId", data["b_city"])

    # Present chain (Contact Information): RegionId -> ProvinceId -> CityId (AJAX)
    actual_region = select_by_fuzzy_text(driver, wait, "RegionId", data["region"])
    wait_for_dependent_options(wait, "ProvinceId")
    actual_province = select_by_fuzzy_text(driver, wait, "ProvinceId", data["province"])
    wait_for_dependent_options(wait, "CityId")
    actual_city = select_by_fuzzy_text(driver, wait, "CityId", data["city"])

    # Sex dropdown (no dependency)
    actual_sex = select_by_fuzzy_text(driver, wait, "SexId", data["sex"])

    # Contact + permanent mailing address (District depends on CityId AJAX).
    if data["mobile"]:
        fill_text(driver, wait, "Contact_no", data["mobile"])
    if data["street"]:
        fill_text(driver, wait, "Number_Street", data["street"])
    if data["barangay"]:
        fill_text(driver, wait, "Barangay", data["barangay"])
    actual_district = None
    if data["district"]:
        wait_for_dependent_options(wait, "District")
        actual_district = select_by_fuzzy_text(driver, wait, "District", data["district"])

    # Consent + confirmation radios (fire check() validator; enable #sendRegister)
    select_radio_by_name_value(driver, wait, "Allow", data["allow"])
    select_radio_by_name_value(driver, wait, "Continue", data["continue"])

    # NOTE: fill-only. Never click input#sendRegister / Next — user verifies.
    return {
        "birth": (actual_b_region, actual_b_province, actual_b_city),
        "present": (actual_region, actual_province, actual_city),
        "sex": actual_sex,
        "district": actual_district,
    }


# Open a browser controlled by Selenium.
driver = webdriver.Chrome()
driver.maximize_window()

# Open T2MIS and log in manually in this browser.
# (Selenium starts a clean session, so automated login would need
# credentials here — manual login avoids storing them.)
driver.get("https://t2mis.tesda.gov.ph/")

input(
    "Log in to T2MIS in the opened Chrome window, "
    "then press Enter here..."
)

wait = WebDriverWait(driver, 20, poll_frequency=0.1)

DONE_VALUES = {"true", "1", "yes", "done", "x"}


def is_done(row):
    """Check the Done flag column (case-insensitive)."""
    return row.get("Done", "").strip().lower() in DONE_VALUES


def mark_done(path, fieldnames, rows, idx):
    """Persist Done=TRUE for one row immediately (crash-safe progress)."""
    rows[idx]["Done"] = "TRUE"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

if "Done" not in fieldnames:
    fieldnames = fieldnames + ["Done"]
    for r in rows:
        r.setdefault("Done", "")

todo = sum(1 for r in rows if not is_done(r))
print(f"Loaded {len(rows)} rows from {CSV_PATH} ({todo} remaining)")

for i, row in enumerate(rows, start=1):
    if is_done(row):
        continue
    data = row_to_data(row)
    print(f"\n--- Row {i}/{len(rows)}: {data['first_name']} {data['last_name']} ---")
    try:
        actual = fill_registration(driver, wait, data)
        print(f"  Birth -> {actual['birth']}")
        print(f"  Present -> {actual['present']}")
        print(f"  Sex -> {actual['sex']} | District -> {actual['district']}")
    except Exception as e:
        print(f"  FAILED: {e}")
        cmd = input("  [Enter]=retry next, s=skip, q=quit: ").strip().lower()
        if cmd == "q":
            break
        continue

    # Pause so you can review, submit manually, then continue.
    # Enter = you submitted -> flag Done and persist. q = quit.
    cmd = input("  Review + submit manually. [Enter]=done/next, q=quit: ").strip().lower()
    if cmd == "q":
        break
    mark_done(CSV_PATH, fieldnames, rows, i - 1)
    print(f"  Flagged row {i} Done.")

driver.quit()