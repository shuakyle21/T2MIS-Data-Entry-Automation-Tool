"""Selenium form-filling engine ported from main.py.

Qt-free on purpose: the QThread worker calls into these functions, and the
UI layer never touches the driver. Locators, retry semantics, and fill order
are verbatim preserves of the proven script.
"""

import difflib
import math
import re

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

REGISTER_URL = "https://t2mis.tesda.gov.ph/Registration/Register"
SEARCH_URL = "https://t2mis.tesda.gov.ph/Registration/SearchStudent"
HOME_URL = "https://t2mis.tesda.gov.ph/"

FORM_READY_ID = "Family_name"
# Stage 2 search form (live DOM 2026-10):
# <form action="/TVIAdmin/SearchStudentproc" id="searchForm" method="get">
#   <input type="text" name="uliFilter" ...>           (no id attribute)
#   <input type="text" name="SchSchedId" value="744175" hidden="hidden">
#   <button type="submit" class="btn btn-blue">Search</button>
# Name filters (firstname/middlename/lastnameFilter) exist but Stage 2
# searches by Learner's ID only.
SEARCH_FORM_XPATH = "//form[@id='searchForm']"
LEARNER_ID_XPATH = "//input[@name='uliFilter']"
SEARCH_BTN_XPATH = "//form[@id='searchForm']//button[@type='submit']"
FORM_SCHED_XPATH = "//form[@id='searchForm']//input[@name='SchSchedId']"
# Search-result enroll link (live DOM 2026-10):
# <a href="/TVIAdmin/ScheduleEnrollsAdd?...&SchSchedId=744175&...&ULIId=18432145"
#    class="btn btn-warning btn-xs">... Enroll</a>
# Same per-session token style as the schedule link; match on the stable
# endpoint + schedule + learner params. Never clicked by automation.
ENROLL_LINK_XPATH = "//a[contains(@href, 'ScheduleEnrollsAdd') and contains(., 'Enroll')]"
# Enroll-details form reached via the result Enroll link (live DOM 2026-10):
# <form action="/TVIAdmin/ScheduleEnrollsAdd?...&SchSchedId=744175&...&ULIId=18432149"
#  method="post"> with hidden SchSchedId/ULIId, selects CivilId, EducIdId,
# PWDClientID (+conditional PwdId/CauseId), EmpTypeId (+conditional EmpStatId),
# TrainStatusId (+conditional TypeScholarId/VoucherNumber), checkboxes
# name="selectedTypes", radio Continue=Yes (enables Create), submit #confirm.
ENROLL_DETAILS_XPATH = "//form[contains(@action, 'ScheduleEnrollsAdd')]"
DEFAULT_SCHED_ID = "744175"


def _fill_by_xpath(driver, wait, xpath, value, retries=3):
    """Fill a text input located by XPath (for fields without an id)."""
    for attempt in range(retries):
        try:
            el = wait.until(EC.visibility_of_element_located((By.XPATH, xpath)))
            el.clear()
            el.send_keys(value)
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


def fill_text(driver, wait, field_id, value, retries=3):
    """Fill a text input and dispatch input/change events for validation."""
    for attempt in range(retries):
        try:
            el = wait.until(EC.visibility_of_element_located((By.ID, field_id)))
            el.clear()
            el.send_keys(value)
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
    vals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
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
    """Select dropdown option by exact, token-weighted, then difflib match."""
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

            chosen = None
            for text, norm in options:
                if norm == target_norm:
                    select.select_by_visible_text(text)
                    chosen = text
                    break
            else:
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
                    score -= 0.05 * len(toks)
                    if score > best_score:
                        best, best_score = text, score
                if best is None:
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
                el = driver.find_element(By.ID, field_id)
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                    el,
                )
            except StaleElementReferenceException:
                pass  # selection already applied; stale dispatch is harmless
            return chosen
        except StaleElementReferenceException as e:
            last_exc = e
            continue
    raise last_exc


def select_radio_by_name_value(driver, wait, name, value, retries=3):
    """Click a radio via name+value XPath (fires inline onclick check())."""
    xpath = f"//input[@name='{name}'][@value='{value}']"
    last_exc = None
    for _ in range(retries):
        try:
            el = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            try:
                el.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", el)
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                    el,
                )
                assert el.is_selected(), f"Radio {name}={value} did not select."
            except StaleElementReferenceException:
                el = driver.find_element(By.XPATH, xpath)
                assert el.is_selected(), f"Radio {name}={value} did not select."
            return True
        except StaleElementReferenceException as e:
            last_exc = e
            continue
    raise last_exc


def open_register_menu(driver, wait):
    """Click the REGISTER dropdown toggle."""
    _ensure_nav_expanded(driver, wait)
    toggle = wait.until(
        EC.element_to_be_clickable((
            By.XPATH,
            "//a[contains(@class, 'dropdown-toggle') and contains(normalize-space(.), 'REGISTER')]",
        ))
    )
    toggle.click()
    wait.until(
        EC.visibility_of_element_located((
            By.XPATH,
            "//li[contains(@class, 'open')]//a[contains(@href, 'SearchStudent')]",
        ))
    )
    return toggle


def _ensure_nav_expanded(driver, wait):
    """Reveal the navbar on small viewports (hamburger collapse)."""
    try:
        toggles = driver.find_elements(
            By.XPATH,
            "//a[contains(@class, 'dropdown-toggle') and contains(normalize-space(.), 'REGISTER')]",
        )
        if toggles and toggles[0].is_displayed():
            return
        hamburger = driver.find_element(By.CSS_SELECTOR, "button.navbar-toggle")
        if hamburger.is_displayed():
            hamburger.click()
            WebDriverWait(driver, 3, poll_frequency=0.1).until(
                lambda d: any(
                    t.is_displayed() for t in d.find_elements(
                        By.XPATH,
                        "//a[contains(@class, 'dropdown-toggle') and contains(normalize-space(.), 'REGISTER')]",
                    )
                )
            )
    except Exception:
        pass  # full-width layout: nothing to expand


def _click(driver, wait, xpath, retries=3):
    """Scroll into view and click; falls back to JS on overlay cover."""
    last_exc = None
    for _ in range(retries):
        try:
            el = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
            try:
                el.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", el)
            return el
        except StaleElementReferenceException as e:
            last_exc = e
            continue
    raise last_exc


def goto_register_form(driver, wait):
    """Navigate to the Register form. Skips reload if already there."""
    if "Registration/Register" in (driver.current_url or ""):
        try:
            WebDriverWait(driver, 2, poll_frequency=0.1).until(
                EC.visibility_of_element_located((By.ID, FORM_READY_ID))
            )
            return driver.current_url
        except Exception:
            pass
    driver.get(REGISTER_URL)
    try:
        wait.until(EC.visibility_of_element_located((By.ID, FORM_READY_ID)))
        return driver.current_url
    except Exception:
        pass
    driver.get(HOME_URL)
    try:
        open_register_menu(driver, wait)
        reg_link = wait.until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//a[contains(@href, 'Registration/Register') or normalize-space(.)='REGISTER LEARNER']",
            ))
        )
        reg_link.click()
    except Exception:
        driver.get(REGISTER_URL)
    wait.until(EC.visibility_of_element_located((By.ID, FORM_READY_ID)))
    return driver.current_url


def fill_registration(driver, wait, data):
    """Fill the form inside `.row.col-xs-12`. Fill-only: never submits."""
    goto_register_form(driver, wait)

    fill_text(driver, wait, "Family_name", data["last_name"])
    fill_text(driver, wait, "First_name", data["first_name"])
    if data["middle_name"]:
        fill_text(driver, wait, "Middle_name", data["middle_name"])
    fill_text(driver, wait, "DateOfBirth", data["dob"])
    dob_actual = driver.find_element(By.ID, "DateOfBirth").get_attribute("value")
    dob_mismatch = None
    if dob_actual != data["dob"]:
        dob_mismatch = f"DOB mismatch: entered {data['dob']!r}, field shows {dob_actual!r}"

    actual_b_region = select_by_fuzzy_text(driver, wait, "BRegionId", data["b_region"])
    wait_for_dependent_options(wait, "BProvinceId")
    actual_b_province = select_by_fuzzy_text(driver, wait, "BProvinceId", data["b_province"])
    wait_for_dependent_options(wait, "BCityId")
    actual_b_city = select_by_fuzzy_text(driver, wait, "BCityId", data["b_city"])

    actual_region = select_by_fuzzy_text(driver, wait, "RegionId", data["region"])
    wait_for_dependent_options(wait, "ProvinceId")
    actual_province = select_by_fuzzy_text(driver, wait, "ProvinceId", data["province"])
    wait_for_dependent_options(wait, "CityId")
    actual_city = select_by_fuzzy_text(driver, wait, "CityId", data["city"])

    actual_sex = select_by_fuzzy_text(driver, wait, "SexId", data["sex"])

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

    select_radio_by_name_value(driver, wait, "Allow", data["allow"])
    select_radio_by_name_value(driver, wait, "Continue", data["continue"])

    # NOTE: fill-only. Never click input#sendRegister / Next — user verifies.
    return {
        "birth": (actual_b_region, actual_b_province, actual_b_city),
        "present": (actual_region, actual_province, actual_city),
        "sex": actual_sex,
        "district": actual_district,
        "dob_mismatch": dob_mismatch,
    }


# ---------------------------------------------------------------------------
# Stage 2 — enrollment (schedule-first, then learner by ID).
# Search-only by design: fills LearnerId + clicks Search, never clicks
# EnrollLearner. The operator verifies the match and enrolls manually.
# ---------------------------------------------------------------------------

def goto_schedule_enroll(driver, wait, sched_id):
    """Navigate to the SearchStudent enroll form for one SchSchedId.

    Skips navigation when the current page already shows the right
    schedule's search form. Otherwise walks the menu: Register dropdown
    -> Register Learner page -> schedule Enroll link (partial-href match
    on SchSchedId; the href carries per-session tokens, e.g.
    /TVIAdmin/SearchStudent?...&SchSchedId=744175&...).

    Live DOM (2026-10): the qualification <h4> holds the Enroll link
    directly (<a class="btn btn-blue pull-right btn-xs">); there is no
    separate Add step on that panel, so the Enroll link is tried first
    and the legacy Add click is only a fallback.
    """
    sched_id = str(sched_id).strip()
    try:
        if f"SchSchedId={sched_id}" in (driver.current_url or ""):
            WebDriverWait(driver, 2, poll_frequency=0.1).until(
                EC.visibility_of_element_located((By.XPATH, LEARNER_ID_XPATH))
            )
            return driver.current_url
    except Exception:
        pass

    goto_register_form(driver, wait)

    enroll_xpath = f"//a[contains(@href, 'SchSchedId={sched_id}') and contains(., 'Enroll')]"
    try:
        _click(driver, wait, enroll_xpath)
    except Exception:
        # Legacy structure: an Add link reveals the schedule table first.
        _click(driver, wait, "//h4//a[contains(., 'Add')]")
        _click(driver, wait, enroll_xpath)
    # The Enroll link lands on /TVIAdmin/SearchStudent?...&SchSchedId=...
    # carrying the search form; the header link is only a fallback.
    try:
        wait.until(EC.visibility_of_element_located((By.XPATH, LEARNER_ID_XPATH)))
    except Exception:
        _click(driver, wait, "//h4//a[contains(@href, 'SearchStudent')]")
        wait.until(EC.visibility_of_element_located((By.XPATH, LEARNER_ID_XPATH)))
    return driver.current_url


def read_form_schedule(driver):
    """Return the hidden SchSchedId value on the search form, or None."""
    try:
        value = driver.find_element(By.XPATH, FORM_SCHED_XPATH).get_attribute(
            "value"
        )
        return (value or "").strip() or None
    except Exception:
        return None


def search_learner_for_enroll(
    driver, wait, learner_id, sched_id=None, navigate=True
):
    """Search one learner on the schedule enroll page. Never enrolls.

    Returns {"learner_id", "match_text", "enroll_ready", "page_url"}.
    enroll_ready is True when an EnrollLearner link is present for the
    operator to click manually.

    navigate=False skips menu navigation (manual-schedule mode: the
    operator already opened the schedule's search form).
    """
    learner_id = str(learner_id).strip()
    if not learner_id:
        raise ValueError("Empty Learner's ID.")
    if sched_id and navigate:
        goto_schedule_enroll(driver, wait, sched_id)
    else:
        wait.until(EC.visibility_of_element_located((By.XPATH, LEARNER_ID_XPATH)))

    _fill_by_xpath(driver, wait, LEARNER_ID_XPATH, learner_id)

    # Safety: the form carries its schedule in a hidden field — refuse to
    # search when it doesn't match the intended schedule (wrong-schedule
    # enrollments are hard to undo).
    try:
        form_sched = driver.find_element(By.XPATH, FORM_SCHED_XPATH).get_attribute(
            "value"
        )
        if sched_id and form_sched and form_sched.strip() != str(sched_id).strip():
            raise ValueError(
                f"Schedule mismatch: form targets SchSchedId={form_sched.strip()!r}, "
                f"expected {str(sched_id).strip()!r}. Aborting before search."
            )
    except ValueError:
        raise
    except Exception:
        pass  # hidden field absent: rely on the URL check in navigation

    _click(driver, wait, SEARCH_BTN_XPATH)
    # Result: ScheduleEnrollsAdd link on hit (row also holds the <code>
    # learner ID and <strong> name for the log); otherwise no-match.
    # NOTE: search-only. The Enroll link is reported, never clicked —
    # the operator verifies the name/DOB row and enrolls manually.
    try:
        link = WebDriverWait(driver, 5, poll_frequency=0.1).until(
            EC.presence_of_element_located((By.XPATH, ENROLL_LINK_XPATH))
        )
        enroll_ready = True
        href = link.get_attribute("href") or ""
        if sched_id and f"SchSchedId={str(sched_id).strip()}" not in href:
            raise ValueError(
                f"Result enroll link targets a different schedule: {href!r}."
            )
        m = re.search(r"ULIId=(\d+)", href)
        uli_id = m.group(1) if m else None
    except ValueError:
        raise
    except Exception:
        enroll_ready = False
        href = ""
        uli_id = None
    try:
        if enroll_ready:
            row = link.find_element(By.XPATH, "./ancestor::tr[1]")
            match_text = " ".join((row.text or "").split())
        else:
            body_text = driver.find_element(By.TAG_NAME, "body").text or ""
            match_text = " ".join(body_text.split())[:300]
    except Exception:
        match_text = ""
    return {
        "phase": "search",
        "learner_id": learner_id,
        "match_text": match_text,
        "enroll_ready": enroll_ready,
        "uli_id": uli_id,
        "page_url": driver.current_url,
    }


def read_enroll_details_ids(driver):
    """Return (SchSchedId, ULIId) hidden values on the details form."""
    try:
        form = driver.find_element(By.XPATH, ENROLL_DETAILS_XPATH)
        sched = form.find_element(
            By.XPATH, ".//input[@name='SchSchedId']"
        ).get_attribute("value")
        uli = form.find_element(By.XPATH, ".//input[@name='ULIId']").get_attribute(
            "value"
        )
        return ((sched or "").strip() or None, (uli or "").strip() or None)
    except Exception:
        return (None, None)


def select_checkbox_by_label(driver, wait, name, target, retries=3):
    """Check a checkbox by its label text (exact, then difflib fallback)."""
    target_norm = target.strip().lower()
    xpath = f"//input[@type='checkbox' and @name='{name}']"
    wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
    boxes = driver.find_elements(By.XPATH, xpath)
    labeled = []
    for b in boxes:
        try:
            text = (b.find_element(By.XPATH, "./ancestor::label[1]").text or "")
        except Exception:
            text = ""
        labeled.append((b, text.strip() or (b.get_attribute("value") or "")))
    chosen = None
    for b, text in labeled:
        if text.strip().lower() == target_norm:
            chosen = (b, text)
            break
    if chosen is None:
        norms = [t.strip().lower() for _, t in labeled]
        close = difflib.get_close_matches(target_norm, norms, n=1, cutoff=0.6)
        if close:
            chosen = next(
                (b, t) for b, t in labeled if t.strip().lower() == close[0]
            )
    if chosen is None:
        raise ValueError(
            f"No checkbox {name}={target!r}. Available: {[t for _, t in labeled]}"
        )
    el, text = chosen
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
    if not el.is_selected():
        try:
            el.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", el)
    assert el.is_selected(), f"Checkbox {name}={target!r} did not check."
    return text


NO_DISABILITY = {"na", "n/a", "none", "no", "-", ""}


def fill_enroll_details(
    driver, wait, data, expected_sched=None, expected_uli_id=None
):
    """Autofill the post-Enroll details form. Fill-only: never submits.

    data keys: civil, educ, client_type, disability, emp_type (from the
    masterlist). Fields with no CSV source (scholarship, voucher, detail
    sub-selects) are left for the operator and reported in "manual".
    Returns {"phase", "set", "manual", "sched", "uli_id"}.
    """
    wait.until(
        EC.visibility_of_element_located((By.XPATH, ENROLL_DETAILS_XPATH))
    )
    sched, uli = read_enroll_details_ids(driver)
    if expected_sched and sched and sched != str(expected_sched).strip():
        raise ValueError(
            f"Details form targets schedule {sched!r}, expected "
            f"{str(expected_sched).strip()!r}."
        )
    if expected_uli_id and uli and uli != str(expected_uli_id).strip():
        raise ValueError(
            f"Details form targets learner ULIId={uli!r}, expected "
            f"{str(expected_uli_id).strip()!r}."
        )
    done, manual = {}, []

    if data.get("civil"):
        done["civil"] = select_by_fuzzy_text(driver, wait, "CivilId", data["civil"])
    else:
        manual.append("CivilId (empty in CSV)")

    if data.get("educ"):
        done["education"] = select_by_fuzzy_text(
            driver, wait, "EducIdId", data["educ"]
        )
    else:
        manual.append("EducIdId (empty in CSV)")

    if data.get("client_type"):
        # Multiple classifications come comma-separated; check each box.
        picked, missing = [], []
        parts = [p.strip() for p in data["client_type"].split(",") if p.strip()]
        for part in parts:
            try:
                picked.append(
                    select_checkbox_by_label(driver, wait, "selectedTypes", part)
                )
            except ValueError:
                missing.append(part)
        done["client_type"] = picked
        if missing:
            manual.append(f"Client Type unmatched: {missing} (check manually)")
        if any(label.strip().lower() == "others" for label in picked):
            manual.append("otherClient text (no CSV source)")
    else:
        manual.append("Client Type checkbox (empty in CSV)")

    dis = (data.get("disability") or "").strip()
    if dis.lower() not in NO_DISABILITY:
        done["pwd"] = select_by_fuzzy_text(driver, wait, "PWDClientID", "Yes")
        try:
            WebDriverWait(driver, 5, poll_frequency=0.1).until(
                EC.visibility_of_element_located((By.ID, "PwdId"))
            )
            done["disability"] = select_by_fuzzy_text(driver, wait, "PwdId", dis)
        except Exception:
            manual.append("PwdId (disability panel did not open)")
        manual.append("CauseId — cause of disability (no CSV source)")
    else:
        done["pwd"] = select_by_fuzzy_text(driver, wait, "PWDClientID", "No")

    if data.get("emp_type"):
        done["employment"] = select_by_fuzzy_text(
            driver, wait, "EmpTypeId", data["emp_type"]
        )
    else:
        manual.append("EmpTypeId (empty in CSV)")
    manual.append("EmpStatId — status detail when employed (no CSV source)")
    manual.append("TrainStatusId / TypeScholarId / VoucherNumber (no CSV source)")

    # Enable Create for the operator (inline onclick only); never click #confirm.
    select_radio_by_name_value(driver, wait, "Continue", "Yes")
    done["continue"] = "Yes"
    return {
        "phase": "details",
        "set": done,
        "manual": manual,
        "sched": sched,
        "uli_id": uli,
    }
