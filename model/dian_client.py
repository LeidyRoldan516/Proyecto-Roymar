"""
model/dian_client.py
Consulta la DIAN usando Playwright (navegador real).
Flujo:
  1. Abre la página del CUFE → Cloudflare Turnstile resuelve solo
  2. Intercepta la respuesta de /Document/DownloadPDF
  3. Descarga el PDF en memoria
  4. Extrae el texto con pdfplumber
  5. Parsea los campos y rellena CufeResultado
"""

import io
import os
import re
import time
import logging
import threading

import pdfplumber
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from model.cufe import CufeResultado

logger = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("pdfminer").setLevel(logging.WARNING)
logging.getLogger("pdfplumber").setLevel(logging.WARNING)

# Sesión HTTP compartida con retry y lock — evita colapso SSL bajo concurrencia
_2CAPTCHA_LOCK = threading.Lock()
# Semáforo: solo una tarea 2captcha a la vez — evita rate limiting por concurrencia
_2CAPTCHA_TASK_SEMAPHORE = threading.Semaphore(1)
_2CAPTCHA_SESSION = requests.Session()
_2CAPTCHA_SESSION.verify = False
_retry = Retry(total=5, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
_2CAPTCHA_SESSION.mount("https://", HTTPAdapter(max_retries=_retry))

# ── Constantes ────────────────────────────────────────────────────────────────

_BASE_URL  = "https://catalogo-vpfe.dian.gov.co"
_SEARCH_URL = f"{_BASE_URL}/document/searchqr"
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PAGE_TIMEOUT = 30_000   # ms
_CF_WAIT      = 15_000   # ms — Turnstile tarda más en navegador visible
_PDF_TIMEOUT  = 30        # segundos
_2CAPTCHA_TIMEOUT = 60    # segundos
_2CAPTCHA_POLL_INTERVAL = 3
_MAX_TURNSTILE_POR_CUFE = 3
_WORKER_STATE = threading.local()


# ── Punto de entrada público ──────────────────────────────────────────────────

def consultar_cufe(cufe: str) -> CufeResultado:
    """
    Consulta la DIAN para el CUFE dado usando un navegador real.
    Nunca lanza excepciones — los errores van en CufeResultado.error.
    """
    resultado = CufeResultado(cufe=cufe)
    try:
        pdf_bytes = _descargar_pdf_optimizado(cufe)
        texto     = _extraer_texto_pdf(pdf_bytes)
        _parsear_texto(texto, resultado)
        logger.debug(f"[{cufe[:16]}] tiene_datos={resultado.tiene_datos}")
    except PlaywrightTimeout as pt:
        logger.error(f"PlaywrightTimeout en CUFE {cufe[:16]}: {pt}")
        resultado.error  = f"Timeout — la DIAN no respondió a tiempo: {pt}"
        resultado.estado = "Error: Timeout"
    except Exception as e:
        logger.exception(f"Error consultando CUFE {cufe[:16]}…")
        resultado.error  = str(e)
        resultado.estado = f"Error: {e}"
    return resultado


# ── Descarga del PDF vía Playwright ──────────────────────────────────────────

def _descargar_pdf(cufe: str) -> bytes:
    """
    Flujo completo contra la DIAN:
      1. Navega a /document/searchqr  →  redirige al formulario /User/SearchDocument
      2. El CUFE ya viene pre-llenado en #DocumentKey
      3. Espera a que Cloudflare Turnstile resuelva en background
         (el campo cf-turnstile-response se rellena solo con el token)
      4. Hace clic en "Buscar"
      5. Espera que cargue la página del documento
      6. Hace clic en el botón de descarga del PDF
      7. Intercepta la respuesta binaria de /Document/DownloadPDF
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="es-CO",
            accept_downloads=True,
        )
        page = context.new_page()

        # Aplicar stealth ANTES de navegar — oculta todas las señales de automatización
        Stealth().apply_stealth_sync(page)

        # ── Capturar respuesta PDF ─────────────────────────────────
        pdf_holder: list[bytes] = []

        def _capturar_respuesta(response):
            if "DownloadPDF" in response.url and response.status == 200:
                try:
                    pdf_holder.append(response.body())
                    logger.debug(f"PDF recibido: {len(pdf_holder[0])} bytes")
                except Exception as e:
                    logger.warning(f"Error capturando cuerpo PDF: {e}")

        page.on("response", _capturar_respuesta)

        # ── Paso 1: cargar página (redirige al formulario) ─────────
        logger.debug(f"Navegando a CUFE {cufe[:16]}…")
        page.goto(
            f"{_SEARCH_URL}?documentKey={cufe}",
            wait_until="domcontentloaded",
            timeout=_PAGE_TIMEOUT,
        )
        # Espera fija para que Cloudflare Turnstile cargue e inicialice
        page.wait_for_timeout(4_000)

        # ── Paso 2: resolver Cloudflare Turnstile ──────────────────
        logger.debug("Resolviendo Cloudflare Turnstile...")

        # DEBUG — estado de la página al llegar
        page.screenshot(path="exports/debug_1_llegada.png", full_page=True)
        with open("exports/debug_1_llegada.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        _resolver_turnstile(page)

        # ── Paso 3: hacer clic en "Buscar" ─────────────────────────
        logger.debug("Haciendo clic en Buscar…")
        page.click("button.search-document")
        # Esperar que cargue la página del documento
        page.wait_for_timeout(5_000)

        # DEBUG — estado de la página tras buscar
        page.screenshot(path="exports/debug_2_documento.png", full_page=True)
        with open("exports/debug_2_documento.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        # ── Paso 4: hacer clic en el botón de PDF ──────────────────
        logger.debug("Buscando botón PDF en página del documento…")
        _click_boton_pdf(page)

        # ── Paso 5: esperar respuesta PDF ──────────────────────────
        deadline = time.time() + _PDF_TIMEOUT
        while not pdf_holder and time.time() < deadline:
            page.wait_for_timeout(400)

        browser.close()

    if not pdf_holder:
        raise RuntimeError(
            "No se recibió el PDF desde la DIAN. "
            "Verifica que el CUFE sea válido y que la DIAN esté disponible."
        )

    return pdf_holder[0]


_2CAPTCHA_API_KEY_CACHE: str | None = None

def _leer_2captcha_api_key() -> str:
    global _2CAPTCHA_API_KEY_CACHE
    if _2CAPTCHA_API_KEY_CACHE is not None:
        return _2CAPTCHA_API_KEY_CACHE
    _cargar_env_local()
    api_key = os.getenv("2CAPTCHA_API_KEY") or os.getenv("TWO_CAPTCHA_API_KEY")
    if api_key in {"", "pon_aqui_tu_api_key_de_2captcha", "TU_API_KEY"}:
        _2CAPTCHA_API_KEY_CACHE = ""
        return ""
    _2CAPTCHA_API_KEY_CACHE = api_key or ""
    return _2CAPTCHA_API_KEY_CACHE


def _descargar_pdf_optimizado(cufe: str) -> bytes:
    """
    Variante rapida: reutiliza una pagina headless por hilo y espera eventos
    concretos en vez de crear/cerrar Chromium y dormir tiempos fijos por CUFE.
    """
    page = _obtener_pagina_worker()
    _WORKER_STATE.turnstile_count = 0

    logger.debug(f"Navegando a CUFE {cufe[:16]}...")
    page.goto(
        f"{_SEARCH_URL}?documentKey={cufe}",
        wait_until="domcontentloaded",
        timeout=_PAGE_TIMEOUT,
    )
    _esperar_formulario_busqueda(page)

    logger.debug("Resolviendo Cloudflare Turnstile...")
    _guardar_debug(page, "debug_1_llegada")
    _resolver_turnstile(page)

    logger.debug("Haciendo clic en Buscar...")
    _click_buscar_y_esperar_documento(page)
    _guardar_debug(page, "debug_2_documento")

    if _hay_turnstile_pendiente(page):
        logger.debug("Turnstile adicional detectado antes de descargar PDF")
        _resolver_turnstile(page)
        logger.debug("Haciendo clic en Buscar tras resolver Turnstile adicional...")
        _click_buscar_y_esperar_documento(page)

    logger.debug("Buscando boton PDF en pagina del documento...")
    return _descargar_pdf_desde_pagina_documento(page)


def _obtener_pagina_worker():
    page = getattr(_WORKER_STATE, "page", None)
    if page and not page.is_closed():
        return page

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="es-CO",
        accept_downloads=True,
    )
    page = context.new_page()
    Stealth().apply_stealth_sync(page)

    _WORKER_STATE.playwright = pw
    _WORKER_STATE.browser = browser
    _WORKER_STATE.context = context
    _WORKER_STATE.page = page
    logger.debug("Worker DIAN inicializado con Chromium headless reutilizable")
    return page


def cerrar_worker_dian() -> None:
    for name in ("context", "browser", "playwright"):
        obj = getattr(_WORKER_STATE, name, None)
        if not obj:
            continue
        try:
            if name == "playwright":
                obj.stop()
            else:
                obj.close()
        except Exception:
            logger.debug(f"No se pudo cerrar {name} del worker", exc_info=True)

    for name in ("page", "context", "browser", "playwright"):
        if hasattr(_WORKER_STATE, name):
            delattr(_WORKER_STATE, name)


def _esperar_formulario_busqueda(page) -> None:
    page.wait_for_selector("#DocumentKey, input[name='DocumentKey']", timeout=_PAGE_TIMEOUT)
    page.wait_for_selector("button.search-document", timeout=_PAGE_TIMEOUT)
    try:
        page.wait_for_selector(
            ".cf-turnstile[data-sitekey], [data-sitekey], "
            "iframe[src*='challenges.cloudflare.com']",
            timeout=10_000,
        )
    except PlaywrightTimeout:
        logger.debug("No se detecto widget Turnstile antes de resolver")


def _click_buscar_y_esperar_documento(page) -> None:
    try:
        page.click("button.search-document", timeout=5_000)
    except PlaywrightTimeout:
        logger.debug("Boton Buscar no encontrado — posible auto-submit de Turnstile")
        _esperar_boton_pdf(page)
        return

    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except PlaywrightTimeout:
        logger.debug("La pagina no llego a networkidle tras Buscar")

    _esperar_boton_pdf(page)


def _descargar_pdf_desde_pagina_documento(page) -> bytes:
    pdf_holder: list[bytes] = []
    eventos_red: list[str] = []

    def capturar_response(response):
        _registrar_evento_pdf(response, eventos_red)
        if not _parece_pdf_response(response):
            return
        try:
            body = response.body()
            if _parece_pdf_bytes(body):
                pdf_holder.append(body)
                logger.debug(f"PDF recibido via response: {len(body)} bytes")
        except Exception as e:
            logger.debug(f"No se pudo leer response PDF: {e}")

    page.on("response", capturar_response)
    _log_boton_pdf(page)

    try:
        if _hay_turnstile_pendiente(page):
            logger.debug("Resolviendo Turnstile de descarga PDF")
            _resolver_turnstile(page)

        body = _descargar_pdf_desde_formulario(page)
        if body:
            return body

        try:
            with page.expect_download(timeout=3_000) as download_info:
                _click_boton_pdf(page)
            download = download_info.value
            path = download.path()
            if path:
                with open(path, "rb") as f:
                    body = f.read()
                if _parece_pdf_bytes(body):
                    logger.debug(f"PDF recibido via download: {len(body)} bytes")
                    return body
        except PlaywrightTimeout:
            logger.debug("El click PDF no produjo evento download")
            if _hay_turnstile_pendiente(page):
                logger.debug("Turnstile aparecio tras click PDF; resolviendo y reintentando")
                _resolver_turnstile(page)
            if not pdf_holder:
                _click_boton_pdf(page)

        deadline = time.time() + _PDF_TIMEOUT
        while time.time() < deadline:
            if pdf_holder:
                return pdf_holder[0]

            if _pagina_actual_es_pdf(page):
                body = _leer_pdf_desde_pagina_actual(page)
                if body:
                    return body

            page.wait_for_timeout(300)

        raise RuntimeError(
            "No se recibio el PDF desde la DIAN despues de hacer click. "
            f"Eventos de red PDF/documento: {eventos_red[-8:]}"
        )
    finally:
        _guardar_debug(page, "debug_pdf_fallo")
        try:
            page.remove_listener("response", capturar_response)
        except Exception:
            pass


def _descargar_pdf_desde_formulario(page) -> bytes:
    """
    DIAN renders the PDF button as a form posting to /Document/DownloadPDF.
    Submitting the form through the page request context is more reliable than
    waiting for a browser download event, especially after a second Turnstile.
    """
    try:
        form_data = page.evaluate(
            """() => {
                const turnstile = document.querySelector(
                    'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], [id$="_response"]'
                )?.value || '';

                const forms = [...document.querySelectorAll('form')]
                    .filter(form => (form.getAttribute('action') || '').toLowerCase().includes('/document/downloadpdf'));

                return forms.map(form => {
                    const data = {};
                    for (const field of form.querySelectorAll('input, textarea, select')) {
                        if (!field.name) continue;
                        data[field.name] = field.value || '';
                    }
                    if (turnstile) data.captcha = turnstile;
                    return {
                        action: form.action || form.getAttribute('action') || '/Document/DownloadPDF',
                        data
                    };
                });
            }"""
        )
    except Exception as e:
        logger.debug(f"No se pudieron leer formularios PDF: {e}")
        return b""

    for form in form_data or []:
        data = form.get("data") or {}
        if not data.get("trackId") or not data.get("token"):
            continue

        try:
            response = page.request.post(
                form.get("action") or f"{_BASE_URL}/Document/DownloadPDF",
                headers={
                    "origin": _BASE_URL,
                    "referer": page.url,
                },
                form=data,
                timeout=_PDF_TIMEOUT * 1000,
            )
            body = response.body()
            if response.ok and _parece_pdf_bytes(body):
                logger.debug(f"PDF recibido via formulario: {len(body)} bytes")
                return body

            logger.debug(
                "Formulario PDF no devolvio PDF: "
                f"{response.status} {response.headers.get('content-type', '')}"
            )
        except Exception as e:
            logger.debug(f"No se pudo descargar PDF via formulario: {e}")

    return b""


def _registrar_evento_pdf(response, eventos_red: list[str]) -> None:
    url = response.url
    lower_url = url.lower()
    content_type = response.headers.get("content-type") or ""
    if not any(x in lower_url for x in ("pdf", "download", "document")):
        return

    evento = f"{response.status} {response.request.method} {url} [{content_type}]"
    eventos_red.append(evento)
    logger.debug(f"Evento red PDF/documento: {evento}")


def _parece_pdf_response(response) -> bool:
    url = response.url.lower()
    content_type = (response.headers.get("content-type") or "").lower()
    return (
        response.status == 200
        and (
            "downloadpdf" in url
            or url.endswith(".pdf")
            or "application/pdf" in content_type
            or "octet-stream" in content_type
        )
    )


def _parece_pdf_bytes(body: bytes) -> bool:
    return bool(body) and body[:4] == b"%PDF"


def _pagina_actual_es_pdf(page) -> bool:
    try:
        content_type = page.evaluate("() => document.contentType || ''")
        return "pdf" in (content_type or "").lower() or page.url.lower().endswith(".pdf")
    except Exception:
        return False


def _leer_pdf_desde_pagina_actual(page) -> bytes:
    try:
        response = page.request.get(page.url, timeout=_PDF_TIMEOUT * 1000)
        body = response.body()
        if response.ok and _parece_pdf_bytes(body):
            logger.debug(f"PDF recibido via pagina actual: {len(body)} bytes")
            return body
    except Exception as e:
        logger.debug(f"No se pudo leer PDF desde pagina actual: {e}")
    return b""


def _log_boton_pdf(page) -> None:
    try:
        info = page.evaluate(
            """() => {
                const all = [...document.querySelectorAll('a, button, [onclick]')];
                const candidates = all.filter(el => {
                    const txt = (el.textContent || '').toLowerCase();
                    const onclick = (el.getAttribute('onclick') || '').toLowerCase();
                    const href = (el.getAttribute('href') || '').toLowerCase();
                    return txt.includes('pdf') ||
                           txt.includes('descargar') ||
                           onclick.includes('pdf') ||
                           href.includes('pdf');
                });
                return candidates.slice(0, 8).map(target => ({
                    tag: target.tagName,
                    text: (target.textContent || '').trim().slice(0, 80),
                    href: target.getAttribute('href') || '',
                    onclick: target.getAttribute('onclick') || '',
                    classes: target.getAttribute('class') || '',
                    id: target.getAttribute('id') || ''
                }));
            }"""
        )
        logger.debug(f"Candidatos PDF detectados: {info}")
    except Exception:
        logger.debug("No se pudo inspeccionar boton PDF", exc_info=True)


def _esperar_boton_pdf(page) -> None:
    selectores = [
        "button:has-text('PDF')",
        "a:has-text('PDF')",
        "button:has-text('Descargar')",
        "a:has-text('Descargar')",
        "[onclick*='DownloadPDF']",
        "[href*='DownloadPDF']",
    ]
    for selector in selectores:
        try:
            page.wait_for_selector(selector, timeout=2_000, state="visible")
            return
        except PlaywrightTimeout:
            continue

    logger.debug("No se encontro boton PDF por selector antes del intento de click")


def _hay_turnstile_pendiente(page) -> bool:
    try:
        return bool(page.evaluate(
            """() => {
                const widgets = [...document.querySelectorAll(
                    '.cf-turnstile[data-sitekey], [data-sitekey], iframe[src*="challenges.cloudflare.com"]'
                )];
                const responses = [...document.querySelectorAll(
                    '[name="cf-turnstile-response"], [id$="_response"]'
                )];
                const hasEmptyResponse = responses.some(el => !el.value || el.value.length < 10);
                return widgets.length > 0 && (responses.length === 0 || hasEmptyResponse);
            }"""
        ))
    except Exception:
        return False


def _guardar_debug(page, nombre: str) -> None:
    es_fallo = "fallo" in nombre
    if not es_fallo and os.getenv("DIAN_DEBUG", "").lower() not in {"1", "true", "yes"}:
        return

    thread_id = threading.get_ident()
    prefix = os.path.join("exports", f"{nombre}_{thread_id}")
    page.screenshot(path=f"{prefix}.png", full_page=True)
    with open(f"{prefix}.html", "w", encoding="utf-8") as f:
        f.write(page.content())


def _cargar_env_local() -> None:
    env_path = os.path.join(_PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _resolver_turnstile(page) -> None:
    """
    Resuelve Cloudflare Turnstile con 2captcha e inyecta el token en la pagina.
    Si no hay API key, conserva el fallback anterior de esperar resolucion local.
    """
    turnstile_count = getattr(_WORKER_STATE, "turnstile_count", 0)
    if turnstile_count >= _MAX_TURNSTILE_POR_CUFE:
        raise RuntimeError(
            f"Limite de {_MAX_TURNSTILE_POR_CUFE} Turnstile alcanzado para este CUFE"
        )
    _WORKER_STATE.turnstile_count = turnstile_count + 1

    api_key = _leer_2captcha_api_key()
    if not api_key:
        logger.warning(
            "2CAPTCHA_API_KEY no configurada; esperando token automatico de Turnstile"
        )
        _esperar_turnstile_automatico(page)
        return

    sitekey = _extraer_turnstile_sitekey(page)
    if not sitekey:
        raise RuntimeError("No se encontro el sitekey de Cloudflare Turnstile")

    pageurl = page.url
    logger.debug(f"Sitekey Turnstile detectado: {sitekey}")
    token = _resolver_turnstile_2captcha(api_key, sitekey, pageurl)
    _inyectar_turnstile_token(page, token)

    try:
        page.wait_for_function(
            """() => {
                const el = document.querySelector(
                    '[name="cf-turnstile-response"], [id$="_response"]'
                );
                return el && el.value && el.value.length > 10;
            }""",
            timeout=8_000,
        )
        logger.debug("Token Turnstile inyectado correctamente")
    except PlaywrightTimeout:
        raise RuntimeError("No fue posible inyectar el token de Turnstile")


def _esperar_turnstile_automatico(page) -> None:
    try:
        page.wait_for_function(
            """() => {
                const el = document.querySelector('[id$="_response"]');
                return el && el.value && el.value.length > 10;
            }""",
            timeout=_CF_WAIT,
        )
        logger.debug("Turnstile resuelto automaticamente")
    except PlaywrightTimeout:
        logger.debug("Turnstile no confirmado; intentando de todas formas")


def _extraer_turnstile_sitekey(page) -> str:
    return page.evaluate(
        """() => {
            const widget = document.querySelector(
                '.cf-turnstile[data-sitekey], [data-sitekey]'
            );
            if (widget?.dataset?.sitekey) return widget.dataset.sitekey;

            const iframe = [...document.querySelectorAll('iframe')]
                .find(el => (el.src || '').includes('challenges.cloudflare.com'));
            if (!iframe) return '';

            try {
                const url = new URL(iframe.src);
                return url.searchParams.get('sitekey') || '';
            } catch (_) {
                return '';
            }
        }"""
    )


def _resolver_turnstile_2captcha(api_key: str, sitekey: str, pageurl: str) -> str:
    # Semáforo: solo una tarea 2captcha a la vez — evita rate limiting
    with _2CAPTCHA_TASK_SEMAPHORE:
        # Lock: solo un hilo usa la sesión HTTP a la vez — evita colapso SSL
        with _2CAPTCHA_LOCK:
            crear = _2CAPTCHA_SESSION.post(
                "https://2captcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "turnstile",
                    "sitekey": sitekey,
                    "pageurl": pageurl,
                    "json": 1,
                },
                timeout=30,
            )
            crear.raise_for_status()
            payload = crear.json()
            if payload.get("status") != 1:
                raise RuntimeError(f"2captcha no acepto el Turnstile: {payload.get('request')}")
            captcha_id = payload["request"]

        logger.debug(f"2captcha acepto Turnstile id={captcha_id}")

        # Espera inicial — 2captcha suele tardar 2-3s en Turnstile
        time.sleep(2)

        deadline = time.time() + _2CAPTCHA_TIMEOUT
        while time.time() < deadline:
            with _2CAPTCHA_LOCK:
                respuesta = _2CAPTCHA_SESSION.get(
                    "https://2captcha.com/res.php",
                    params={
                        "key": api_key,
                        "action": "get",
                        "id": captcha_id,
                        "json": 1,
                    },
                    timeout=30,
                )
                respuesta.raise_for_status()
                data = respuesta.json()

            if data.get("status") == 1:
                logger.debug("2captcha devolvio token Turnstile")
                return data["request"]

            estado = data.get("request")
            if estado not in {"CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"}:
                raise RuntimeError(f"2captcha fallo resolviendo Turnstile: {estado}")

            time.sleep(1)  # polling cada 1s en vez de 3s

        raise RuntimeError("2captcha no devolvio token antes del timeout")


def _inyectar_turnstile_token(page, token: str) -> None:
    page.evaluate(
        """(token) => {
            const selectors = [
                'input[name="cf-turnstile-response"]',
                'textarea[name="cf-turnstile-response"]',
                'input[name="g-recaptcha-response"]',
                'textarea[name="g-recaptcha-response"]',
                '[id$="_response"]'
            ];

            const fields = selectors
                .flatMap(selector => [...document.querySelectorAll(selector)]);

            if (!fields.length) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'cf-turnstile-response';
                document.querySelector('form')?.appendChild(input);
                fields.push(input);
            }

            for (const field of fields) {
                field.value = token;
                field.setAttribute('value', token);
                field.dispatchEvent(new Event('input', { bubbles: true }));
                field.dispatchEvent(new Event('change', { bubbles: true }));
            }

            const widget = document.querySelector('.cf-turnstile, [data-sitekey]');
            const callbackName = widget?.getAttribute('data-callback');
            if (callbackName && typeof window[callbackName] === 'function') {
                window[callbackName](token);
            }
        }""",
        token,
    )


def _click_boton_pdf(page) -> None:
    """
    Intenta encontrar y pulsar el botón de descarga del PDF probando
    múltiples selectores en orden de especificidad.
    """
    selectores = [
        "a:has-text('Descargar PDF')",
        "button:has-text('Descargar PDF')",
        "a:has-text('Descargar')",
        "button:has-text('Descargar')",
        # Por texto visible
        "button:has-text('PDF')",
        "a:has-text('PDF')",
        "button:has-text('Representación')",
        "a:has-text('Representación')",
        # Por atributos comunes
        "[onclick*='DownloadPDF']",
        "[onclick*='download']",
        "[href*='DownloadPDF']",
        # Por id/clase típicos
        "#btnPdf", "#downloadPdf", "#btnDownload",
        ".btn-pdf", ".btn-download", ".download-pdf",
        # Link genérico void — último recurso específico
        "a[href='javascript:void(0);']",
    ]

    for selector in selectores:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                el.scroll_into_view_if_needed()
                el.click(force=True)
                logger.debug(f"Botón PDF encontrado con: {selector}")
                return
        except Exception:
            continue

    # Último recurso: evaluar JS para encontrar cualquier elemento descargable
    logger.debug("Buscando botón PDF via JS evaluation")
    page.evaluate("""
        () => {
            const all = [...document.querySelectorAll('a, button, [onclick]')];
            const target = all.find(el => {
                const txt = (el.textContent || '').toLowerCase();
                const onclick = (el.getAttribute('onclick') || '').toLowerCase();
                return txt.includes('pdf') ||
                       txt.includes('descargar') ||
                       txt.includes('representación') ||
                       onclick.includes('pdf') ||
                       onclick.includes('download');
            });
            if (target) target.click();
        }
    """)


# ── Extracción de texto del PDF ───────────────────────────────────────────────

def _extraer_texto_pdf(pdf_bytes: bytes) -> str:
    """
    Extrae el texto de todas las páginas del PDF con pdfplumber.
    """
    paginas = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, pagina in enumerate(pdf.pages):
            texto = pagina.extract_text(x_tolerance=3, y_tolerance=3)
            if texto:
                paginas.append(texto)
                logger.debug(f"Página {i+1}: {len(texto)} caracteres extraídos")
    return "\n".join(paginas)


# ── Parser del texto del PDF ──────────────────────────────────────────────────

def _parsear_texto(texto: str, r: CufeResultado) -> None:
    """
    Rellena CufeResultado a partir del texto crudo del PDF de la DIAN.

    El PDF estándar de la DIAN tiene secciones como:
      - Encabezado: tipo de documento, número, fecha
      - Emisor: NIT, razón social, dirección
      - Adquirente: NIT, nombre
      - Cuerpo: ítems
      - Pie: subtotal, impuestos, total

    Los patrones se ajustan según el texto real observado.
    Activar logging DEBUG para ver el texto crudo y afinar.
    """
    logger.debug(f"Texto PDF (primeros 800 chars):\n{texto[:800]}")

    texto = _normalizar_texto_pdf(texto)
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    pares  = _construir_pares(lineas)
    datos_documento = _seccion(texto, "Datos del Documento", "Datos del Emisor / Vendedor")
    datos_emisor = _seccion(texto, "Datos del Emisor / Vendedor", "Datos del Adquiriente / Comprador")
    datos_adquiriente = _seccion(texto, "Datos del Adquiriente / Comprador", "Detalles de Productos")
    datos_totales = _seccion(texto, "Datos Totales")

    # ── Tipo de documento ──────────────────────────────────────────
    r.tipo_documento = _detectar_tipo(texto) or _limpiar_valor(
        _get_campo(pares, "tipo de documento", "tipo documento", "documento")
    )

    # ── Numeración ─────────────────────────────────────────────────
    numero_factura = _valor_etiqueta(
        datos_documento, "Número de Factura", "Forma de pago", "Fecha de Emisión"
    ) or _get_campo(pares, "número", "no.", "folio", "factura no", "numero", "consecutivo")
    r.folio = _limpiar_valor(numero_factura)
    r.prefijo = _limpiar_valor(_get_campo(pares, "prefijo")) or _prefijo_desde_folio(r.folio)

    # ── Fechas ─────────────────────────────────────────────────────
    r.fecha_emision = _limpiar_valor(
        _valor_etiqueta(datos_documento, "Fecha de Emisión", "Medio de Pago", "Fecha de Vencimiento")
        or _get_fecha(pares, texto, "fecha de expedición", "fecha expedición",
                      "fecha de emisión", "fecha emisión", "fecha de generación", "fecha")
    )
    r.fecha_recepcion = _limpiar_valor(
        _fecha_despues_etiqueta_partida(datos_totales, "Documento validado por la", "DIAN")
        or _get_fecha(pares, texto, "fecha de recepción", "fecha recepción", "fecha recibo")
    )

    # ── Emisor ─────────────────────────────────────────────────────
    r.nit_emisor = _limpiar_documento(
        _valor_etiqueta(datos_emisor, "Nit del Emisor", "País", "Tipo de Contribuyente")
        or _get_campo(pares, "nit emisor", "nit vendedor", "nit del emisor",
                      "nit/cc emisor", "número de identificación tributaria")
    )
    r.nombre_emisor = _limpiar_valor(
        _valor_etiqueta(datos_emisor, "Razón Social", "Nombre Comercial", "Nit del Emisor")
        or _get_campo(pares, "razón social", "nombre emisor", "vendedor",
                      "emisor", "empresa", "nombre o razón social")
    )

    # ── Receptor / Adquirente ──────────────────────────────────────
    r.nit_receptor = _limpiar_documento(
        _valor_etiqueta(datos_adquiriente, "Número Documento", "Departamento", "Tipo de Contribuyente")
        or _get_campo(pares, "nit adquirente", "nit comprador", "nit receptor",
                      "nit del adquirente", "identificación adquirente", "documento adquirente")
    )
    r.nombre_receptor = _limpiar_valor(
        _valor_etiqueta(datos_adquiriente, "Nombre o Razón Social", "Tipo de Documento")
        or _get_campo(pares, "adquirente", "comprador", "nombre adquirente",
                      "receptor", "razón social adquirente")
    )

    # ── Pago ───────────────────────────────────────────────────────
    r.forma_pago = _limpiar_valor(
        _valor_etiqueta(datos_documento, "Forma de pago", "Fecha de Emisión")
        or _get_campo(pares, "forma de pago", "forma pago", "condición de pago")
    )
    r.medio_pago = _limpiar_valor(
        _valor_etiqueta(datos_documento, "Medio de Pago", "Fecha de Vencimiento")
        or _get_campo(pares, "medio de pago", "medio pago")
    )
    r.divisa = _limpiar_valor(_valor_moneda(datos_totales) or _get_campo(pares, "moneda", "divisa") or "COP")

    # ── Impuestos ──────────────────────────────────────────────────
    r.iva = _num_linea(datos_totales, "IVA") or _get_num(
        pares, texto, "iva", "impuesto iva", "valor iva", "impuesto sobre las ventas"
    )
    r.ica = _num_linea(datos_totales, "ICA") or 0.0
    r.inc = _num_linea(datos_totales, "INC") or _get_num(pares, texto, "inc", "impuesto nacional al consumo")
    r.inc_bolsas = _num_linea(datos_totales, "Bolsas")
    r.timbre = _num_linea(datos_totales, "Timbre") or _get_num(pares, texto, "timbre")
    r.rete_iva = _num_linea(datos_totales, "Rete IVA") or _get_num(
        pares, texto, "rete iva", "retención iva", "reteiva"
    )
    r.rete_renta = _num_linea(datos_totales, "Rete fuente") or _get_num(
        pares, texto, "rete renta", "retención en la fuente", "retefuente", "reterenta"
    )
    r.rete_ica = _num_linea(datos_totales, "Rete ICA") or _get_num(
        pares, texto, "rete ica", "retención ica", "reteica"
    )

    # ── Total ──────────────────────────────────────────────────────
    r.total = _num_linea(datos_totales, "Total factura") or _get_num(
        pares, texto, "total factura", "valor total", "total a pagar",
        "total comprobante", "total"
    )

    # ── Estado ─────────────────────────────────────────────────────
    r.estado = _get_campo(pares, "estado", "estado dian") or "Aprobado"

    r.tiene_datos = bool(r.folio or r.nit_emisor or r.nombre_emisor or r.total)


# ── Helpers del parser ────────────────────────────────────────────────────────

def _normalizar_texto_pdf(texto: str) -> str:
    return (
        texto.replace("\u3164", " ")
        .replace("\xa0", " ")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _seccion(texto: str, inicio: str, fin: str = "") -> str:
    pattern = re.compile(re.escape(inicio), re.IGNORECASE)
    match = pattern.search(texto)
    if not match:
        return ""

    start = match.end()
    end = len(texto)
    if fin:
        fin_match = re.search(re.escape(fin), texto[start:], re.IGNORECASE)
        if fin_match:
            end = start + fin_match.start()

    return texto[start:end].strip()


def _valor_etiqueta(texto: str, etiqueta: str, *siguientes: str) -> str:
    label = re.compile(re.escape(etiqueta) + r"\s*:\s*", re.IGNORECASE)
    stop_patterns = [re.compile(re.escape(s) + r"\s*:", re.IGNORECASE) for s in siguientes]
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]

    for i, linea in enumerate(lineas):
        match = label.search(linea)
        if not match:
            continue

        valor = linea[match.end():]
        corte = len(valor)
        for stop in stop_patterns:
            stop_match = stop.search(valor)
            if stop_match:
                corte = min(corte, stop_match.start())
        valor = valor[:corte].strip()
        if valor:
            return _limpiar_valor(valor)

        if i + 1 < len(lineas):
            return _limpiar_valor(lineas[i + 1])

    return ""


def _valor_despues_linea(texto: str, etiqueta: str) -> str:
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    etiqueta_lower = etiqueta.lower()
    for i, linea in enumerate(lineas):
        if etiqueta_lower in linea.lower() and i + 1 < len(lineas):
            return _limpiar_valor(lineas[i + 1])
    return ""


def _fecha_despues_etiqueta_partida(texto: str, *partes: str) -> str:
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    partes_lower = [p.lower() for p in partes]
    fecha = re.compile(r"\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2}:\d{2})?")

    for i, linea in enumerate(lineas):
        ventana = " ".join(lineas[i:i + len(partes_lower) + 1]).lower()
        if not all(parte in ventana for parte in partes_lower):
            continue

        for siguiente in lineas[i + 1:i + 6]:
            match = fecha.search(siguiente)
            if match:
                return match.group(0)

    return ""


def _valor_moneda(texto: str) -> str:
    match = re.search(r"\bMONEDA\b\s+\bMONEDA\b\s+([A-Z]{3})\b", texto, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    match = re.search(r"\b(COP|USD|EUR)\b", texto, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def _num_linea(texto: str, etiqueta: str) -> float:
    etiqueta_lower = etiqueta.lower()
    for linea in texto.splitlines():
        limpia = linea.replace("$", " ")
        if etiqueta_lower not in limpia.lower():
            continue

        numeros = re.findall(r"(?<!\d)(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{2})?(?!\d)", limpia)
        if numeros:
            return _a_float(numeros[-1])

    return 0.0


def _limpiar_valor(valor: str) -> str:
    if not valor:
        return ""
    valor = re.sub(r"\s+", " ", str(valor)).strip(" :|-")
    return valor.strip()


def _limpiar_documento(valor: str) -> str:
    valor = _limpiar_valor(valor)
    match = re.search(r"\d[\d.-]*", valor)
    return match.group(0).replace(".", "").replace("-", "") if match else valor


def _prefijo_desde_folio(folio: str) -> str:
    match = re.match(r"([A-Za-z]+)", folio or "")
    return match.group(1).upper() if match else ""


def _construir_pares(lineas: list[str]) -> dict[str, str]:
    """
    Construye {clave_lower: valor} a partir de las líneas del PDF.
    Soporta:
      A) "Clave: Valor"   — todo en la misma línea
      B) "Clave:"         — valor en la línea siguiente
    """
    pares: dict[str, str] = {}
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        if ":" in linea:
            partes = linea.split(":", 1)
            clave  = partes[0].strip().lower()
            valor  = partes[1].strip()
            if clave and len(clave) < 60:
                if valor:
                    pares.setdefault(clave, valor)
                elif i + 1 < len(lineas):
                    pares.setdefault(clave, lineas[i + 1].strip())
                    i += 1
        i += 1
    return pares


def _get_campo(pares: dict, *claves: str) -> str:
    """Busca el primer valor que coincida exacta o parcialmente con alguna clave."""
    for clave in claves:
        if clave in pares:
            return pares[clave]
        for k, v in pares.items():
            if clave in k:
                return v
    return ""


def _get_fecha(pares: dict, texto: str, *claves: str) -> str:
    """Extrae una fecha: primero en pares, luego regex en texto crudo."""
    valor = _get_campo(pares, *claves)
    if valor:
        # Normalizar: tomar solo la parte de fecha
        return valor.split("T")[0].split(" ")[0]

    for clave in claves:
        patron = re.compile(
            re.escape(clave) + r'[:\s]+(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})',
            re.IGNORECASE,
        )
        m = patron.search(texto)
        if m:
            return m.group(1)
    return ""


def _get_num(pares: dict, texto: str, *claves: str) -> float:
    """Extrae un número: primero en pares, luego regex en texto crudo."""
    valor = _get_campo(pares, *claves)
    if valor:
        return _a_float(valor)

    for clave in claves:
        patron = re.compile(
            re.escape(clave) + r'[\s:$]*([\d.,]+)',
            re.IGNORECASE,
        )
        m = patron.search(texto)
        if m:
            return _a_float(m.group(1))
    return 0.0


def _a_float(s: str) -> float:
    """
    Convierte string de moneda a float.
    La DIAN usa formato colombiano: punto=miles, coma=decimal.
      '1.250.000,50' → 1250000.5
      '1.500.000'    → 1500000.0
      '285000'       → 285000.0
    Si la coma aparece antes que el punto (formato anglosajón), se trata
    la coma como separador de miles y el punto como decimal.
    """
    if not s:
        return 0.0
    limpio = re.sub(r'[^\d,.]', '', s)
    if not limpio:
        return 0.0

    pos_punto = limpio.rfind('.')
    pos_coma  = limpio.rfind(',')

    if pos_punto > 0 and pos_coma > 0:
        if pos_coma > pos_punto:
            # Formato colombiano: 1.250.000,50
            limpio = limpio.replace('.', '').replace(',', '.')
        else:
            # Formato anglosajón: 1,250,000.50
            limpio = limpio.replace(',', '')
    elif pos_punto > 0 and pos_coma < 0:
        # Solo puntos: si hay más de uno, o si la parte tras el último punto
        # tiene exactamente 3 dígitos → separador de miles (1.500.000 o 1.500)
        partes = limpio.split('.')
        if len(partes) > 2 or (len(partes) == 2 and len(partes[-1]) == 3):
            limpio = limpio.replace('.', '')   # quitar separadores de miles
        # Si solo hay un punto y la parte decimal ≠ 3 dígitos → es decimal normal
    elif pos_coma > 0 and pos_punto < 0:
        # Solo coma: puede ser decimal (150,50) o miles (1,500)
        # Si hay exactamente 3 dígitos tras la coma → miles; sino → decimal
        parte_tras_coma = limpio.split(',')[-1]
        if len(parte_tras_coma) == 3:
            limpio = limpio.replace(',', '')   # separador de miles
        else:
            limpio = limpio.replace(',', '.')  # decimal

    try:
        return float(limpio)
    except ValueError:
        return 0.0


def _detectar_tipo(texto: str) -> str:
    t = texto.lower()
    if "nota crédito"  in t or "nota credito"  in t: return "Nota Crédito"
    if "nota débito"   in t or "nota debito"   in t: return "Nota Débito"
    if "documento soporte"                      in t: return "Documento soporte con no obligados"
    if "application response"                   in t: return "Application response"
    return "Factura electrónica"