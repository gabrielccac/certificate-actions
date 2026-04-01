import argparse
import json
import os
import re
import sys
import requests
from curl_cffi import requests as requests
from seleniumbase import Driver

TRF1_PAGE_URL = "https://sistemas.trf1.jus.br/certidao/#/solicitacao"
RECAPTCHA_CO = "aHR0cHM6Ly9zaXN0ZW1hcy50cmYxLmp1cy5icjo0NDM."


def log(level, message):
    print(f"[{level}] {message}", file=sys.stderr)


def get_callback_url():
    callback_url = os.environ.get("RAW_CALLBACK")
    if not callback_url:
        parser = argparse.ArgumentParser(description="TRF1 reCAPTCHA Token Generator")
        parser.add_argument("--callback-url", required=False, help="URL to POST the result JSON to")
        args, _ = parser.parse_known_args()
        callback_url = args.callback_url
    return callback_url


def send_callback(url: str, result: dict) -> None:
    try:
        resp = requests.post(url, json=result, timeout=15)
        log("INFO", f"Callback sent (status {resp.status_code})")
    except Exception as e:
        log("ERROR", f"Callback failed: {e}")


def _get_recaptcha_token() -> str:
    driver = Driver(uc=True, headless=False, no_sandbox=True, pls="eager")
    try:
        driver.get(TRF1_PAGE_URL)
        driver.wait_for_element_present("iframe[src*='recaptcha']", timeout=15)
        page_source = driver.page_source
        log("INFO", "Page loaded for reCAPTCHA extraction.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    key_match = re.search(r'recaptcha/api2/anchor\?[^"]*?k=([A-Za-z0-9_-]+)', page_source)
    ver_match = re.search(r'recaptcha/api2/anchor\?[^"]*?v=([A-Za-z0-9_-]+)', page_source)
    if not key_match:
        raise ValueError("reCAPTCHA key not found in page source")

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

    log("INFO", "reCAPTCHA token obtained successfully.")
    return final_match.group(1)


if __name__ == "__main__":
    callback_url = get_callback_url()
    try:
        token = _get_recaptcha_token()
        result = {"status": "success", "token": token}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if callback_url:
            send_callback(callback_url, result)
        sys.exit(0)
    except Exception as e:
        log("ERROR", f"Failed to get token: {e}")
        result = {"status": "error", "token": None}
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if callback_url:
            send_callback(callback_url, result)
        sys.exit(1)
