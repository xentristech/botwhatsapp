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
    borrar_foto,
    candidatos_seguimiento,
    crear_producto,
    es_modo_humano,
    existe_producto_codigo,
    get_cotizacion_por_token,
    get_foto,
    marcar_seguimiento,
    listar_citas,
    listar_conversaciones,
    listar_cotizaciones,
    listar_leads,
    listar_mensajes,
    listar_solicitudes_producto,
    marcar_cotizacion_pagada,
    marcar_solicitud,
    registrar_mensaje,
    set_etiqueta,
    set_foto,
    set_modo_humano,
    set_override,
)

load_dotenv()

META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "platim2024")

# ── Autenticación del dashboard (Basic Auth) ─────────────────────────────
# Protege /dashboard y /api/*. Los webhooks (/webhook, /webhook/mercadopago)
# quedan libres para Meta y Mercado Pago. Solo se exige si hay contraseña.
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "platim")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


def _basic_ok(auth_header: str) -> bool:
    import base64
    import secrets

    if not auth_header.startswith("Basic "):
        return False
    try:
        usuario, _, clave = base64.b64decode(auth_header[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001
        return False
    return (
        secrets.compare_digest(usuario, DASHBOARD_USER)
        and secrets.compare_digest(clave, DASHBOARD_PASSWORD)
    )


app = FastAPI(title="PLATIM Agent", version="1.0.0")


@app.middleware("http")
async def _auth_dashboard(request: Request, call_next):
    ruta = request.url.path
    protegido = ruta == "/dashboard" or ruta.startswith("/api/")
    if protegido and DASHBOARD_PASSWORD:
        if not _basic_ok(request.headers.get("Authorization", "")):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="PLATIM Dashboard"'},
            )
    return await call_next(request)

# Suscriptores SSE del dashboard: cada conexion (pestaña) tiene su PROPIA cola.
# Un evento se difunde (broadcast) a todas. Antes había una sola cola compartida
# y los eventos se repartían al azar entre conexiones/generadores huérfanos, por
# eso el dashboard no actualizaba en tiempo real y tocaba refrescar a mano.
_suscriptores: "set[asyncio.Queue[dict]]" = set()

# Evita procesar dos veces el mismo mensaje (Meta reintenta).
_mensajes_vistos: set[str] = set()

# Agrupador de mensajes (debounce): junta mensajes seguidos del mismo cliente y
# responde una sola vez. Segundos de espera configurables por entorno.
DEBOUNCE_SEGUNDOS = float(os.getenv("DEBOUNCE_SEGUNDOS", "6"))
_buffers: dict[str, list[str]] = {}
_tareas: dict[str, "asyncio.Task"] = {}

# Seguimiento automático (recuperar clientes que dejaron "en visto").
SEGUIMIENTO_ACTIVO = os.getenv("SEGUIMIENTO_ACTIVO", "true").lower() in ("1", "true", "si", "yes")
SEGUIMIENTO_HORAS = float(os.getenv("SEGUIMIENTO_HORAS", "3"))
SEGUIMIENTO_INTERVALO_MIN = float(os.getenv("SEGUIMIENTO_INTERVALO_MIN", "15"))
MENSAJE_SEGUIMIENTO = os.getenv(
    "MENSAJE_SEGUIMIENTO",
    "Hola, vimos que nos dejaste en visto🥹, quisiera saber si tienes alguna "
    "duda que no pudimos resolver, recuerda que estoy aquí para ayudarte y "
    "aclarar todas tus inquietudes, cuéntame ¿en qué puedo ayudarte?☺️",
)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DASHBOARD_HTML = os.path.join(BASE_DIR, "dashboard", "index.html")

# Carpeta de fotos de producto (en el volumen 'data', persiste entre despliegues).
FOTOS_DIR = os.path.join(BASE_DIR, "data", "fotos")
os.makedirs(FOTOS_DIR, exist_ok=True)
MAX_FOTO_BYTES = 5 * 1024 * 1024  # 5 MB (límite de WhatsApp para imágenes)


async def _publicar_evento(tipo: str, data: dict) -> None:
    evento = {"tipo": tipo, "data": data}
    for q in list(_suscriptores):
        with suppress(Exception):
            q.put_nowait(evento)


# ── Healthcheck ──────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "servicio": "PLATIM Agent"}


# ── Cotización por token (link público de la página "solicitud enviada") ──
# La página estática de platim.co lee ?c=<token> y llama a estos endpoints.
# Son PÚBLICOS (no pasan por el Basic Auth): el token aleatorio es la seguridad.

_CORS = {"Access-Control-Allow-Origin": "*"}


@app.get("/cot/{token}/info")
async def cot_info(token: str):
    """Devuelve datos mínimos para personalizar la página (nombre, código, total)."""
    cot = get_cotizacion_por_token(token)
    if not cot:
        return JSONResponse({"error": "no_encontrada"}, status_code=404, headers=_CORS)
    return JSONResponse(
        {
            "nombre": cot.get("nombre", ""),
            "codigo": cot.get("codigo", ""),
            "total": cot.get("total", 0),
            "estado_pago": cot.get("estado_pago", "pendiente"),
        },
        headers=_CORS,
    )


@app.get("/cot/{token}")
async def cot_pdf(token: str):
    """Sirve el PDF de la cotización de ese cliente (regenerado desde la DB)."""
    cot = get_cotizacion_por_token(token)
    if not cot:
        return JSONResponse({"error": "no_encontrada"}, status_code=404, headers=_CORS)
    from agent.pdf_service import generar_pdf_cotizacion

    pdf_bytes = generar_pdf_cotizacion(cot)
    nombre_archivo = f"Cotizacion_PLATIM_{cot.get('codigo', 'PLATIM')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{nombre_archivo}"',
            **_CORS,
        },
    )


async def _bucle_seguimiento() -> None:
    """Revisa periódicamente y envía un mensaje a los clientes que dejaron
    'en visto' (no respondieron dentro de la ventana de 24h)."""
    from agent.whatsapp import send_text

    while True:
        await asyncio.sleep(SEGUIMIENTO_INTERVALO_MIN * 60)
        try:
            for jid in candidatos_seguimiento(SEGUIMIENTO_HORAS):
                try:
                    await send_text(jid, MENSAJE_SEGUIMIENTO)
                    registrar_mensaje(jid, "out", MENSAJE_SEGUIMIENTO, "bot")
                    marcar_seguimiento(jid)
                    await _publicar_evento(
                        "mensaje_out", {"jid": jid, "texto": MENSAJE_SEGUIMIENTO}
                    )
                    print(f"Seguimiento enviado a {jid}")
                except Exception as e:  # noqa: BLE001
                    print(f"Error enviando seguimiento a {jid}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"Error en bucle de seguimiento: {e}")


@app.on_event("startup")
async def _iniciar_tareas():
    if SEGUIMIENTO_ACTIVO:
        asyncio.create_task(_bucle_seguimiento())
        print(
            f"Seguimiento automático activo: {SEGUIMIENTO_HORAS}h de silencio, "
            f"revisa cada {SEGUIMIENTO_INTERVALO_MIN} min."
        )


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
    elif tipo in ("image", "document"):
        # Imágenes y PDF llevan su propio flujo (visión/lectura) y responden ya.
        if jid:
            await _procesar_media(msg, jid, tipo)
        return
    elif tipo in ("video", "sticker", "location", "contacts"):
        # Formatos que el bot no procesa: avisar qué sí aceptamos.
        if jid:
            await _avisar_formato_no_soportado(jid, tipo)
        return
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


async def _ejecutar_bot(
    jid: str, texto: str, es_audio: bool = False, adjuntos: list | None = None
) -> None:
    """Corre el agente y envía la respuesta (texto + voz si aplica).
    No registra el 'in' (ya se registró al recibir cada mensaje).
    adjuntos: items multimodales (imagen/PDF) para visión/lectura."""
    from agent.whatsapp import send_text

    try:
        respuesta = await procesar_mensaje(
            jid, texto, registrar_in=False, adjuntos=adjuntos
        )
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


async def _avisar_formato_no_soportado(jid: str, tipo: str) -> None:
    """Avisa al cliente que ese formato no se procesa y lista los que sí."""
    from agent.whatsapp import send_text

    nombres = {
        "video": "videos 🎥",
        "sticker": "stickers",
        "location": "ubicaciones",
        "contacts": "contactos",
    }
    que_es = nombres.get(tipo, "ese formato")
    # Registrar en el dashboard qué mandó el cliente.
    registrar_mensaje(jid, "in", f"[{que_es} — no soportado]", "cliente")
    await _publicar_evento("mensaje_in", {"jid": jid, "texto": f"[{que_es}]"})

    # Si un humano tomó el control, el bot no responde.
    if es_modo_humano(jid):
        return

    with suppress(Exception):
        await send_text(
            jid,
            f"Por ahora no puedo procesar {que_es} 😅. Puedo ayudarte por "
            "*texto*, *notas de voz* 🎙️, *fotos* 📷 y archivos *PDF* 📄. "
            "¿Me lo envías en alguno de esos formatos?",
        )


async def _procesar_media(msg: dict, jid: str, tipo: str) -> None:
    """Descarga una imagen o PDF entrante y deja que el agente lo vea/lea.

    Solo procesa imágenes y PDF (lo que el modelo puede interpretar). Otros
    documentos (docx, xlsx, etc.) reciben un mensaje pidiendo foto o PDF.
    """
    from agent.whatsapp import download_media, get_media_url, send_text

    info = msg.get("image" if tipo == "image" else "document", {})
    media_id = info.get("id", "")
    caption = (info.get("caption") or "").strip()
    mime = info.get("mime_type", "") or ""
    filename = info.get("filename", "documento")
    if not media_id:
        return

    # Descargar los bytes del media.
    try:
        media_url = await get_media_url(media_id)
        data = await download_media(media_url)
    except Exception as e:  # noqa: BLE001
        print(f"Error descargando media: {e}")
        with suppress(Exception):
            await send_text(
                jid, "No pude abrir tu archivo 😅. ¿Puedes reenviármelo?"
            )
        return

    from agent.vision_service import MAX_BYTES, item_documento, item_imagen

    es_pdf = tipo == "document" and (
        mime == "application/pdf" or filename.lower().endswith(".pdf")
    )
    es_img = tipo == "image" or mime.startswith("image/")

    if not (es_pdf or es_img):
        with suppress(Exception):
            await send_text(
                jid,
                "Por ahora no puedo abrir ese archivo 😅. Puedo ayudarte por "
                "*texto*, *notas de voz* 🎙️, *fotos* 📷 y archivos *PDF* 📄. "
                "¿Me lo envías en alguno de esos formatos?",
            )
        return

    if len(data) > MAX_BYTES:
        with suppress(Exception):
            await send_text(
                jid,
                "Ese archivo es muy grande 😅. ¿Puedes enviarme uno más liviano "
                "o una foto?",
            )
        return

    if es_pdf:
        adjunto = item_documento(data, filename, "application/pdf")
        texto_registro = f"📄 {filename}" + (f" — {caption}" if caption else "")
        texto_modelo = caption or (
            "El cliente envió un PDF. Léelo, resume lo que entiendas "
            "(productos y cantidades si aplica) y ayúdalo con su requerimiento."
        )
    else:
        adjunto = item_imagen(data, mime or "image/jpeg")
        texto_registro = "📷 Imagen" + (f" — {caption}" if caption else "")
        texto_modelo = caption or (
            "El cliente envió una imagen. Analízala, describe lo que ves "
            "y ayúdalo según lo que necesite."
        )

    # Registrar el entrante (dashboard/historial) y avisar en vivo.
    registrar_mensaje(jid, "in", texto_registro, "cliente")
    await _publicar_evento("mensaje_in", {"jid": jid, "texto": texto_registro})

    # Si un humano tomó el control, el bot NO responde.
    if es_modo_humano(jid):
        return

    await _ejecutar_bot(jid, texto_modelo, adjuntos=[adjunto])


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


# ── Webhook de Mercado Pago (confirmacion de pago) ───────────────────────

@app.post("/webhook/mercadopago")
@app.get("/webhook/mercadopago")
async def webhook_mercadopago(request: Request):
    params = request.query_params
    payment_id = params.get("data.id") or params.get("id")
    tipo = params.get("type") or params.get("topic")
    if not payment_id:
        with suppress(Exception):
            body = await request.json()
            tipo = body.get("type") or body.get("topic") or tipo
            payment_id = (body.get("data") or {}).get("id") or body.get("id")

    if tipo and "payment" in str(tipo) and payment_id:
        try:
            from agent.pagos_service import consultar_pago

            pago = await consultar_pago(str(payment_id))
            if pago.get("status") == "approved":
                codigo = pago.get("external_reference", "")
                cot = marcar_cotizacion_pagada(codigo) if codigo else None
                if cot:
                    jid = cot.get("jid", "")
                    if jid:
                        set_etiqueta(jid, "Compró")
                        from agent.whatsapp import send_text

                        with suppress(Exception):
                            await send_text(
                                jid,
                                f"✅ ¡Pago recibido! Tu cotización {codigo} quedó "
                                "pagada. ¡Gracias por tu compra en PLATIM! 🙌",
                            )
                        await _publicar_evento(
                            "pago", {"jid": jid, "codigo": codigo}
                        )
        except Exception as e:  # noqa: BLE001
            print(f"Error procesando webhook Mercado Pago: {e}")

    return JSONResponse({"status": "ok"})


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


@app.get("/api/solicitudes")
async def api_solicitudes(limite: int = 100):
    """Productos que clientes pidieron y no estaban en el catálogo."""
    return listar_solicitudes_producto(limite)


class SolicitudBody(BaseModel):
    estado: str


@app.post("/api/solicitud/{id_}/estado")
async def api_solicitud_estado(id_: int, body: SolicitudBody):
    estado = (body.estado or "").strip()
    if estado not in ("pendiente", "agregado", "descartado"):
        return JSONResponse({"error": "Estado inválido"}, status_code=400)
    marcar_solicitud(id_, estado)
    return {"ok": True}


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
            "precio_volumen": int(p.get("precio_volumen") or 0),
            "observaciones": p.get("observaciones", ""),
            "sin_stock": bool(p.get("sin_stock")),
            "tiene_foto": bool(p.get("tiene_foto")),
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
    precio_volumen: int = 0
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
        "precio_volumen": body.precio_volumen,
        "descripcion": (body.descripcion or "").strip(),
        "uso": (body.uso or "").strip(),
    })
    return {"ok": True, "codigo": nuevo}


class ProductoBody(BaseModel):
    codigo: str
    precio_publico: int | None = None
    precio_mayoreo: int | None = None
    precio_volumen: int | None = None
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
    if body.precio_volumen is not None:
        campos["precio_volumen"] = body.precio_volumen
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


@app.get("/fotos/{codigo}")
async def servir_foto(codigo: str):
    """Sirve la foto de un producto (PÚBLICO: WhatsApp la descarga por este link)."""
    codigo = (codigo or "").strip().upper()
    archivo = get_foto(codigo)
    if not archivo:
        return JSONResponse({"error": "sin_foto"}, status_code=404)
    ruta = os.path.join(FOTOS_DIR, archivo)
    if not os.path.isfile(ruta):
        return JSONResponse({"error": "sin_foto"}, status_code=404)
    return FileResponse(ruta)


@app.post("/api/producto/{codigo}/foto")
async def api_subir_foto(codigo: str, archivo: UploadFile = File(...)):
    """Carga/reemplaza la foto de un producto desde el dashboard.
    SIEMPRE convierte a JPEG: WhatsApp solo entrega imágenes JPG/PNG (el WEBP lo
    descarta en silencio), así que normalizamos todo a JPG."""
    codigo = (codigo or "").strip().upper()
    if not codigo or not catalogo.obtener(codigo):
        return JSONResponse({"error": "Código de producto inválido"}, status_code=400)
    contenido = await archivo.read()
    if len(contenido) > MAX_FOTO_BYTES:
        return JSONResponse(
            {"error": "La imagen es muy grande (máx 5 MB)."}, status_code=400
        )
    jpeg = _a_jpeg(contenido)
    if jpeg is None:
        return JSONResponse(
            {"error": "No pude procesar la imagen. Usa una foto JPG o PNG."},
            status_code=400,
        )
    # Borrar cualquier foto anterior (por si tenía otra extensión).
    anterior = get_foto(codigo)
    if anterior:
        with suppress(Exception):
            os.remove(os.path.join(FOTOS_DIR, anterior))
    nombre = f"{codigo}.jpg"
    with open(os.path.join(FOTOS_DIR, nombre), "wb") as f:
        f.write(jpeg)
    set_foto(codigo, nombre)
    return {"ok": True, "codigo": codigo, "url": f"/fotos/{codigo}"}


def _a_jpeg(contenido: bytes) -> bytes | None:
    """Convierte cualquier imagen (JPG/PNG/WEBP/…) a JPEG RGB compatible con
    WhatsApp. Aplana transparencias sobre blanco y limita el tamaño. None si falla."""
    try:
        from io import BytesIO

        from PIL import Image

        img = Image.open(BytesIO(contenido))
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            fondo = Image.new("RGB", img.size, (255, 255, 255))
            img = img.convert("RGBA")
            fondo.paste(img, mask=img.split()[-1])
            img = fondo
        else:
            img = img.convert("RGB")
        img.thumbnail((1600, 1600))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        print(f"Error convirtiendo imagen a JPEG: {e}")
        return None


@app.delete("/api/producto/{codigo}/foto")
async def api_borrar_foto(codigo: str):
    """Quita la foto de un producto."""
    codigo = (codigo or "").strip().upper()
    archivo = borrar_foto(codigo)
    if archivo:
        with suppress(Exception):
            os.remove(os.path.join(FOTOS_DIR, archivo))
    return {"ok": True, "codigo": codigo}


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
    cola: "asyncio.Queue[dict]" = asyncio.Queue()
    _suscriptores.add(cola)

    async def generador():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evento = await asyncio.wait_for(cola.get(), timeout=15)
                    yield {"event": evento["tipo"], "data": json.dumps(evento["data"], ensure_ascii=False)}
                except asyncio.TimeoutError:
                    # Keepalive para mantener viva la conexion SSE.
                    yield {"event": "ping", "data": "{}"}
        finally:
            _suscriptores.discard(cola)

    return EventSourceResponse(
        generador(),
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── Dashboard UI ─────────────────────────────────────────────────────────

@app.get("/dashboard")
async def dashboard():
    if os.path.exists(DASHBOARD_HTML):
        return FileResponse(DASHBOARD_HTML)
    return JSONResponse({"error": "dashboard/index.html no encontrado"}, status_code=404)
