#!/usr/bin/env bash
# Actualiza el bot en el VPS: trae los últimos cambios, reconstruye y reinicia.
# Lo usa GitHub Actions en cada push, y también puedes correrlo a mano.
set -e
cd "$(dirname "$0")"
echo "==> git pull..."
git pull --ff-only
echo "==> docker build..."
sudo docker build -t platim-agent .
echo "==> reiniciando servicio..."
sudo systemctl restart platim
echo "==> Redeploy OK. Estado:"
sudo systemctl --no-pager status platim | head -5
