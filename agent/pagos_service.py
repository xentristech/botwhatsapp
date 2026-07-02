"""
Integración de pagos con Mercado Pago (Checkout Pro / links de pago).

    crear_link_pago(cot) -> dict     # crea una preferencia y devuelve el link
    consultar_pago(payment_id) -> dict

Variables de entorno:
    MP_ACCESS_TOKEN   token de acceso de Mercado Pago (usar TEST-... en sandbox)
    MP_SANDBOX        'true' para usar el link de pruebas (default true)
    PUBLIC_BASE_URL   URL pública del servidor (para el webhook de notificación)
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
MP_SANDBOX = os.getenv("MP_SANDBOX", "true").lower() in ("1", "true", "yes", "si")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

_API = "https://api.mercadopago.com"


def _headers() -> dict:
    return {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}


async def crear_link_pago(cot: dict) -> dict:
    """Crea una preferencia de pago para una cotización y devuelve
    {'url': ..., 'preference_id': ...}. 'cot' debe tener codigo, total, items."""
    items = []
    for it in cot.get("items", []):
        items.append({
            "title": str(it.get("nombre", "Producto"))[:250],
            "quantity": int(it.get("cantidad", 1)),
            "unit_price": float(it.get("precio", 0)),
            "currency_id": "COP",
        })
    if not items:  # respaldo: un solo ítem con el total
        items = [{
            "title": f"Cotización {cot.get('codigo', '')}",
            "quantity": 1,
            "unit_price": float(cot.get("total", 0)),
            "currency_id": "COP",
        }]

    pref = {
        "items": items,
        "external_reference": cot.get("codigo", ""),
        "statement_descriptor": "PLATIM",
        "metadata": {"jid": cot.get("jid", ""), "codigo": cot.get("codigo", "")},
    }
    if cot.get("nombre") or cot.get("email"):
        pref["payer"] = {
            "name": cot.get("nombre", ""),
            "email": cot.get("email", "") or None,
        }
    if PUBLIC_BASE_URL:
        pref["notification_url"] = f"{PUBLIC_BASE_URL}/webhook/mercadopago"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{_API}/checkout/preferences", json=pref, headers=_headers()
        )
        r.raise_for_status()
        data = r.json()

    url = data.get("sandbox_init_point") if MP_SANDBOX else data.get("init_point")
    return {"url": url or data.get("init_point"), "preference_id": data.get("id")}


async def consultar_pago(payment_id: str) -> dict:
    """Consulta el estado de un pago por su id. Devuelve el JSON de Mercado Pago
    (incluye 'status' y 'external_reference')."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{_API}/v1/payments/{payment_id}", headers=_headers()
        )
        r.raise_for_status()
        return r.json()
