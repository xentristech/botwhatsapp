# PLATIM Agent

Bot de **WhatsApp con IA** para atención al cliente y ventas de dotaciones industriales y equipos de protección personal (EPP). Asesora, arma cotizaciones, cobra, agenda citas y hace seguimiento — todo por WhatsApp, con un panel de control en tiempo real.

Construido con la **API oficial de Meta WhatsApp Cloud** y el **OpenAI Agents SDK**.

> [!NOTE]
> Proyecto real en producción para **PLATIM** (Palmira, Valle del Cauca, Colombia). Atiende por el número de negocio verificado y corre 24/7 en un VPS.

## Funciones

- **Asesoría inteligente** sobre un catálogo de 65+ productos, con búsqueda tolerante a acentos, plurales y errores.
- **Cotizaciones en PDF** enviadas por WhatsApp y por email (con copia interna al equipo).
- **Catálogo completo en PDF** a demanda, agrupado por categoría y con precios público/mayoreo.
- **Audio bidireccional**: entiende notas de voz (Whisper) y responde también con voz (TTS).
- **Agenda de citas** con una asesora (días/horas configurables): ver disponibilidad, agendar, cancelar y reprogramar, con aviso por correo.
- **Pagos con Mercado Pago**: el bot envía un botón "Pagar ahora" y marca la cotización como pagada al confirmarse el pago.
- **Seguimiento automático**: si el cliente deja "en visto", el bot le reescribe (dentro de la ventana de 24 h de WhatsApp).
- **Dashboard en tiempo real** (SSE) con login:
  - Historial de conversaciones y toma de control **humano** (handoff): pausar el bot y escribir tú.
  - **Etiquetas de venta** (Compró / No compró / Interesado…).
  - **Editor de productos**: cambiar precios, crear productos, marcar sin stock, e **importar/exportar en Excel**.
- **Validación de correo** (formato + registro MX del dominio) y **formato WhatsApp** correcto (convierte el Markdown del modelo).

## Stack

| Área | Tecnología |
|------|------------|
| Backend | Python 3.11, FastAPI, uvicorn |
| Agente IA | openai-agents SDK (`SQLiteSession`), modelo `gpt-4o-mini` |
| Mensajería | Meta WhatsApp Cloud API |
| Audio | OpenAI Whisper (STT) + TTS (Ogg/Opus) |
| Pagos | Mercado Pago (Checkout Pro) |
| Documentos | reportlab (PDF), openpyxl (Excel) |
| Email | aiosmtplib (Gmail o SMTP del dominio) |
| Datos | SQLite |
| Despliegue | Docker, Caddy (HTTPS), systemd, GitHub Actions |

## Estructura

```
agent/
  main.py           # FastAPI: webhooks (Meta y Mercado Pago), API del dashboard (SSE),
                    #          login, debounce de mensajes, bucle de seguimiento
  agente.py         # Agente + tools + system prompt + procesar_mensaje()
  catalogo.py       # Catálogo base + búsqueda por tokens (scoring, sin acentos)
  db.py             # SQLite: leads, cotizaciones, mensajes, citas, control, productos
  whatsapp.py       # Cliente Cloud API: texto, documentos, audio, botones de pago
  email_service.py  # Envío por SMTP configurable + copias internas
  pdf_service.py    # PDF de cotización y de catálogo
  audio_service.py  # Transcripción (Whisper) y voz (TTS)
  pagos_service.py  # Mercado Pago
  excel_service.py  # Import/export del catálogo en .xlsx
dashboard/index.html  # Panel en tiempo real
Dockerfile · deploy_oracle.sh · redeploy.sh · .github/workflows/deploy.yml
```

## Cómo funciona

1. El cliente escribe (o manda una nota de voz) al número de WhatsApp del negocio.
2. Meta reenvía el mensaje al webhook `POST /webhook`.
3. Los mensajes seguidos del mismo cliente se **agrupan** y se procesan como uno solo.
4. El agente responde usando sus herramientas (buscar, cotizar, agendar, cobrar…). La memoria de cada conversación se guarda por número.
5. Si un humano toma el control desde el dashboard, el bot se pausa en esa conversación.

## Empezar (local)

Requisitos: Python 3.11+, credenciales de Meta WhatsApp Cloud API y una clave de OpenAI.

```bash
# 1. Entorno y dependencias
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # macOS/Linux
pip install -r requirements.txt

# 2. Configuración
cp .env.example .env             # completa con tus credenciales

# 3. Ejecutar
uvicorn agent.main:app --port 8088 --reload
```

Para exponer el webhook durante el desarrollo puedes usar un túnel (ngrok, Cloudflare Tunnel) apuntando al puerto `8088`, y registrar `https://<tu-url>/webhook` como Callback URL en Meta (verify token = `META_VERIFY_TOKEN`).

El dashboard queda en `http://localhost:8088/dashboard`.

## Variables de entorno

Ver [`.env.example`](.env.example) para la lista completa. Las principales:

| Variable | Descripción |
|----------|-------------|
| `OPENAI_API_KEY` | Clave de OpenAI (agente, Whisper, TTS) |
| `META_PHONE_NUMBER_ID`, `META_WABA_ID`, `META_ACCESS_TOKEN` | WhatsApp Cloud API |
| `META_VERIFY_TOKEN` | Token de verificación del webhook |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | SMTP para enviar correos (o usa `SMTP_*` para tu dominio) |
| `PLATIM_EMAIL`, `PLATIM_EMAIL_COPIA` | Correos internos que reciben copia de cada cotización |
| `MP_ACCESS_TOKEN`, `MP_SANDBOX` | Mercado Pago |
| `PUBLIC_BASE_URL` | URL pública del servidor (para el webhook de pagos) |
| `DASHBOARD_USER`, `DASHBOARD_PASSWORD` | Login del dashboard |
| `SEGUIMIENTO_HORAS` | Horas de silencio antes del mensaje de seguimiento |

> [!WARNING]
> El archivo `.env` nunca se sube al repositorio (está en `.gitignore`). Los secretos van solo en el `.env` del servidor.

## Despliegue (VPS con Docker + HTTPS)

En un servidor Ubuntu (por ejemplo Oracle Cloud "Always Free"):

```bash
git clone https://github.com/xentristech/botwhatsapp.git && cd botwhatsapp
# crea el archivo .env con tus credenciales
bash deploy_oracle.sh bot.tudominio.com
```

El script instala Docker y Caddy (HTTPS automático con Let's Encrypt), abre el firewall del sistema, construye la imagen y registra el bot como servicio **systemd** (`platim`) que arranca solo al reiniciar. Ver [`DEPLOY.md`](DEPLOY.md) para el detalle.

> [!IMPORTANT]
> En proveedores como Oracle Cloud hay que abrir los puertos **80 y 443** también en la **Security List / firewall de la nube**, no solo en el sistema operativo.

### Auto-deploy

`.github/workflows/deploy.yml` actualiza el servidor en cada push a `main` (SSH → `git pull` → build → `systemctl restart`). Requiere estos secrets en el repo: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`.

## Operación

```bash
sudo systemctl status platim     # estado del servicio
sudo docker logs -f platim       # logs en vivo
bash redeploy.sh                 # actualizar a mano (pull + build + restart)
```
