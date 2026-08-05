"""
Cliente de Meta WhatsApp Cloud API.

Funciones:
    send_text(to, texto)              — envia mensaje de texto
    upload_media(pdf_bytes, filename) — sube un PDF y devuelve media_id
    send_document(to, media_id, ...)  — envia un documento ya subido

Las credenciales se leen de variables de entorno (.env).
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
META_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0")

BASE_URL = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}"
HEADERS = {
    "Authorization": f"Bearer {META_TOKEN}",
    "Content-Type": "application/json",
}


def _normalizar_numero(to: str) -> str:
    """Normaliza el numero a solo digitos (formato E.164 sin '+').

    Acepta jids tipo '573001112233@s.whatsapp.net', con espacios, guiones
    o el prefijo '+'. Para numeros colombianos de 10 digitos antepone 57.
    """
    numero = (to or "").split("@", 1)[0]
    numero = "".join(c for c in numero if c.isdigit())
    # Celular colombiano local (10 digitos que empiezan en 3) -> agrega 57
    if len(numero) == 10 and numero.startswith("3"):
        numero = "57" + numero
    return numero


async def _post_mensaje(payload: dict) -> dict:
    """Envía un payload a /messages. Si Meta responde error, lo REGISTRA con su
    detalle (código/motivo) antes de relanzar, para que no pase desapercibido."""
    url = f"{BASE_URL}/messages"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload, headers=HEADERS)
        if r.status_code >= 400:
            tipo = payload.get("type", "?")
            print(
                f"[whatsapp] ERROR {r.status_code} al enviar '{tipo}' a "
                f"{payload.get('to')}: {r.text}"
            )
        r.raise_for_status()
        return r.json()


async def send_text(to: str, texto: str) -> dict:
    """Envia un mensaje de texto por WhatsApp."""
    return await _post_mensaje({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalizar_numero(to),
        "type": "text",
        "text": {"preview_url": False, "body": texto},
    })


def _param_plantilla(v: str) -> str:
    """Limpia un valor para usarlo como parámetro de plantilla: Meta no admite
    saltos de línea, tabs ni >4 espacios seguidos en los parámetros."""
    s = " ".join(str(v or "").split())
    return s[:200] or "-"


async def send_template(
    to: str, nombre: str, idioma: str, parametros: list[str]
) -> dict:
    """Envía un mensaje de PLANTILLA aprobada (llega aunque el número no haya
    escrito en las últimas 24h). 'parametros' rellena {{1}}, {{2}}, ... del cuerpo."""
    componentes = []
    if parametros:
        componentes = [{
            "type": "body",
            "parameters": [
                {"type": "text", "text": _param_plantilla(p)} for p in parametros
            ],
        }]
    return await _post_mensaje({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalizar_numero(to),
        "type": "template",
        "template": {
            "name": nombre,
            "language": {"code": idioma},
            "components": componentes,
        },
    })


async def send_cta_button(to: str, cuerpo: str, boton_texto: str, url: str) -> dict:
    """Envia un mensaje interactivo con un botón que abre una URL (CTA).
    Ideal para el botón 'Pagar' con el link de Mercado Pago."""
    return await _post_mensaje({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalizar_numero(to),
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": cuerpo[:1024]},
            "action": {
                "name": "cta_url",
                "parameters": {"display_text": boton_texto[:20], "url": url},
            },
        },
    })


async def upload_media(
    contenido: bytes, filename: str, mime: str = "application/pdf"
) -> str:
    """Sube un archivo (PDF, audio, etc.) a Meta y devuelve el media_id."""
    url = f"{BASE_URL}/media"
    files = {
        "file": (filename, contenido, mime),
        "messaging_product": (None, "whatsapp"),
        "type": (None, mime),
    }
    headers_upload = {"Authorization": f"Bearer {META_TOKEN}"}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, files=files, headers=headers_upload)
        r.raise_for_status()
        return r.json()["id"]


async def get_media_url(media_id: str) -> str:
    """Obtiene la URL temporal de descarga de un media entrante."""
    url = f"https://graph.facebook.com/{META_API_VERSION}/{media_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {META_TOKEN}"})
        r.raise_for_status()
        return r.json()["url"]


async def download_media(media_url: str) -> bytes:
    """Descarga el contenido binario de un media (requiere el token en header)."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            media_url, headers={"Authorization": f"Bearer {META_TOKEN}"}
        )
        r.raise_for_status()
        return r.content


async def send_image(to: str, link: str, caption: str = "") -> dict:
    """Envia una imagen por WhatsApp usando un enlace PÚBLICO (Meta lo descarga).
    La URL debe ser HTTPS y accesible; formato JPG/PNG."""
    imagen: dict = {"link": link}
    if caption:
        imagen["caption"] = caption
    return await _post_mensaje({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalizar_numero(to),
        "type": "image",
        "image": imagen,
    })


async def send_audio(to: str, media_id: str) -> dict:
    """Envia una nota de voz / audio (ya subido) por WhatsApp."""
    return await _post_mensaje({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalizar_numero(to),
        "type": "audio",
        "audio": {"id": media_id},
    })


async def send_document(
    to: str, media_id: str, filename: str, caption: str = ""
) -> dict:
    """Envia un documento PDF (ya subido) por WhatsApp."""
    return await _post_mensaje({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _normalizar_numero(to),
        "type": "document",
        "document": {
            "id": media_id,
            "filename": filename,
            "caption": caption,
        },
    })
