#!/usr/bin/env python3
"""DogCam: desliga o BD PROCHOT do MacBook Pro 2017 e vigia se ele volta.

O BD PROCHOT (bit 0 do MSR 0x1FC) deixa a placa-mãe travar a CPU a 400 MHz. Neste
MacBook isso acontece com a temperatura normal (ver CLAUDE.md). O travão interno da
CPU por temperatura (TCC, ~100 °C) continua ativo; este programa só desliga o externo.

Instalado como root em /usr/local/sbin/dogcam-prochot (não correr a partir de ~/dogcam
como root: o utilizador pedro podia alterar o ficheiro).

  dogcam-prochot ler           estado do travão em cada CPU (root)
  dogcam-prochot desligar      desliga-o em todas as CPUs, se a temperatura deixar (root)
  dogcam-prochot vigiar        a cada minuto (timer): CPU presa com carga -> desligar e avisar (root)
  dogcam-prochot testar        verifica as regras de decisão (sem root, não mexe em nada)
  dogcam-prochot aviso-teste   envia uma mensagem de teste para o Telegram (root, para ler o .env)
"""
import glob
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

POWER_CTL = 0x1FC          # bit 0 = BD PROCHOT ligado
THERM_STATUS = 0x19C       # bits 2/3 = PROCHOT ativo / registado
PERF_LIMIT = 0x64F         # bit 0 = a CPU está limitada por PROCHOT
ENV = "/home/pedro/dogcam/.env"
ESTADO = "/run/dogcam-prochot.json"   # último aviso de cada tipo (para não repetir)
REPETIR_AVISO_S = 15 * 60
MHZ_MINIMO = 1000
TEMP_MEXER_ABAIXO = 85     # só desliga o travão abaixo disto
TEMP_PERIGO = 90           # a partir disto não mexe e avisa


# ---------------------------------------------------------------- decisão (testável)
def decidir(mhz_carga: int, adaptador: bool, temp_c: float) -> tuple[str, str]:
    """Devolve (acao, motivo). acao: 'nada', 'desligar' ou 'avisar' (avisar sem mexer)."""
    if mhz_carga >= MHZ_MINIMO:
        return "nada", f"CPU a {mhz_carga} MHz com carga"
    if temp_c >= TEMP_PERIGO:
        return "avisar", f"CPU presa a {mhz_carga} MHz mas a {temp_c:.0f} °C (>= {TEMP_PERIGO} °C): não mexi no travão"
    if not adaptador:
        return "avisar", f"CPU presa a {mhz_carga} MHz e o servidor está só com a bateria: não mexi no travão"
    if temp_c >= TEMP_MEXER_ABAIXO:
        return "avisar", f"CPU presa a {mhz_carga} MHz a {temp_c:.0f} °C (entre {TEMP_MEXER_ABAIXO} e {TEMP_PERIGO} °C): não mexi no travão"
    return "desligar", f"CPU presa a {mhz_carga} MHz com o carregador ligado e a {temp_c:.0f} °C"


def testar():
    assert decidir(2300, True, 60)[0] == "nada"
    assert decidir(400, True, 62)[0] == "desligar"
    assert decidir(400, True, 84.9)[0] == "desligar"
    assert decidir(400, True, 85)[0] == "avisar"
    assert decidir(400, True, 89)[0] == "avisar"
    assert decidir(400, True, 90)[0] == "avisar" and "não mexi" in decidir(400, True, 95)[1]
    assert decidir(400, False, 60)[0] == "avisar"
    assert decidir(400, False, 95)[0] == "avisar" and "90" in decidir(400, False, 95)[1]
    print("regras de decisão: OK")
    assert numero_cpu("/dev/cpu/0/msr") == 0 and numero_cpu("/dev/cpu/12/msr") == 12
    for errado in ("/dev/cpu/msr", "/dev/cpu/x/msr", "/dev/cpu/0/cpuid"):
        try:
            numero_cpu(errado)
            raise AssertionError(errado)
        except ValueError:
            pass
    reais = cpus()                                   # caminhos reais deste sistema (não precisa de root)
    esperado = sorted(int(d) for d in os.listdir("/dev/cpu") if d.isdigit() and os.path.exists(f"/dev/cpu/{d}/msr"))
    assert reais == esperado and reais, (reais, esperado)
    print(f"cpus(): OK {reais}")


# ---------------------------------------------------------------- leituras
def numero_cpu(caminho: str) -> int:
    """'/dev/cpu/12/msr' -> 12"""
    partes = caminho.split("/")
    if len(partes) != 5 or partes[:3] != ["", "dev", "cpu"] or partes[4] != "msr":
        raise ValueError(f"caminho inesperado: {caminho}")
    return int(partes[3])


def cpus():
    return sorted(numero_cpu(p) for p in glob.glob("/dev/cpu/[0-9]*/msr"))


def rdmsr(cpu: int, reg: int) -> int:
    fd = os.open(f"/dev/cpu/{cpu}/msr", os.O_RDONLY)
    try:
        return struct.unpack("<Q", os.pread(fd, 8, reg))[0]
    finally:
        os.close(fd)


def wrmsr(cpu: int, reg: int, valor: int):
    fd = os.open(f"/dev/cpu/{cpu}/msr", os.O_WRONLY)
    try:
        os.pwrite(fd, struct.pack("<Q", valor), reg)
    finally:
        os.close(fd)


def temperatura() -> float:
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        if open(f"{z}/type").read().strip() == "x86_pkg_temp":
            return int(open(f"{z}/temp").read()) / 1000
    return max(int(open(f).read()) for f in glob.glob("/sys/class/thermal/thermal_zone*/temp")) / 1000


def adaptador() -> bool:
    return open("/sys/class/power_supply/ADP1/online").read().strip() == "1"


def mhz_com_carga() -> int:
    """MHz mais alto com uma CPU ocupada 3 s: normal sobe para ~2300; com o travão fica presa."""
    p = subprocess.Popen([sys.executable, "-I", "-c",
                          "import time\nf = time.monotonic() + 3\nwhile time.monotonic() < f: pass"])
    time.sleep(1)
    mhz = 0
    for _ in range(2):
        for f in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
            mhz = max(mhz, int(open(f).read()) // 1000)
        time.sleep(1)
    p.wait()
    return mhz


# ---------------------------------------------------------------- ações
def ler():
    for c in cpus():
        p, t, l = rdmsr(c, POWER_CTL), rdmsr(c, THERM_STATUS), rdmsr(c, PERF_LIMIT)
        print(f"cpu{c}: BD PROCHOT {'LIGADO' if p & 1 else 'desligado'} (0x1FC={p:#x}) | "
              f"PROCHOT ativo agora: {'sim' if t & 4 else 'não'} (0x19C={t:#x}) | limite por PROCHOT: {'sim' if l & 1 else 'não'} (0x64F={l:#x})")
    print(f"temperatura {temperatura():.0f} °C | carregador {'ligado' if adaptador() else 'DESLIGADO'}")


def desligar() -> bool:
    """Limpa o bit 0 do 0x1FC em todas as CPUs. Devolve True se ficou desligado em todas."""
    ok = True
    for c in cpus():
        v = rdmsr(c, POWER_CTL)
        if v & 1:
            wrmsr(c, POWER_CTL, v & ~1)
        ok &= not (rdmsr(c, POWER_CTL) & 1)
    return ok


def aviso(tipo: str, texto: str, repetir=True):
    """Envia para o Telegram do Pedro (TELEGRAM_TOKEN e TELEGRAM_CHAT_ID do .env), sem repetir o mesmo tipo em 15 min."""
    print(texto, flush=True)
    estado = {}
    try:
        estado = json.load(open(ESTADO))
    except (OSError, ValueError):
        pass
    if not repetir and time.time() - estado.get(tipo, 0) < REPETIR_AVISO_S:
        return
    env = dict(re.findall(r"^([A-Z_]+)=(.*)$", open(ENV).read(), re.M))
    dados = urllib.parse.urlencode({"chat_id": env["TELEGRAM_CHAT_ID"], "text": "Servidor: " + texto}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{env['TELEGRAM_TOKEN']}/sendMessage", dados, timeout=20)
        estado[tipo] = time.time()
        json.dump(estado, open(ESTADO, "w"))
    except Exception as e:                       # nunca mostrar o URL (tem o token)
        print(f"Telegram falhou: {type(e).__name__}", flush=True)


def vigiar(arranque=False):
    mhz = mhz_com_carga() if not arranque else 0
    temp, adp = temperatura(), adaptador()
    if arranque:   # no arranque desliga sempre, se a temperatura deixar
        acao, motivo = ("desligar", "arranque") if temp < TEMP_PERIGO else ("avisar", f"arranque a {temp:.0f} °C: não mexi no travão")
    else:
        acao, motivo = decidir(mhz, adp, temp)
    if acao == "nada":
        return
    if acao == "avisar":
        aviso("avisar", motivo, repetir=False)
        return
    ok = desligar()
    if arranque:
        print(f"BD PROCHOT {'desligado' if ok else 'NÃO foi possível desligar'} no arranque ({temp:.0f} °C)", flush=True)
        if not ok:
            aviso("falhou", "não consegui desligar o BD PROCHOT no arranque.", repetir=False)
        return
    depois = mhz_com_carga()
    aviso("desligado", f"{motivo}. Desliguei o travão BD PROCHOT: agora {depois} MHz com carga"
          + ("." if ok and depois >= MHZ_MINIMO else " — NÃO resolveu, vê o carregador e faz reset ao SMC."))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "testar":
        testar()
    elif os.geteuid() != 0:
        sys.exit("corre com sudo")
    elif cmd == "ler":
        ler()
    elif cmd == "desligar":
        print("desligado em todas as CPUs" if desligar() else "FALHOU em alguma CPU")
    elif cmd == "vigiar":
        vigiar("--arranque" in sys.argv)
    elif cmd == "aviso-teste":
        aviso("teste", "teste do vigia do PROCHOT (dogcam-prochot aviso-teste).")
    else:
        sys.exit(__doc__)
