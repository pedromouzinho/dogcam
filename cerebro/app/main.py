"""Cérebro DogCam: liga o MQTT do Frigate, as regras, o Claude, o SQLite e o Telegram."""
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

import anthropic
import paho.mqtt.client as mqtt
import requests
import yaml

from . import visao
from .comandos import AJUDA, TZ, Comandos, inicio_do_dia
from .db import DB
from .regras import Alerta, Diario, Regras, sitio_de
from .suspeita import SITIOS, PedirVerificacao, Suspeita
from .verificador import Verificador

log = logging.getLogger("cerebro")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

CFG = yaml.safe_load(open(os.environ.get("CEREBRO_CONFIG", "/config/config.yaml")))
L = CFG["limites"]
CAMARA = CFG["camara"]
FRIGATE = CFG["frigate_url"]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or None
HEALTHCHECK = os.environ.get("HEALTHCHECK_URL") or None
TG = f"https://api.telegram.org/bot{TOKEN}"
COM_RECORTES = ("suspeita", "suspeita_nao_confirmada")


def seguro(e: Exception) -> str:
    """Texto de um erro sem segredos (os URLs do Telegram e do healthcheck têm-nos)."""
    t = f"{type(e).__name__}: {e}"
    for s in (TOKEN, HEALTHCHECK, os.environ.get("ANTHROPIC_API_KEY")):
        if s:
            t = t.replace(s, "***")
    return t


def botoes(aid: int) -> str:
    return json.dumps({"inline_keyboard": [[{"text": "✅ Certo", "callback_data": f"fb:{aid}:1"},
                                            {"text": "❌ Errado", "callback_data": f"fb:{aid}:0"}]]})


class Cerebro:
    def __init__(self):
        self.lock = threading.RLock()
        self.db = DB(CFG["sqlite"])
        self.regras = Regras(L, CAMARA, **self.db.estado_inicial())
        self.cmd = Comandos(CHAT_ID, self.regras, self.db, self.enviar_texto, self.enviar_foto, L, self.lock, self.tratar)
        cliente = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            # O tempo máximo é imposto pelo Verificador; sem novas tentativas para não passar dos 15 s.
            cliente = anthropic.Anthropic(timeout=L["claude_tempo_max_s"], max_retries=0)
        self.verificador = Verificador(cliente, L)
        self.suspeita = Suspeita(L, self.regras._alerta)
        self.quadros = deque(maxlen=3)   # últimas 3 imagens da câmara (para medir movimento e recortar)
        self.frigate_ok = False

    # -- saída ----------------------------------------------------------------
    def imagem(self, bbox=1):
        try:
            r = requests.get(f"{FRIGATE}/api/{CAMARA}/latest.jpg", params={"bbox": bbox, "h": 720}, timeout=10)
            r.raise_for_status()
            return r.content
        except Exception as e:
            log.warning("sem imagem do Frigate: %s", seguro(e))
            return None

    def _telegram(self, metodo, **kw) -> bool:
        if not CHAT_ID:
            log.warning("TELEGRAM_CHAT_ID não definido; não envio")
            return False
        try:
            requests.post(f"{TG}/{metodo}", timeout=30, **kw).raise_for_status()
            return True
        except Exception as e:
            log.error("Telegram (%s) falhou: %s", metodo, seguro(e))
            return False

    def enviar_texto(self, texto: str, aid: int | None = None) -> bool:
        dados = {"chat_id": CHAT_ID, "text": texto}
        if aid is not None:
            dados["reply_markup"] = botoes(aid)
        return self._telegram("sendMessage", data=dados)

    def enviar_foto(self, legenda: str, jpg: bytes | None = None, aid: int | None = None):
        """Envia uma foto (a imagem de agora da câmara, se não vier nenhuma). Devolve (ok, jpg)."""
        jpg = jpg or self.imagem()
        if jpg is None:
            return self.enviar_texto(legenda + "\n(sem imagem do Frigate)", aid), None
        dados = {"chat_id": CHAT_ID, "caption": legenda[:1000]}
        if aid is not None:
            dados["reply_markup"] = botoes(aid)
        return self._telegram("sendPhoto", data=dados, files={"photo": ("foto.jpg", jpg, "image/jpeg")}), jpg

    def tratar(self, acoes, agora):
        for a in acoes:
            if isinstance(a, Alerta):
                log.info("ALERTA %s: %s", a.tipo, a.texto.replace("\n", " | "))
                aid = self.db.alerta(a, agora)
                if a.foto:
                    meio = a.imagens[len(a.imagens) // 2] if a.imagens else None
                    ok, jpg = self.enviar_foto(a.texto, meio, aid)
                    self.db.guardar_recortes(aid, a.imagens or ([jpg] if jpg else []))
                else:
                    ok = self.enviar_texto(a.texto, aid)
                self.db.marcar_enviado(aid, ok)
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

    # -- SUSPEITA (fase 2B) ---------------------------------------------------
    def recortes(self, caixa):
        return [visao.recorte(img, caixa, L["claude_margem"], L["claude_lado_minimo_px"]) for _, img in self.quadros]

    def ciclo_vigia(self):
        """Enquanto o cão está na cama/descanso, mede o movimento dentro da caixa dele."""
        while True:
            time.sleep(L["vigia_s"])
            agora = time.time()
            try:
                with self.lock:
                    r = self.regras
                    sitio, caixa = r.ultimo_sitio, r.caixa
                    visivel, parado, pausa = r.visivel_certo(), r.parado, r.em_pausa(agora)
                    ativo = sitio in SITIOS and caixa is not None and r.camara_ok() and not pausa
                if not ativo:
                    self.quadros.clear()
                    with self.lock:
                        self.tratar(self.suspeita.amostra(agora, sitio, visivel, parado, 0.0, 0, "", True), agora)
                    continue
                jpg = self.imagem(bbox=0)
                if jpg is None:
                    continue
                img = visao.abrir(jpg)
                mov = visao.movimento(self.quadros[-1][1], img, caixa) if self.quadros else 0.0
                self.quadros.append((agora, img))
                n, _ = self.db.chamadas(inicio_do_dia(agora))
                dia = datetime.fromtimestamp(agora, TZ).date().isoformat()
                with self.lock:
                    acoes = self.suspeita.amostra(agora, sitio, visivel, parado, mov, n, dia, False)
                    pedir = any(isinstance(a, PedirVerificacao) for a in acoes)
                    acoes = [a for a in acoes if not isinstance(a, PedirVerificacao)]
                    for a in acoes:
                        if isinstance(a, Alerta) and a.tipo in COM_RECORTES and a.imagens is None:
                            a.imagens = self.recortes(caixa)
                    self.tratar(acoes, agora)
                if pedir:
                    threading.Thread(target=self.verificar, args=(self.recortes(caixa), sitio), daemon=True).start()
            except Exception:
                log.exception("erro na vigia")

    def verificar(self, recortes, sitio):
        res = self.verificador.verificar(recortes)
        agora = time.time()
        log.info("Claude: %s em %.1f s, %d+%d tokens, %.4f USD%s", "ok" if res.ok else "FALHOU", res.duracao_s,
                 res.tokens_entrada, res.tokens_saida, res.custo_usd, "" if res.ok else f" ({res.erro})")
        self.db.verificacao(res, agora)
        with self.lock:
            acoes = self.suspeita.resultado(res, agora, sitio)
            for a in acoes:
                if isinstance(a, Alerta) and a.tipo in COM_RECORTES:
                    a.imagens = recortes
            self.tratar(acoes, agora)

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

    def botao(self, cb):
        resposta = self.cmd.botao(cb, time.time())
        if resposta is None:
            log.warning("botão ignorado do chat %s", (cb.get("message") or {}).get("chat", {}).get("id"))
            return
        self._telegram("answerCallbackQuery", data={"callback_query_id": cb["id"], "text": resposta})
        m = cb.get("message") or {}
        self._telegram("editMessageReplyMarkup", data={
            "chat_id": m.get("chat", {}).get("id"), "message_id": m.get("message_id"),
            "reply_markup": json.dumps({"inline_keyboard": [[{"text": resposta, "callback_data": cb.get("data", "fb:0:0")}]]})})
        log.info("feedback: %s", resposta)

    def ciclo_telegram(self):
        offset = None
        while True:
            try:
                r = requests.get(f"{TG}/getUpdates", params={"timeout": 30, "offset": offset,
                                 "allowed_updates": json.dumps(["message", "callback_query"])}, timeout=40).json()
                for u in r.get("result", []):
                    offset = u["update_id"] + 1
                    if "callback_query" in u:
                        self.botao(u["callback_query"])
                        continue
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
                    ("diario", "Resumo das últimas 12 h"), ("teste", "Alerta de teste (respeita a /pausa)"))]})
        except Exception as e:
            log.warning("setMyCommands falhou: %s", seguro(e))
        cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cerebro")
        cliente.on_connect = self.ao_ligar
        cliente.on_message = self.mensagem
        cliente.reconnect_delay_set(1, 30)
        cliente.connect_async(CFG["mqtt_host"], 1883)
        cliente.loop_start()
        for alvo in (self.ciclo_camara, self.ciclo_servidor, self.ciclo_telegram, self.ciclo_vigia):
            threading.Thread(target=alvo, daemon=True, name=alvo.__name__).start()
        log.info("cérebro a correr (câmara %s, chat %s, Claude %s)", CAMARA, "definido" if CHAT_ID else "NÃO definido",
                 "ligado" if self.verificador.cliente else "DESLIGADO (sem ANTHROPIC_API_KEY)")
        self.enviar_texto("Cérebro DogCam ligado.\n\n" + AJUDA)
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    Cerebro().correr()
