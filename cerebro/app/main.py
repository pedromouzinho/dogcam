"""Cérebro DogCam: liga o MQTT do Frigate, as regras, o SQLite e o Telegram."""
import json
import logging
import os
import subprocess
import threading
import time

import paho.mqtt.client as mqtt
import requests
import yaml

from .comandos import AJUDA, Comandos
from .db import DB
from .regras import Alerta, Diario, Regras, sitio_de

log = logging.getLogger("cerebro")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CFG = yaml.safe_load(open(os.environ.get("CEREBRO_CONFIG", "/config/config.yaml")))
L = CFG["limites"]
CAMARA = CFG["camara"]
FRIGATE = CFG["frigate_url"]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or None
HEALTHCHECK = os.environ.get("HEALTHCHECK_URL") or None
TG = f"https://api.telegram.org/bot{TOKEN}"


def seguro(e: Exception) -> str:
    """Texto de um erro sem segredos (os URLs do Telegram e do healthcheck têm-nos)."""
    t = f"{type(e).__name__}: {e}"
    for s in (TOKEN, HEALTHCHECK):
        if s:
            t = t.replace(s, "***")
    return t


class Cerebro:
    def __init__(self):
        self.lock = threading.RLock()
        self.db = DB(CFG["sqlite"])
        self.regras = Regras(L, CAMARA, **self.db.estado_inicial())
        self.cmd = Comandos(CHAT_ID, self.regras, self.db, self.enviar_texto, self.enviar_foto, L, self.lock)
        self.frigate_ok = False

    # -- saída ----------------------------------------------------------------
    def imagem(self):
        try:
            r = requests.get(f"{FRIGATE}/api/{CAMARA}/latest.jpg", params={"bbox": 1, "h": 720}, timeout=10)
            r.raise_for_status()
            return r.content
        except Exception as e:
            log.warning("sem imagem do Frigate: %s", seguro(e))
            return None

    def enviar_texto(self, texto: str) -> bool:
        if not CHAT_ID:
            log.warning("TELEGRAM_CHAT_ID não definido; não envio: %s", texto)
            return False
        try:
            requests.post(f"{TG}/sendMessage", data={"chat_id": CHAT_ID, "text": texto}, timeout=20).raise_for_status()
            return True
        except Exception as e:
            log.error("Telegram falhou: %s", seguro(e))
            return False

    def enviar_foto(self, legenda: str) -> bool:
        jpg = self.imagem()
        if jpg is None:
            return self.enviar_texto(legenda + "\n(sem imagem do Frigate)")
        if not CHAT_ID:
            log.warning("TELEGRAM_CHAT_ID não definido; não envio: %s", legenda)
            return False
        try:
            requests.post(f"{TG}/sendPhoto", data={"chat_id": CHAT_ID, "caption": legenda},
                          files={"photo": ("agora.jpg", jpg, "image/jpeg")}, timeout=30).raise_for_status()
            return True
        except Exception as e:
            log.error("Telegram falhou: %s", seguro(e))
            return False

    def tratar(self, acoes, agora):
        for a in acoes:
            if isinstance(a, Alerta):
                log.info("ALERTA %s: %s", a.tipo, a.texto)
                ok = self.enviar_foto(a.texto) if a.foto else self.enviar_texto(a.texto)
                self.db.alerta(a, agora, ok)
            elif isinstance(a, Diario):
                log.info("diário %s: %s", a.tipo, a.texto)
                self.db.diario(a, agora)

    # -- MQTT -----------------------------------------------------------------
    def ao_ligar(self, cliente, *_):
        log.info("MQTT ligado")
        cliente.subscribe([("frigate/events", 0), (f"frigate/{CAMARA}/motion", 0)])

    def mensagem(self, _cliente, _ud, m):
        agora = time.time()
        try:
            with self.lock:
                acoes = self.regras.mqtt(m.topic, m.payload, agora)
                if m.topic == "frigate/events":
                    ev = json.loads(m.payload)
                    if (ev.get("after") or {}).get("camera") == CAMARA:
                        self.db.deteccao(ev, sitio_de(set(ev["after"].get("current_zones") or [])))
                self.tratar(acoes, agora)
        except Exception:
            log.exception("erro a tratar %s", m.topic)

    # -- ciclos ---------------------------------------------------------------
    def ciclo_camara(self):
        ultimo_ping = 0.0
        while True:
            agora = time.time()
            try:
                s = requests.get(f"{FRIGATE}/api/stats", timeout=5).json()
                fps = s["cameras"][CAMARA]["camera_fps"]
                self.frigate_ok = True
            except Exception as e:
                fps, self.frigate_ok = None, False
                log.warning("Frigate sem resposta: %s", seguro(e))
            with self.lock:
                acoes = self.regras.camara_fps(fps, agora) + self.regras.tick(agora)
                self.tratar(acoes, agora)
                camara_ok = self.regras.camara_ok()
            if HEALTHCHECK and agora - ultimo_ping >= CFG["intervalos"]["healthcheck_s"]:
                ultimo_ping = agora
                if self.frigate_ok and camara_ok:
                    try:
                        requests.get(HEALTHCHECK, timeout=10).raise_for_status()
                    except Exception as e:
                        log.warning("healthcheck falhou: %s", seguro(e))
                else:
                    log.warning("sem ping ao healthcheck: Frigate ou câmara em baixo")
            time.sleep(CFG["intervalos"]["camara_s"])

    def ciclo_servidor(self):
        while True:
            try:
                linha = subprocess.run(["bash", "/scripts/energia.sh"], capture_output=True, text=True, timeout=30).stdout
                v = dict(p.split("=") for p in linha.split())
                agora = time.time()
                with self.lock:
                    self.tratar(self.regras.servidor(v["adaptador"] == "1", int(v["bateria"]), int(v["mhz_max"]), agora), agora)
            except Exception as e:
                log.warning("verificação de energia falhou: %s", seguro(e))
            time.sleep(CFG["intervalos"]["servidor_s"])

    def ciclo_telegram(self):
        offset = None
        while True:
            try:
                r = requests.get(f"{TG}/getUpdates", params={"timeout": 30, "offset": offset,
                                 "allowed_updates": json.dumps(["message"])}, timeout=40).json()
                for u in r.get("result", []):
                    offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if not self.cmd.tratar(msg, time.time()):
                        log.warning("mensagem ignorada do chat %s (%s): %r", msg.get("chat", {}).get("id"),
                                    msg.get("chat", {}).get("first_name"), (msg.get("text") or "")[:40])
            except Exception as e:
                log.warning("Telegram (getUpdates) falhou: %s", seguro(e))
                time.sleep(5)

    def correr(self):
        try:
            requests.post(f"{TG}/setMyCommands", timeout=20, json={"commands": [
                {"command": c, "description": d} for c, d in (
                    ("foto", "Imagem de agora"), ("estado", "Onde está e últimos eventos"),
                    ("pausa", "Pausa os alertas do cão [min]"), ("retomar", "Volta a avisar"),
                    ("diario", "Resumo das últimas 12 h"))]})
        except Exception as e:
            log.warning("setMyCommands falhou: %s", seguro(e))
        cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cerebro")
        cliente.on_connect = self.ao_ligar
        cliente.on_message = self.mensagem
        cliente.reconnect_delay_set(1, 30)
        cliente.connect_async(CFG["mqtt_host"], 1883)
        cliente.loop_start()
        for alvo in (self.ciclo_camara, self.ciclo_servidor, self.ciclo_telegram):
            threading.Thread(target=alvo, daemon=True, name=alvo.__name__).start()
        log.info("cérebro a correr (câmara %s, chat %s)", CAMARA, "definido" if CHAT_ID else "NÃO definido")
        self.enviar_texto("Cérebro DogCam ligado.\n\n" + AJUDA)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    Cerebro().correr()
