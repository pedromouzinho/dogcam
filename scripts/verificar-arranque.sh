#!/usr/bin/env bash
# Confirma que tudo voltou sozinho depois de um reinício.
# Uso: ~/dogcam/scripts/verificar-arranque.sh

ok()  { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
mau() { printf '  \033[31mFALHA\033[0m %s\n' "$1"; FALHAS=$((FALHAS+1)); }
FALHAS=0

echo "Ligado desde: $(uptime -s)"

systemctl is-active -q docker && ok "Docker ativo" || mau "Docker não está ativo"

for i in $(seq 1 60); do
  estado=$(sudo docker inspect -f '{{.State.Health.Status}}' frigate 2>/dev/null)
  [ "$estado" = healthy ] && break
  sleep 3
done
[ "$estado" = healthy ] && ok "Frigate a correr (healthy)" || mau "Frigate: ${estado:-não existe}"

for c in mosquitto cerebro; do
  [ "$(sudo docker inspect -f '{{.State.Status}}' $c 2>/dev/null)" = running ] && ok "$c a correr" || mau "$c não está a correr"
done

findmnt -n ~/dogcam/storage >/dev/null && ok "Disco virtual de 150 GB montado ($(df -h --output=avail ~/dogcam/storage | tail -1 | tr -d ' ') livres)" || mau "Disco virtual não montado"

systemctl is-active -q mbpfan && ok "mbpfan ativo" || mau "mbpfan não está ativo"

[ "$(cat /sys/devices/system/cpu/intel_pstate/no_turbo)" = 1 ] && ok "Turbo desligado (máx. $(($(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq)/1000)) MHz)" || mau "Turbo ligado"

# Verificações de energia partilhadas com o cérebro (scripts/energia.sh).
source "$(dirname "$0")/energia.sh"
mhz=$(energia_mhz)
[ $mhz -ge 1000 ] && ok "CPU a acelerar (até $mhz MHz)" || mau "CPU presa abaixo de 1 GHz (máx. $mhz MHz): travão PROCHOT? Ver carregador e reset ao SMC"

[ "$(energia_adaptador)" = 1 ] && ok "Carregador ligado (bateria a $(energia_bateria)%)" || mau "Servidor só com a bateria ($(energia_bateria)%): sem corrente!"

tailscale status >/dev/null 2>&1 && ok "Tailscale ligado ($(tailscale ip -4))" || mau "Tailscale desligado"

url=$(tailscale serve status 2>/dev/null | awk '/^https:/{print $1; exit}')
if [ -n "$url" ]; then
  codigo=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$url/")
  [ "$codigo" = 200 ] && ok "tailscale serve: $url responde (HTTP 200)" || mau "tailscale serve: $url respondeu HTTP $codigo"
else
  mau "tailscale serve sem configuração"
fi

sudo ufw status | grep -q 'Status: active' && ok "Firewall (ufw) ativa" || mau "Firewall (ufw) inativa"

echo "  Temperatura da CPU: $(sensors coretemp-isa-0000 2>/dev/null | awk '/Package/{print $4}'), ventoinha: $(cat /sys/devices/platform/applesmc.768/fan1_input) RPM"

[ $FALHAS -eq 0 ] && echo "Tudo bem." || echo "$FALHAS problema(s)."
exit $FALHAS
