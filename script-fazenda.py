import argparse
import html
import json
import os
import sys
import time
from curl_cffi import requests
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
    parser = argparse.ArgumentParser(description="Fazenda Certificate Generator")
    parser.add_argument("--payload", required=True, help="JSON payload")
    args = parser.parse_args()

    data = json.loads(args.payload)
    if not isinstance(data, dict):
        raise ValueError("Invalid payload: expected JSON object")

    required_keys = ["tipo_pessoa", "documento", "finalidade", "tipo_certidao"]
    for key in required_keys:
        if not str(data.get(key, "")).strip():
            raise ValueError(f"Invalid payload: '{key}' is required")

    return {k: str(data[k]).strip() for k in required_keys}

payload = parse_payload()
output_filename = f"Fazenda_Certificate_{payload['tipo_pessoa']}_{payload['documento']}.pdf"

captured_path = None
captured_cookie = None
blocked_by_debit = False

os.environ.pop("WAYLAND_DISPLAY", None)
os.environ["GDK_BACKEND"] = "x11"

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

result = {
    "status": "timeout",
    "filename": output_filename,
    "message": "No matching request was captured.",
    "pdf_size_bytes": None,
}

if blocked_by_debit:
    result = {
        "status": "blocked_by_debit",
        "filename": None,
        "message": "Certificate cannot be generated, there are debits",
        "pdf_size_bytes": None,
    }
elif captured_path and captured_cookie:
    log("INFO", "Fetching PDF via capture match")
    cf_clearance = next(
        (p.strip().split("=", 1)[1] for p in captured_cookie.split(";") if p.strip().startswith("cf_clearance=")),
        None,
    )

    if not cf_clearance:
        result.update({
            "status": "error",
            "message": "cf_clearance cookie was not present in captured request.",
        })
    else:
        full_url = "https://ww1.receita.fazenda.df.gov.br" + html.unescape(captured_path)
        response = requests.get(
            full_url,
            headers={"Cookie": f"cf_clearance={cf_clearance}"},
            impersonate="chrome",
        )

        if response.status_code == 200:
            pdf_bytes = response.content or b""
            result.update({
                "status": "success",
                "message": None,
                "pdf_size_bytes": len(pdf_bytes),
            })
            log("INFO", "Success request status 200")
        else:
            result.update({
                "status": "error",
                "message": f"Turnstile replay failed: HTTP {response.status_code}",
            })
            log("ERROR", f"Request failed (status {response.status_code})")
else:
    log("ERROR", "No matching request was captured.")

print(json.dumps(result, ensure_ascii=False), flush=True)
sys.exit(0 if result["status"] in ("success", "blocked_by_debit") else 1)
