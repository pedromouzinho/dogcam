"""Teste ao vivo do ANDA DEMASIADO sem o cão verdadeiro a interferir: uma instância de
teste das regras recebe mensagens MQTT falsas (formato do Frigate) com o relógio
acelerado, e o alerta sai pelo envio verdadeiro do cérebro (Telegram + SQLite), marcado TESTE.

Uso: docker exec cerebro python -m tests.simular_anda
"""
import json
import time

from app.main import CAMARA, L, Cerebro
from app.regras import Alerta, Regras

c = Cerebro()
r = Regras(L, CAMARA)                      # regras novas: o cão verdadeiro não entra aqui
t0 = t = time.time()
r.camara_fps(3.0, t)


def evento(tipo, zonas, parado):
    after = {"id": "teste-anda", "camera": CAMARA, "label": "dog", "current_zones": zonas, "stationary": parado,
             "false_positive": False, "start_time": t0, "end_time": None, "box": [400, 300, 700, 600]}
    return r.mqtt("frigate/events", json.dumps({"type": tipo, "after": after}).encode(), t)


acoes = evento("new", ["quarto"], False)
while t - t0 < 130:
    t += 1
    if int(t - t0) % 3 == 0:
        acoes += evento("update", ["quarto"], False)
    if int(t - t0) % 5 == 0:
        acoes += r.camara_fps(3.0, t) + r.tick(t)
for a in acoes:
    if isinstance(a, Alerta):
        print(f"alerta {a.tipo}: {a.texto}")
        a.texto = "TESTE (cão simulado): " + a.texto
        c.tratar([a], time.time())
print("alertas:", [a.tipo for a in acoes if isinstance(a, Alerta)])
