# PLATIM Agent

Agente de WhatsApp para **PLATIM** (dotaciones industriales y EPP, Palmira, Valle del Cauca).
Atiende clientes, asesora sobre productos, arma cotizaciones, genera un PDF profesional y lo
envía por **WhatsApp** (documento) y **email** (PDF adjunto).

Usa **Meta WhatsApp Cloud API** (oficial) + **OpenAI Agents SDK**.

## Stack

- Python 3.11+ · FastAPI + uvicorn
- openai >= 2.0.0 · openai-agents >= 0.17.7
- reportlab (PDF) · aiosmtplib (email) · httpx · SQLite · SSE (dashboard)

## Estructura

```
botwhatsapp/
├── agent/
│   ├── __init__.py
│   ├── main.py          # FastAPI: webhook Meta + API dashboard + SSE
│   ├── agente.py        # OpenAI Agents SDK: 7 tools + flujo
│   ├── db.py            # SQLite: leads, cotizaciones, mensajes
│   ├── catalogo.py      # 65 productos PLATIM (fuente de verdad)
│   ├── whatsapp.py      # Cliente Meta Cloud API
│   ├── email_service.py # Gmail SMTP con PDF adjunto
│   └── pdf_service.py   # Generación de PDF (reportlab)
├── dashboard/index.html # Dashboard SSE en tiempo real
├── data/                # SQLite (se crea solo)
├── .env                 # Variables de entorno (copiar de .env.example)
└── requirements.txt
```

## Setup

```bash
# 1. Copiar variables de entorno
cp .env.example .env        # editar con credenciales reales

# 2. Entorno virtual
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# 3. Dependencias
pip install -r requirements.txt

# 4. Backend
uvicorn agent.main:app --port 8000 --reload

# 5. En otra terminal: exponer con ngrok
ngrok http 8000

# 6. Verificar webhook
curl "http://localhost:8000/webhook?hub.mode=subscribe&hub.verify_token=platim2024&hub.challenge=TEST"
# -> debe responder: TEST
```

### Configurar webhook en Meta

- **URL:** `https://XXXX.ngrok-free.app/webhook`
- **Verify token:** el valor de `META_VERIFY_TOKEN` (default `platim2024`)
- Suscribir el campo **messages**.

## Dashboard

Abrir `http://localhost:8000/dashboard` — muestra conversaciones en vivo (SSE),
leads y cotizaciones.

## Flujo de conversación

1. Cliente escribe → agente saluda mencionando PLATIM.
2. Busca productos (`buscar_productos`) y compara (`comparar_productos`).
3. Define minorista/mayorista y arma la cotización (`agregar_item_cotizacion`).
4. Registra datos del cliente (`registrar_datos_cliente`).
5. Al confirmar → `generar_y_enviar_cotizacion`: PDF por WhatsApp + email + copia interna.

## Notas

- `openai-agents` requiere `openai >= 2.0.0` (no usar v1.x).
- La memoria de conversación usa `SQLiteSession` (de `agents`), persistida en
  `data/sessions.db`. El estado de la cotización en curso se guarda en
  `data/platim.db` (tabla `estado_cotizacion`), así que **sobrevive reinicios**.
- El token de Meta de prueba es temporal (24 h). Para producción usar un token
  permanente de un usuario del sistema (Meta Business Suite).
- El `.env` **nunca** va al repositorio (ya está en `.gitignore`).
