---
name: platim-whatsapp-agent
description: Playbook para construir y desplegar un agente de WhatsApp con IA (tipo PLATIM) para ventas/cotizaciones — Meta Cloud API + OpenAI Agents SDK + FastAPI + SQLite, con cotizaciones PDF, audio, citas, pagos Mercado Pago, dashboard con control humano y despliegue en VPS con HTTPS y auto-deploy. Úsalo cuando pidan crear, extender o desplegar un bot de WhatsApp para atención al cliente / e-commerce conversacional, o para retomar el proyecto botwhatsapp.
---

# Agente de WhatsApp con IA (PLATIM) — Playbook completo

Guía para construir/extender/desplegar un bot de WhatsApp que asesora, cotiza, cobra y agenda. Referencia real: `xentristech/botwhatsapp` (PLATIM, dotaciones/EPP, Colombia).

## Stack
- **Python 3.11** · FastAPI + uvicorn (webhook + API dashboard + SSE)
- **openai-agents SDK** (Agent + tools + `SQLiteSession` para memoria) · modelo `gpt-4o-mini`
- **Meta WhatsApp Cloud API** (webhook + envío de texto/documentos/audio/botones CTA)
- **reportlab** (PDF) · **aiosmtplib** (email) · **openpyxl** (Excel) · **SQLite** (datos)
- Audio: Whisper (STT) + TTS (Ogg/Opus). Pagos: **Mercado Pago** (Checkout Pro).

## Estructura del proyecto
```
agent/
  main.py           # FastAPI: webhook Meta, webhook Mercado Pago, API dashboard (SSE),
                    #          Basic Auth, debounce de mensajes, bucle de seguimiento
  agente.py         # Agent + TOOLS + system prompt + procesar_mensaje()
  catalogo.py       # Catálogo base + búsqueda por tokens (sin acentos, scoring, plural)
  db.py             # SQLite: leads, cotizaciones, mensajes, citas, estado, control_conversacion,
                    #         producto_override, producto_nuevo. Migraciones "suaves" (ALTER guardado).
  whatsapp.py       # Cliente Cloud API: send_text, send_document, send_cta_button, upload/download media
  email_service.py  # SMTP configurable (Gmail o dominio propio) + copias internas
  pdf_service.py    # PDF de cotización y de catálogo completo
  audio_service.py  # transcribir_audio (Whisper) + texto_a_audio (TTS)
  pagos_service.py  # Mercado Pago: crear_link_pago + consultar_pago
  excel_service.py  # exportar/importar catálogo en .xlsx
dashboard/index.html  # SSE en vivo, chat/handoff, etiquetas, editor de precios/stock, modal productos
Dockerfile · Procfile · deploy_oracle.sh · redeploy.sh · .github/workflows/deploy.yml
```

## Las tools del agente (en `agente.py`, decoradas `@function_tool`)
`buscar_productos`, `comparar_productos`, `enviar_catalogo_pdf`, `agregar_item_cotizacion`,
`ver_cotizacion_actual`, `registrar_datos_cliente`, `generar_y_enviar_cotizacion`,
`ver_disponibilidad_asesora`, `agendar_cita_asesora`, `mis_citas_asesora`,
`cancelar_cita_asesora`, `generar_link_pago`, `limpiar_cotizacion`.
El estado de la cotización en curso se persiste por `jid` (write-through a SQLite), y la memoria
de conversación con `SQLiteSession(jid, data/sessions.db)`.

## Flujo del webhook (`main.py`)
1. Meta hace `POST /webhook`. Se deduplica por `msg.id`.
2. Se extrae texto (o se transcribe si es audio). Se registra el mensaje entrante y se emite evento SSE.
3. Si la conversación está en **modo humano** → el bot NO responde.
4. **Debounce:** los mensajes de texto seguidos del mismo cliente se agrupan y se responde UNA vez.
5. `procesar_mensaje` corre el agente; la respuesta se envía por WhatsApp (texto; + voz si entró por audio).

## Funciones clave y dónde tocarlas
- **Cotización → PDF + email + WhatsApp:** `generar_y_enviar_cotizacion` (tool) usa `pdf_service` + `email_service` + `whatsapp`.
- **Catálogo PDF:** `enviar_catalogo_pdf` + `pdf_service.generar_pdf_catalogo`.
- **Precios/stock editables + Excel:** `producto_override`/`producto_nuevo` en `db.py`; `catalogo.buscar` aplica los ajustes y excluye `sin_stock`; endpoints `/api/producto[...]`.
- **Citas asesora:** franjas 30 min L-V 2-4 PM (hora Colombia = UTC-5). Evita choque y reconoce la cita propia del cliente.
- **Control humano/handoff:** `control_conversacion.humano`; endpoints `/api/enviar` y `/api/modo`; el dashboard pausa el bot y escribe como agente.
- **Pagos:** `generar_link_pago` (tool) → `pagos_service.crear_link_pago` → botón CTA. Confirmación en `POST /webhook/mercadopago` → marca "pagado" + etiqueta "Compró".
- **Seguimiento "en visto":** `_bucle_seguimiento` en `main.py` + `db.candidatos_seguimiento` (respeta ventana 24h, no repite, no molesta en modo humano).
- **Seguridad:** middleware Basic Auth protege `/dashboard` y `/api/*`; los webhooks quedan libres.

## Formato de mensajes
El LLM tiende a Markdown; `formatear_whatsapp()` convierte a sintaxis WhatsApp (negrita con un solo `*`, sin `##`, enlaces legibles). Aplícalo a TODA respuesta antes de enviar.

## Despliegue (VPS Ubuntu, ej. Oracle Cloud Always Free)
1. `git clone` en el VPS, crear `.env` (ver `.env.example`), correr `bash deploy_oracle.sh <dominio>`.
   Instala Docker + Caddy (HTTPS automático) + abre firewall del SO + crea servicio **systemd `platim`**.
2. **Abrir 80/443 también en la Security List/cloud firewall** (el firewall del SO no basta).
3. **Auto-deploy:** GitHub Actions (`deploy.yml`) hace SSH + `git pull` + build + `systemctl restart platim` en cada push. Secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`.
4. Webhook Meta y Mercado Pago apuntan a `https://<dominio>/webhook` y `/webhook/mercadopago`.

## Gotchas (aprendidos a los golpes)
- SDK: usar `SQLiteSession`, NO `InMemorySession` (no existe).
- Oracle: **dos** firewalls (SO + Security List); Ubuntu trae **nginx en :80** (parar/deshabilitar) que choca con Caddy; al instalar Caddy **no** instalar `debian-keyring` (se cuelga).
- DNS parking de Hostinger secuestra subdominios (incluye IPv6) → romper con **sslip.io** (`157-137-224-141.sslip.io`) mientras se arregla el dominio real.
- **Token de Meta temporal (~24h):** para prod generar token permanente (Usuario del Sistema) y suscribir el WABA a la app (`POST /{WABA_ID}/subscribed_apps`).
- **OpenAI:** sin billing → 50 req/día / `insufficient_quota`.
- **WhatsApp:** solo texto libre dentro de 24h del último mensaje del cliente; después, plantillas.
- **Mercado Pago sandbox:** credenciales de prueba nuevas empiezan con `APP_USR-`; para pagar, loguearse como el **usuario COMPRADOR** de prueba.
- El `.env` NUNCA va a git (`.gitignore`). Los secretos van en el `.env` del server.

## Cómo extender
Añade una tool en `agente.py` (`@function_tool`, regístrala en la lista `tools=[...]` del Agent y describe su uso en el system prompt). Si necesita datos, agrega tabla/función en `db.py` (con migración ALTER guardada). Si expone algo al dashboard, agrega endpoint en `main.py` y UI en `dashboard/index.html`. Haz `push` → el VPS se actualiza solo.
