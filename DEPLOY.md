# Despliegue — PLATIM Agent

El bot es un servicio web persistente (FastAPI/uvicorn) que expone `/webhook`.
Necesita: proceso siempre activo, HTTPS público, y un volumen persistente para
`data/` (SQLite: cotizaciones, citas, conversaciones).

## Variables de entorno (configurar en la plataforma, NO subir `.env`)
```
OPENAI_API_KEY
META_PHONE_NUMBER_ID
META_ACCESS_TOKEN          # usar token PERMANENTE (Usuario del Sistema) en prod
META_VERIFY_TOKEN=platim2024
META_WABA_ID
META_API_VERSION=v21.0
GMAIL_USER
GMAIL_APP_PASSWORD
PLATIM_EMAIL
OPENAI_MODEL=gpt-4o-mini
STT_MODEL=whisper-1
TTS_MODEL=gpt-4o-mini-tts
TTS_VOICE=alloy
```

## Opción A — VPS (Ubuntu) con Docker  [recomendada para producción]
```bash
# en el VPS
git clone https://github.com/xentristech/botwhatsapp.git && cd botwhatsapp
# crear .env con las variables de arriba
docker build -t platim-agent .
docker run -d --name platim --restart unless-stopped \
  --env-file .env -p 8088:8088 -v $PWD/data:/app/data platim-agent
# HTTPS: Caddy o Nginx + Let's Encrypt apuntando a un subdominio -> :8088
```
Con Caddy (auto-HTTPS), un `Caddyfile`:
```
bot.tudominio.com {
    reverse_proxy localhost:8088
}
```
El webhook queda fijo: `https://bot.tudominio.com/webhook`.

## Opción B — Railway / Render (desde GitHub)
- Conecta el repo `xentristech/botwhatsapp`.
- Define las variables de entorno (arriba).
- Usa el `Dockerfile` o el `Procfile`.
- **Monta un volumen/disco persistente en `/app/data`** (si no, se pierden los
  datos en cada despliegue). Alternativa: migrar a Postgres.
- La plataforma da una URL HTTPS fija -> úsala como Callback URL en Meta.

## Después de desplegar (Meta)
1. Webhook Callback URL = `https://<tu-dominio>/webhook`, verify token `platim2024`.
2. Suscribir el campo `messages` y el WABA a la app.
3. Token permanente + app en modo **Live** para atender a cualquier cliente.
