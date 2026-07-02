#!/usr/bin/env bash
# Despliegue de PLATIM Agent en un VPS Ubuntu (Oracle Cloud, etc.)
#
# Uso:
#   1) git clone https://github.com/xentristech/botwhatsapp.git && cd botwhatsapp
#   2) Crea el archivo .env en esta carpeta (con tus credenciales).
#   3) bash deploy_oracle.sh [dominio]      (dominio por defecto: bot.platim.co)
#
# Deja el bot como servicio systemd (arranca solo al reiniciar) detrás de Caddy
# (HTTPS automático).
set -e

DOMINIO="${1:-bot.platim.co}"
APP_DIR="$PWD"

if [ ! -f .env ]; then
  echo "ERROR: no existe el archivo .env en $(pwd). Créalo primero."
  exit 1
fi

echo "==> [1/6] Instalando Docker..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi

echo "==> [2/6] Instalando Caddy (HTTPS automático)..."
if ! command -v caddy >/dev/null 2>&1; then
  # Nota: NO instalamos debian-keyring (29 MB, innecesario y muy lento en algunos mirrors).
  sudo apt-get install -y apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y caddy
fi

echo "==> [3/6] Abriendo puertos 80/443 en el firewall (Oracle)..."
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT || true
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT || true
{ sudo netfilter-persistent save 2>/dev/null \
  || { sudo mkdir -p /etc/iptables && sudo bash -c 'iptables-save > /etc/iptables/rules.v4'; }; } || true

echo "==> [4/6] Construyendo la imagen del bot..."
sudo docker build -t platim-agent .

echo "==> [5/6] Instalando servicio systemd (arranque automático)..."
sudo bash -c "cat > /etc/systemd/system/platim.service" <<UNIT
[Unit]
Description=PLATIM Agent (bot WhatsApp)
After=docker.service network-online.target
Requires=docker.service

[Service]
WorkingDirectory=${APP_DIR}
ExecStartPre=-/usr/bin/docker rm -f platim
ExecStart=/usr/bin/docker run --name platim --env-file ${APP_DIR}/.env -p 8088:8088 -v ${APP_DIR}/data:/app/data platim-agent
ExecStop=/usr/bin/docker stop platim
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now platim

echo "==> [6/6] Configurando Caddy para ${DOMINIO}..."
sudo bash -c "cat > /etc/caddy/Caddyfile" <<CADDY
${DOMINIO} {
    reverse_proxy localhost:8088
}
CADDY
sudo systemctl restart caddy

echo ""
echo "======================================================"
echo " Listo. En 1-2 min (cuando Caddy emita el certificado)"
echo " el bot responderá en: https://${DOMINIO}"
echo " Ver estado:   sudo systemctl status platim"
echo " Ver logs:     sudo docker logs -f platim"
echo "======================================================"
