#!/usr/bin/env bash
# Placa de rede do cabo (ver CLAUDE.md, "Rede principal"). Só lê /sys e o .env, não precisa de root.
# Sem o cabo o Wi-Fi assume a rota, mas o iPad não chega ao servidor pelo Wi-Fi: câmara sem imagem.
# Usado pelo verificar-arranque.sh (com source) e pelo dogcam-cabo.timer (a cada minuto, como pedro).
#   cabo.sh              estado: "ok" ou o problema
#   cabo.sh vigiar       o mesmo, e avisa no Telegram uma vez quando falha e outra quando volta
#   cabo.sh aviso-teste  envia uma mensagem de teste para o Telegram
# Para testar o aviso sem mexer na rede: CABO_IF=enxteste RUNTIME_DIRECTORY=/tmp cabo.sh vigiar

CABO_IF=${CABO_IF:-enxd0c0bf27ca26}
CABO_IP=192.168.1.69
CABO_ENV=/home/pedro/dogcam/.env
CABO_ESTADO=${RUNTIME_DIRECTORY:-/run/dogcam-cabo}/estado   # apagado em cada arranque (/run)

# "ok", ou uma frase com o problema.
cabo_estado() {
  local d=/sys/class/net/$CABO_IF
  if [ ! -e "$d" ]; then
    echo "arrancou sem a placa de rede do cabo (hub desligado, ou ligado depois do arranque)"
  elif [ "$(cat "$d/operstate")" != up ]; then
    echo "a placa de rede do cabo está ligada mas o cabo não tem ligação ao router"
  elif ! ip -4 -br addr show "$CABO_IF" | grep -q " $CABO_IP/"; then
    echo "o cabo de rede não tem o IP $CABO_IP"
  else
    echo ok
  fi
}

# Velocidade USB da placa: 5000 = normal; 480 = hub ligado depois do arranque (só USB 2.0).
cabo_usb_mbps() { cat "$(readlink -f "/sys/class/net/$CABO_IF/device")/../speed" 2>/dev/null; }

cabo_aviso() {
  local token chat
  token=$(sed -n 's/^TELEGRAM_TOKEN=//p' "$CABO_ENV")
  chat=$(sed -n 's/^TELEGRAM_CHAT_ID=//p' "$CABO_ENV")
  # O URL vai pelo stdin (curl -K -) para o token não aparecer na lista de processos nem no journal.
  printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" |
    curl -sf -o /dev/null -m 20 -K - --data-urlencode "chat_id=$chat" --data-urlencode "text=Servidor: $1" ||
    { echo "Telegram falhou"; return 1; }
}

# O estado só é guardado depois de o aviso seguir; se o Telegram falhar, tenta no minuto seguinte.
cabo_vigiar() {
  local agora antes
  agora=$(cabo_estado)
  antes=$(cat "$CABO_ESTADO" 2>/dev/null || echo ok)
  echo "$agora"
  [ "$agora" = "$antes" ] && return
  if [ "$agora" = ok ]; then
    cabo_aviso "o cabo de rede voltou ($CABO_IP). Se a câmara não voltar em 3 min, fecha de vez a OctoStream no iPad e volta a abri-la."
  else
    cabo_aviso "$agora. Sem o cabo o iPad não chega ao servidor e a câmara fica sem imagem. Desliga o servidor (toque curto no botão), confirma que o hub está ligado ao MacBook e volta a ligá-lo."
  fi && echo "$agora" > "$CABO_ESTADO"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "$1" in
    "")          cabo_estado ;;
    vigiar)      cabo_vigiar ;;
    aviso-teste) cabo_aviso "teste do aviso do cabo de rede (cabo.sh aviso-teste)." && echo "enviado" ;;
    *)           sed -n '5,8p' "$0"; exit 1 ;;
  esac
fi
