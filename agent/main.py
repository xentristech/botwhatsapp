"""
FastAPI: webhook de Meta WhatsApp Cloud API + API del dashboard (SSE).

Endpoints:
    GET  /                      — healthcheck
    GET  /webhook               — verificacion del webhook de Meta
    POST /webhook               — recepcion de mensajes entrantes
    GET  /api/leads             — leads registrados
    GET  /api/cotizaciones      — cotizaciones generadas
    GET  /api/mensajes          — historial de mensajes
    GET  /api/stream            — SSE en tiempo real (mensajes/eventos)
    GET  /dashboard             — UI del dashboard

Para correr:  uvicorn agent.main:app --port 8000 --reload
"""

import asyncio
import json
import os
from contextlib import suppress

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from agent.agente import procesar_mensaje
from agent.db import (
    listar_citas,
    listar_conversaciones,
    listar_cotizaciones,
    listar_leads,
    listar_mensajes,
)

load_dotenv()

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "platim2024")

app = FastAPI(title="PLATIM Agent", version="1.0.0")

# Cola de eventos para el dashboard (SSE).
_event_queue: "asyncio.Queue[dict]" = asyncio.Queue()

# Evita procesar dos veces el mismo mensaje (Meta reintenta).
_mensajes_vistos: set[str] = set()

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DASHBOARD_HTML = os.path.join(BASE_DIR, "dashboard", "index.html")


async def _publicar_evento(tipo: str, data: dict) -> None:
    with suppress(Exception):
        await _event_queue.put({"tipo": tipo, "data": data})


# ── Healthcheck ──────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "servicio": "PLATIM Agent"}


# ── Webhook Meta: verificacion (GET) ─────────────────────────────────────

@app.get("/webhook")
async def verificar_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(content="Forbidden", status_code=403)


# ── Webhook Meta: recepcion de mensajes (POST) ───────────────────────────

@app.post("/webhook")
async def recibir_webhook(request: Request):
    body = await request.json()

    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                mensajes = value.get("messages", [])
                for msg in mensajes:
                    await _procesar_mensaje_entrante(msg, value)
    except Exception as e:  # noqa: BLE001
        print(f"Error procesando webhook: {e}")

    # Meta exige responder 200 rapido.
    return JSONResponse({"status": "received"})


async def _procesar_mensaje_entrante(msg: dict, value: dict) -> None:
    msg_id = msg.get("id", "")
    if msg_id and msg_id in _mensajes_vistos:
        return
    if msg_id:
        _mensajes_vistos.add(msg_id)

    jid = msg.get("from", "")
    tipo = msg.get("type", "")
    es_audio = False  # el cliente escribio por nota de voz

    if tipo == "text":
        texto = msg.get("text", {}).get("body", "")
    elif tipo == "audio":
        es_audio = True
        texto = await _transcribir_nota_voz(msg)
    elif tipo == "interactive":
        inter = msg.get("interactive", {})
        texto = (
            inter.get("button_reply", {}).get("title")
            or inter.get("list_reply", {}).get("title")
            or ""
        )
    else:
        texto = ""

    if not jid:
        return

    # Si era audio pero no se pudo transcribir, pedir que repita.
    if es_audio and not texto:
        from agent.whatsapp import send_text

        with suppress(Exception):
            await send_text(
                jid,
                "No pude entender tu nota de voz 😅. ¿Puedes repetirla o "
                "escribirme por texto?",
            )
        return

    if not texto:
        return

    await _publicar_evento("mensaje_in", {"jid": jid, "texto": texto})

    try:
        respuesta = await procesar_mensaje(jid, texto)
    except Exception as e:  # noqa: BLE001
        print(f"Error en agente: {e}")
        respuesta = (
            "Disculpa, tuvimos un inconveniente técnico. "
            "¿Puedes repetir tu mensaje?"
        )
        from agent.whatsapp import send_text

        with suppress(Exception):
            await send_text(jid, respuesta)
        return

    # Enviar la respuesta del agente al cliente (texto siempre).
    if respuesta:
        from agent.whatsapp import send_text

        with suppress(Exception):
            await send_text(jid, respuesta)

        # Si el cliente escribio por nota de voz, responder tambien con audio.
        if es_audio:
            await _responder_con_audio(jid, respuesta)

        await _publicar_evento("mensaje_out", {"jid": jid, "texto": respuesta})


async def _transcribir_nota_voz(msg: dict) -> str:
    """Descarga la nota de voz de Meta y la transcribe a texto."""
    try:
        from agent.audio_service import transcribir_audio
        from agent.whatsapp import download_media, get_media_url

        media_id = msg.get("audio", {}).get("id", "")
        if not media_id:
            return ""
        media_url = await get_media_url(media_id)
        audio_bytes = await download_media(media_url)
        return await transcribir_audio(audio_bytes)
    except Exception as e:  # noqa: BLE001
        print(f"Error transcribiendo nota de voz: {e}")
        return ""


async def _responder_con_audio(jid: str, texto: str) -> None:
    """Genera una nota de voz (TTS) del texto y la envia por WhatsApp."""
    try:
        from agent.audio_service import texto_a_audio
        from agent.whatsapp import send_audio, upload_media

        audio_bytes = await texto_a_audio(texto)
        media_id = await upload_media(audio_bytes, "respuesta.ogg", "audio/ogg")
        await send_audio(jid, media_id)
    except Exception as e:  # noqa: BLE001
        print(f"Error enviando nota de voz: {e}")


# ── API del dashboard ────────────────────────────────────────────────────

@app.get("/api/leads")
async def api_leads(limite: int = 100):
    return listar_leads(limite)


@app.get("/api/cotizaciones")
async def api_cotizaciones(limite: int = 100):
    return listar_cotizaciones(limite)


@app.get("/api/conversaciones")
async def api_conversaciones(limite: int = 100):
    return listar_conversaciones(limite)


@app.get("/api/citas")
async def api_citas(limite: int = 100):
    return listar_citas(limite)


@app.get("/api/mensajes")
async def api_mensajes(jid: str | None = None, limite: int = 200):
    # Devuelto en orden cronologico ascendente para pintar el chat.
    msgs = listar_mensajes(jid, limite)
    return list(reversed(msgs))


@app.get("/api/stream")
async def api_stream(request: Request):
    async def generador():
        while True:
            if await request.is_disconnected():
                break
            try:
                evento = await asyncio.wait_for(_event_queue.get(), timeout=15)
                yield {"event": evento["tipo"], "data": json.dumps(evento["data"], ensure_ascii=False)}
            except asyncio.TimeoutError:
                # Keepalive para mantener viva la conexion SSE.
                yield {"event": "ping", "data": "{}"}

    return EventSourceResponse(generador())


# ── Dashboard UI ─────────────────────────────────────────────────────────

@app.get("/dashboard")
async def dashboard():
    if os.path.exists(DASHBOARD_HTML):
        return FileResponse(DASHBOARD_HTML)
    return JSONResponse({"error": "dashboard/index.html no encontrado"}, status_code=404)
