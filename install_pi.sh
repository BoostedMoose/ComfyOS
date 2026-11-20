#!/usr/bin/env bash
# Set up ComfyOS on the Raspberry Pi:
#   * host name "comfyos"  -> in the home network at http://comfyos/ (FRITZ!Box),
#                           http://comfyos.fritz.box/ und http://comfyos.local/
#   * autostart as a system service on port 80 (without root rights for the app)
#   * automatic restart after updates
# Usage in the ComfyOS folder:  sudo ./install_pi.sh
# Optional: COMFYOS_HOSTNAME=myname COMFYOS_PORT=8080 sudo -E ./install_pi.sh
# Undo:  sudo ./install_pi.sh --uninstall   (remove the service, restore the old host name)
# Can also be called from within ComfyOS: System -> Serversetup
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${COMFYOS_USER:-${SUDO_USER:-$(id -un)}}"
PREV_FILE=/etc/comfyos-prev-hostname
NAME="${COMFYOS_HOSTNAME:-comfyos}"
PORT="${COMFYOS_PORT:-80}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo starten:  sudo ./install_pi.sh"
  exit 1
fi
if [ "$RUN_USER" = "root" ]; then
  echo "Bitte als normaler Benutzer mit sudo starten (nicht direkt als root)."
  exit 1
fi

set_name() {
  hostnamectl set-hostname "$1"
  if grep -q "^127.0.1.1" /etc/hosts; then
    sed -i "s/^127.0.1.1.*/127.0.1.1\t$1/" /etc/hosts
  else
    printf "127.0.1.1\t%s\n" "$1" >> /etc/hosts
  fi
  systemctl restart avahi-daemon 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
  echo "==> Dienst comfyos.service entfernen"
  systemctl disable comfyos.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/comfyos.service
  systemctl daemon-reload
  if [ -f "$PREV_FILE" ]; then
    OLD="$(cat "$PREV_FILE")"
    echo "==> Hostname zurück auf: $OLD"
    set_name "$OLD"
    rm -f "$PREV_FILE"
  fi
  echo "FERTIG: Dienst entfernt. ComfyOS startet nicht mehr automatisch."
  systemctl stop comfyos.service >/dev/null 2>&1 || true
  exit 0
fi

echo "==> Pakete (python3-venv, avahi-daemon für comfyos.local)"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip avahi-daemon >/dev/null

echo "==> Hostname: $NAME"
if [ "$(hostname)" != "$NAME" ]; then
  [ -f "$PREV_FILE" ] || hostname > "$PREV_FILE"
  set_name "$NAME"
  NEED_REBOOT=1
fi

echo "==> Python-Umgebung"
sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/python" -m pip install -q --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/python" -m pip install -q -r "$APP_DIR/backend/requirements.txt"

echo "==> Dienst comfyos.service (Port $PORT)"
HOME_DIR="$(getent passwd "$RUN_USER" | cut -d: -f6)"
CONF_DIR="$HOME_DIR/.config/ComfyOS"
PARENT_DIR="$(dirname "$APP_DIR")"   # the updater puts packages and backups here
sudo -u "$RUN_USER" mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"
cat > /etc/systemd/system/comfyos.service <<EOF
[Unit]
Description=ComfyOS Smart-Home-Zentrale
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
# install dependencies after an update (ignore errors without a network)
ExecStartPre=-$APP_DIR/.venv/bin/python -m pip install -q --no-cache-dir -r $APP_DIR/backend/requirements.txt
# HTTP on $PORT; with HTTPS (System → Erreichbarkeit) additionally port 443, HTTP then redirects
Environment=COMFYOS_HOST=0.0.0.0 COMFYOS_PORT=$PORT
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/backend/run.py
# port 80 without root: only this one capability
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
# file system read-only – ComfyOS may only write to its folder, the update folder and its profile
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$APP_DIR $PARENT_DIR $CONF_DIR
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
RestrictSUIDSGID=true
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
UMask=0077
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable comfyos.service >/dev/null
echo "FERTIG: Dienst eingerichtet."
systemctl restart comfyos.service
sleep 5

CODE_FILE="$CONF_DIR/setup_code.txt"
IP="$(hostname -I | awk '{print $1}')"
SUFFIX=""; [ "$PORT" != "80" ] && SUFFIX=":$PORT"

echo
echo "  ComfyOS läuft. Im WLAN am Handy öffnen:"
echo "     http://$NAME$SUFFIX/          (FRITZ!Box)"
echo "     http://$NAME.local$SUFFIX/    (iPhone, Mac, Windows)"
echo "     http://$IP$SUFFIX/"
if [ -f "$CODE_FILE" ]; then
  echo
  echo "  Einrichtungscode für die Ersteinrichtung:  $(cat "$CODE_FILE")"
fi
echo
echo "  Status:  systemctl status comfyos     Logs:  journalctl -u comfyos -f"
if [ "${NEED_REBOOT:-0}" = "1" ]; then
  echo
  echo "  Der Hostname wurde geändert. Bitte den Pi einmal neu starten (sudo reboot),"
  echo "  damit die FRITZ!Box den Namen \"$NAME\" lernt."
fi
