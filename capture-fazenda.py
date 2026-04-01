import argparse
import json
import os
import sys
import time
import requests
from seleniumbase import Driver

URL = "https://ww1.receita.fazenda.df.gov.br/cidadao/certidoes/Certidao"


def log(level, message):
    print(f"[{level}] {message}", file=sys.stderr)


def xpath_literal(value: str) -> str:
    """
    Build a safe XPath string literal for arbitrary text.
    Returns either a quoted string literal or a concat(...) expression.
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    elements = []
    for idx, part in enumerate(parts):
        elements.append(f"'{part}'")
        if idx != len(parts) - 1:
            elements.append("\"'\"")
    return "concat(" + ", ".join(elements) + ")"


def parse_payload() -> dict:
    # 1. Try to get data from Environment Variables (set by GitHub Actions)
    payload_str = os.environ.get("RAW_PAYLOAD")
    callback_url = os.environ.get("RAW_CALLBACK")

    # 2. Fallback to Argparse (if you want to run it manually on your PC)
    if not payload_str:
        parser = argparse.ArgumentParser()
        parser.add_argument("--payload", required=False)
        parser.add_argument("--callback-url", required=False)
        args = parser.parse_args()
        payload_str = args.payload
        callback_url = callback_url or args.callback_url

    # 3. Process the string
    if payload_str:
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError as e:
            log("ERROR", f"Failed to parse JSON: {e}")
            raise
    else:
        log("INFO", "No payload provided. Using default test payload.")
        data = {
            "tipo_pessoa": "Pessoa Física",
            "documento": "12433377617",
            "finalidade": "Junto ao GDF",
            "tipo_certidao": "Certidão de Débitos",
        }

    if not isinstance(data, dict):
        raise ValueError("Invalid payload: expected JSON object")

    required_keys = ["tipo_pessoa", "documento", "finalidade", "tipo_certidao"]
    for key in required_keys:
        if not str(data.get(key, "")).strip():
            raise ValueError(f"Invalid payload: '{key}' is required")

    return {k: str(data[k]).strip() for k in required_keys}, callback_url


payload, callback_url = parse_payload()

captured_path = None
captured_cookie = None
blocked_by_debit = False

driver = Driver(uc=True, uc_cdp_events=True, headless=False, chromium_arg="--ozone-platform=x11")

try:
    def handle_request(data):
        global captured_path, captured_cookie
        try:
            path = data["params"]["headers"].get(":path", "")
            if "P_TurnstileToken" in path:
                log("INFO", "Request captured")
                captured_path = path
                captured_cookie = data["params"]["headers"].get("cookie", "")
        except (KeyError, TypeError):
            pass

    driver.add_cdp_listener("Network.requestWillBeSentExtraInfo", handle_request)

    driver.get(URL)
    time.sleep(2)  # reconnect delay

    try:
        driver.wait_for_element_visible("mat-expansion-panel-header", timeout=5)
    except Exception:
        driver.refresh()
        driver.wait_for_element_visible("mat-expansion-panel-header", timeout=5)

    driver.click("mat-expansion-panel-header")

    driver.click(f"//mat-radio-button[contains(.,{xpath_literal(payload['tipo_pessoa'])})]")
    driver.type("#documento", payload['documento'])

    for sel, val in [
        ("#finalidade", payload['finalidade']),
        ('mat-select[formcontrolname="TipoCertidao"]', payload['tipo_certidao']),
    ]:
        driver.click(sel)
        driver.click(f"//mat-option[contains(.,{xpath_literal(str(val))})]")

    for attempt in range(3):
        if captured_path:
            break

        if driver.is_element_visible("mat-dialog-container"):
            blocked_by_debit = True
            log("ERROR", "Server returned debit-blocking modal")
            break

        driver.click("//button[contains(.,'Gerar PDF')]")
        log("INFO", f"Gerar PDF clicked (attempt {attempt + 1})")
        time.sleep(1.25)

finally:
    driver.quit()

def send_callback(url: str, result: dict) -> None:
    try:
        resp = requests.post(url, json=result, timeout=15)
        log("INFO", f"Callback sent (status {resp.status_code})")
    except Exception as e:
        log("ERROR", f"Callback failed: {e}")


if blocked_by_debit:
    log("ERROR", "Blocked by debit modal. No path captured.")
    result = {"status": "blocked_by_debit", "path": None, "cookie": None, "payload": payload}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if callback_url:
        send_callback(callback_url, result)
    sys.exit(1)
elif captured_path and captured_cookie:
    log("INFO", "Path and cookie captured successfully.")
    result = {"status": "success", "path": captured_path, "cookie": captured_cookie, "payload": payload}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if callback_url:
        send_callback(callback_url, result)
    sys.exit(0)
else:
    log("ERROR", "No matching request was captured.")
    result = {"status": "timeout", "path": None, "cookie": None, "payload": payload}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if callback_url:
        send_callback(callback_url, result)
    sys.exit(1)
