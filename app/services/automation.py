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
