#!/usr/bin/env python3
"""
hCaptcha token generator for Receita Federal using SeleniumBase CDP Chrome.

Install deps:
  pip install seleniumbase requests
"""
import asyncio
import sys
import requests
from seleniumbase import cdp_driver

# Configuration and Constants
URL = "https://servicos.receitafederal.gov.br/servico/certidoes/#/home/cpf"
VALIDATE_URL = "https://servicos.receitafederal.gov.br/servico/certidoes/api/Emissao/verificar"
VALIDATE_BODY = {
    "ni": "12433377617",
    "tipoContribuinte": "PF",
    "dataNascimento": "2002-05-23",
    "tipoContribuinteEnum": "CPF",
}

ERRO_CAPTCHA_MARKER = "erro-captcha"

JS_START_TOKEN_EXECUTION = """
(() => {
    window.__sb_hcaptcha_token = null;
    window.__sb_hcaptcha_error = null;
    (async function() {
        try {
            const el = document.querySelector('[data-hcaptcha-widget-id]');
            if (!el) throw new Error('No hCaptcha element found');
            if (typeof hcaptcha === 'undefined') {
                throw new Error('hcaptcha not loaded');
            }
            const id = el.getAttribute('data-hcaptcha-widget-id');
            const result = await hcaptcha.execute(id, { async: true });
            window.__sb_hcaptcha_token = result && result.response ? result.response : null;
        } catch (e) {
            window.__sb_hcaptcha_error = String(e);
        }
    })();
    return true;
})()
"""


def log(level, message):
    print(f"[{level}] {message}", file=sys.stderr)


def _is_erro_captcha_url(url: str | None) -> bool:
    return bool(url and ERRO_CAPTCHA_MARKER in url.lower())


async def _check_erro_captcha(page) -> bool:
    """Returns True if an erro-captcha redirect was detected and we should abort."""
    href = None
    try:
        href = await page.get_current_url()
    except Exception:
        href = getattr(page, "url", None)
    if not _is_erro_captcha_url(href):
        try:
            href = await page.evaluate("location.href")
        except Exception:
            pass
    if _is_erro_captcha_url(href):
        log("ERROR", f"Erro-captcha redirect detected: {href}")
        return True
    return False


async def _generate_token() -> str | None:
    driver = await cdp_driver.start_async(uc=True, headless=False)

    try:
        page = await driver.get(URL, lang="pt-BR")
        log("INFO", "Page opened.")

        await page.select("[data-hcaptcha-widget-id]", timeout=30)
        log("INFO", "hCaptcha widget found.")

        # Wait until the hcaptcha object is available before execution.
        for _ in range(60):
            hcaptcha_ready = await page.evaluate("typeof hcaptcha !== 'undefined'")
            if hcaptcha_ready:
                break
            await asyncio.sleep(0.5)
        else:
            log("ERROR", "hCaptcha script not ready within timeout.")
            return None
        log("INFO", "hCaptcha ready. Starting token execution.")

        await page.evaluate(JS_START_TOKEN_EXECUTION)

        for _ in range(60):
            if await _check_erro_captcha(page):
                return None
            token = await page.evaluate("window.__sb_hcaptcha_token")
            if isinstance(token, str) and token.strip():
                log("INFO", "hCaptcha token generated successfully.")
                return token.strip()
            err = await page.evaluate("window.__sb_hcaptcha_error")
            if err:
                log("ERROR", f"hCaptcha execution failed: {err}")
                return None
            await asyncio.sleep(0.5)

        if await _check_erro_captcha(page):
            return None
        log("ERROR", "hCaptcha token response timed out.")
        return None
    except Exception as exc:
        log("ERROR", f"Token generation failed: {exc}")
        return None
    finally:
        driver.stop(deconstruct=True)
        # Give subprocess transports a brief moment to flush on Windows.
        await asyncio.sleep(0.2)


def validate_token(token: str) -> int:
    log("INFO", "Sending validation request.")
    try:
        resp = requests.post(
            VALIDATE_URL,
            json=VALIDATE_BODY,
            headers={
                "x-captcha-token": token,
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
            },
            timeout=30,
        )
        log("INFO", f"Validation status: {resp.status_code}")
        log("INFO", f"Validation body: {resp.text[:400]}")
        return 0 if 200 <= resp.status_code < 300 else 1
    except requests.RequestException as err:
        log("ERROR", f"Validation request failed: {err}")
        return 1


if __name__ == "__main__":
    try:
        token = asyncio.run(_generate_token())
        if token:
            print(f"Obtained Token:\n{token}")
            exit(validate_token(token))
        else:
            log("ERROR", "Token generation failed.")
            exit(1)
    except Exception as e:
        log("ERROR", f"Failed to get token: {e}")
        exit(1)
