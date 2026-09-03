#!/usr/bin/env bash
# Instala o relay do Meu Controle Remoto em um VPS Linux e cria um servico
# systemd para rodar sempre (sobe sozinho no boot e reinicia se cair).
#
# Uso:
#   sudo bash install_relay.sh [porta]
#   (porta padrao: 8765)

set -euo pipefail

PORT="${1:-8765}"
DIR="/opt/meucontrole"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Instalando Python e dependencias..."
(apt-get update -y && apt-get install -y python3 python3-pip python3-venv) || true

echo "==> Criando pasta $DIR..."
mkdir -p "$DIR"

if [ ! -f "$SCRIPT_DIR/relay.py" ]; then
  echo "!!! Coloque o relay.py na mesma pasta deste script e rode de novo."
  exit 1
fi
cp "$SCRIPT_DIR/relay.py" "$DIR/relay.py"

echo "==> Criando venv e instalando aiohttp..."
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --upgrade pip -q
"$DIR/venv/bin/pip" install aiohttp -q

echo "==> Criando servico systemd..."
cat > /etc/systemd/system/meucontrole-relay.service <<EOF
[Unit]
Description=Meu Controle Remoto - Relay
After=network.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python $DIR/relay.py --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable meucontrole-relay
systemctl restart meucontrole-relay
systemctl --no-pager status meucontrole-relay || true

echo ""
echo "============================================"
echo "Relay instalado!"
echo "Anote o IP PUBLICO do VPS e configure os apps:"
echo "  relay: ws://IP_PUBLICO_DO_VPS:$PORT"
echo ""
echo "Se houver firewall (ufw etc.), libere a porta:"
echo "  ufw allow $PORT/tcp"
echo "============================================"
