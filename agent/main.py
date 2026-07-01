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
from fastapi import FastAPI, File, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from agent.agente import procesar_mensaje
from pydantic import BaseModel

from agent import catalogo
from agent.db import (
    crear_producto,
    es_modo_humano,
    existe_producto_codigo,
    listar_citas,
    listar_conversaciones,
    listar_cotizaciones,
    listar_leads,
    listar_mensajes,
    registrar_mensaje,
    set_etiqueta,
    set_modo_humano,
    set_override,
)

load_dotenv()

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "platim2024")

app = FastAPI(title="PLATIM Agent", version="1.0.0")

# Cola de eventos para el dashboard (SSE).
_event_queue: "asyncio.Queue[dict]" = asyncio.Queue()

# Evita procesar dos veces el mismo mensaje (Meta reintenta).
_mensajes_vistos: set[str] = set()

# Agrupador de mensajes (debounce): junta mensajes seguidos del mismo cliente y
# responde una sola vez. Segundos de espera configurables por entorno.
DEBOUNCE_SEGUNDOS = float(os.getenv("DEBOUNCE_SEGUNDOS", "6"))
_buffers: dict[str, list[str]] = {}
_tareas: dict[str, "asyncio.Task"] = {}

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

    # Registrar el mensaje entrante (dashboard/historial) y avisar en vivo.
    registrar_mensaje(jid, "in", texto, "cliente")
    await _publicar_evento("mensaje_in", {"jid": jid, "texto": texto})

    # Si un humano tomó el control, el bot NO responde.
    if es_modo_humano(jid):
        return

    # Notas de voz: responder de inmediato. Texto: agrupar (debounce) para no
    # contestar a cada mensajito cuando el cliente escribe en varios seguidos.
    if es_audio:
        await _ejecutar_bot(jid, texto, es_audio=True)
        return

    _buffers.setdefault(jid, []).append(texto)
    tarea = _tareas.get(jid)
    if tarea and not tarea.done():
        tarea.cancel()
    _tareas[jid] = asyncio.create_task(_procesar_con_espera(jid))


async def _procesar_con_espera(jid: str) -> None:
    """Espera a que el cliente termine de escribir y responde una sola vez."""
    try:
        await asyncio.sleep(DEBOUNCE_SEGUNDOS)
    except asyncio.CancelledError:
        return  # llegó otro mensaje: esta tanda se descarta, sigue la nueva
    textos = _buffers.pop(jid, [])
    _tareas.pop(jid, None)
    if not textos or es_modo_humano(jid):
        return
    await _ejecutar_bot(jid, " ".join(textos), es_audio=False)


async def _ejecutar_bot(jid: str, texto: str, es_audio: bool = False) -> None:
    """Corre el agente y envía la respuesta (texto + voz si aplica).
    No registra el 'in' (ya se registró al recibir cada mensaje)."""
    from agent.whatsapp import send_text

    try:
        respuesta = await procesar_mensaje(jid, texto, registrar_in=False)
    except Exception as e:  # noqa: BLE001
        print(f"Error en agente: {e}")
        with suppress(Exception):
            await send_text(
                jid,
                "Disculpa, tuvimos un inconveniente técnico. "
                "¿Puedes repetir tu mensaje?",
            )
        return

    if respuesta:
        with suppress(Exception):
            await send_text(jid, respuesta)
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


class EnviarBody(BaseModel):
    jid: str
    texto: str


@app.post("/api/enviar")
async def api_enviar(body: EnviarBody):
    """Envia un mensaje al cliente ESCRITO POR UN HUMANO desde el dashboard.
    Al hacerlo, activa el modo humano (pausa el bot) para esa conversación."""
    jid = (body.jid or "").strip()
    texto = (body.texto or "").strip()
    if not jid or not texto:
        return JSONResponse({"error": "Faltan jid o texto"}, status_code=400)

    from agent.whatsapp import send_text

    try:
        await send_text(jid, texto)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"No se pudo enviar: {e}"}, status_code=502)

    registrar_mensaje(jid, "out", texto, "humano")
    set_modo_humano(jid, True)  # tomar control -> pausar bot
    await _publicar_evento(
        "mensaje_out", {"jid": jid, "texto": texto, "origen": "humano"}
    )
    return {"ok": True, "humano": True}


class ModoBody(BaseModel):
    jid: str
    humano: bool


@app.post("/api/modo")
async def api_modo(body: ModoBody):
    """Activa/desactiva el modo humano (pausa/reanuda el bot) para un jid."""
    jid = (body.jid or "").strip()
    if not jid:
        return JSONResponse({"error": "Falta jid"}, status_code=400)
    set_modo_humano(jid, body.humano)
    await _publicar_evento("modo", {"jid": jid, "humano": body.humano})
    return {"ok": True, "jid": jid, "humano": body.humano}


@app.get("/api/productos")
async def api_productos(q: str = "", categoria: str = "", limite: int = 60):
    """Lista productos (con ajustes aplicados) para el editor del dashboard.
    Incluye los sin stock (para poder marcarlos/reactivarlos)."""
    prods = catalogo.buscar(q, categoria, incluir_sin_stock=True)[:limite]
    return [
        {
            "codigo": p["codigo"],
            "nombre": p["nombre"],
            "categoria": p["categoria"],
            "precio_publico": p["precio_publico"],
            "precio_mayoreo": p["precio_mayoreo"],
            "observaciones": p.get("observaciones", ""),
            "sin_stock": bool(p.get("sin_stock")),
        }
        for p in prods
    ]


@app.get("/api/productos/export")
async def api_productos_export():
    """Descarga el catálogo completo en Excel (.xlsx)."""
    from agent.excel_service import exportar_xlsx

    data = exportar_xlsx()
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=productos_platim.xlsx"},
    )


@app.post("/api/productos/import")
async def api_productos_import(archivo: UploadFile = File(...)):
    """Sube un Excel para crear/actualizar productos en masa."""
    from agent.excel_service import importar_xlsx

    contenido = await archivo.read()
    try:
        resultado = importar_xlsx(contenido)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"error": f"No se pudo leer el Excel: {e}"}, status_code=400
        )
    if resultado.get("error"):
        return JSONResponse(resultado, status_code=400)
    return resultado


class ProductoNuevoBody(BaseModel):
    nombre: str
    categoria: str = "General"
    precio_publico: int = 0
    precio_mayoreo: int = 0
    codigo: str | None = None
    descripcion: str | None = None
    uso: str | None = None


@app.post("/api/producto/nuevo")
async def api_producto_nuevo(body: ProductoNuevoBody):
    """Crea un producto nuevo desde el dashboard; el bot lo incluye al instante."""
    if not (body.nombre or "").strip():
        return JSONResponse({"error": "El nombre es obligatorio"}, status_code=400)
    codigo = (body.codigo or "").strip().upper()
    if codigo and (catalogo.obtener(codigo) or existe_producto_codigo(codigo)):
        return JSONResponse({"error": "Ese código ya existe"}, status_code=400)
    nuevo = crear_producto({
        "codigo": codigo,
        "nombre": body.nombre.strip(),
        "categoria": (body.categoria or "General").strip(),
        "precio_publico": body.precio_publico,
        "precio_mayoreo": body.precio_mayoreo,
        "descripcion": (body.descripcion or "").strip(),
        "uso": (body.uso or "").strip(),
    })
    return {"ok": True, "codigo": nuevo}


class ProductoBody(BaseModel):
    codigo: str
    precio_publico: int | None = None
    precio_mayoreo: int | None = None
    nombre: str | None = None
    observaciones: str | None = None
    sin_stock: bool | None = None


@app.post("/api/producto")
async def api_producto(body: ProductoBody):
    """Guarda un ajuste de producto (precio/nombre) hecho desde el dashboard."""
    codigo = (body.codigo or "").strip().upper()
    if not codigo or not catalogo.obtener(codigo):
        return JSONResponse({"error": "Código de producto inválido"}, status_code=400)
    campos = {}
    if body.precio_publico is not None:
        campos["precio_publico"] = body.precio_publico
    if body.precio_mayoreo is not None:
        campos["precio_mayoreo"] = body.precio_mayoreo
    if body.nombre is not None and body.nombre.strip():
        campos["nombre"] = body.nombre.strip()
    if body.observaciones is not None:
        campos["observaciones"] = body.observaciones.strip()
    if body.sin_stock is not None:
        campos["sin_stock"] = body.sin_stock
    if not campos:
        return JSONResponse({"error": "Nada para actualizar"}, status_code=400)
    set_override(codigo, campos)
    return {"ok": True, "codigo": codigo, "actualizado": campos}


class EtiquetaBody(BaseModel):
    jid: str
    etiqueta: str


@app.post("/api/etiqueta")
async def api_etiqueta(body: EtiquetaBody):
    """Asigna el estado de venta (Compró, No compró, etc.) a una conversación."""
    jid = (body.jid or "").strip()
    if not jid:
        return JSONResponse({"error": "Falta jid"}, status_code=400)
    set_etiqueta(jid, body.etiqueta)
    await _publicar_evento("etiqueta", {"jid": jid, "etiqueta": body.etiqueta})
    return {"ok": True, "jid": jid, "etiqueta": body.etiqueta}


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
