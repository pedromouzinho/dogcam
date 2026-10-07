#!/usr/bin/env bash
# Estado da energia do servidor. Só lê /sys, não precisa de root.
# Usado pelo verificar-arranque.sh (com source) e pelo cérebro, que o corre e lê
# a linha "adaptador=1 bateria=69 mhz_max=2300".

energia_adaptador() { cat /sys/class/power_supply/ADP1/online; }
energia_bateria() { cat /sys/class/power_supply/BAT0/capacity; }

# MHz mais alto de todas as CPUs em 5 leituras (1 por segundo). Com o Frigate a
# trabalhar há sempre uma CPU acima de 1 GHz; se nenhuma passar, a placa-mãe
# está a travar a CPU (BD PROCHOT, ver CLAUDE.md).
energia_mhz_max() {
  local mhz=0 v f
  for _ in 1 2 3 4 5; do
    for f in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq; do
      v=$(( $(cat "$f") / 1000 ))
      [ "$v" -gt "$mhz" ] && mhz=$v
    done
    sleep 1
  done
  echo "$mhz"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "adaptador=$(energia_adaptador) bateria=$(energia_bateria) mhz_max=$(energia_mhz_max)"
fi
