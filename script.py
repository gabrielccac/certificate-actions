import re
import os
import sys

from seleniumbase import Driver
from curl_cffi import requests

# Configuration and Constants
TRF1_PAGE_URL = "https://sistemas.trf1.jus.br/certidao/#/solicitacao" # TODO: Update this URL
RECAPTCHA_CO = "aHR0cHM6Ly9zaXN0ZW1hcy50cmYxLmp1cy5icjo0NDM." # TODO: Update with your target reCAPTCHA 'co' parameter

def log(level, message):
    print(f"[{level}] {message}", file=sys.stderr)

def configure_writable_driver_dir():
    """
    On some CI environments, seleniumbase needs a writable folder for drivers.
    """
    os.environ["SELENIUMBASE_DIR"] = os.path.join(os.getcwd(), ".seleniumbase")

def _get_recaptcha_token():
    driver = None
    page_source = ""
    try:
        configure_writable_driver_dir()
        driver = Driver(uc=True, headless=False, no_sandbox=True, pls="eager")
        driver.get(TRF1_PAGE_URL)
        driver.wait_for_element_present("iframe[src*='recaptcha']", timeout=15)
        page_source = driver.page_source
        log("INFO", "Page loaded for reCAPTCHA extraction.")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    key_match = re.search(r'recaptcha/api2/anchor\?[^"]*?k=([A-Za-z0-9_-]+)', page_source)
    ver_match = re.search(r'recaptcha/api2/anchor\?[^"]*?v=([A-Za-z0-9_-]+)', page_source)
    if not key_match:
        raise ValueError("reCAPTCHA key not found")

    key = key_match.group(1)
    version = ver_match.group(1) if ver_match else ""
    log("INFO", f"reCAPTCHA key extracted: {key}")

    session = requests.Session(impersonate="chrome120")
    anchor_resp = session.get(
        "https://www.google.com/recaptcha/api2/anchor",
        params={
            "ar": "1",
            "k": key,
            "co": RECAPTCHA_CO,
            "hl": "pt-BR",
            "v": version,
            "size": "invisible",
        },
    )
    anchor_resp.raise_for_status()

    token_match = re.search(r'id="recaptcha-token"\s+value="([^"]+)"', anchor_resp.text)
    if not token_match:
        raise ValueError("reCAPTCHA anchor token not found")
    c_token = token_match.group(1)

    reload_resp = session.post(
        f"https://www.google.com/recaptcha/api2/reload?k={key}",
        data=f"v={version}&reason=q&c={c_token}&k={key}&co={RECAPTCHA_CO}&hl=pt-BR&size=invisible",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    reload_resp.raise_for_status()

    final_match = re.search(r'"rresp","([^"]+)"', reload_resp.text)
    if not final_match:
        raise ValueError("reCAPTCHA reload token not found")

    log("INFO", "reCAPTCHA token obtained.")
    return final_match.group(1)

if __name__ == "__main__":
    # Execute the function and print the token so it can be seen or captured
    try:
        token = _get_recaptcha_token()
        print(f"Obtained Token:\n{token}")
    except Exception as e:
        log("ERROR", f"Failed to get token: {e}")
        sys.exit(1)
