"""Publica no MQTT eventos FALSOS do Frigate (um "dog" com id teste-...) para testar o
cérebro ao vivo sem mexer no cão. Acaba sempre com o cão "deitado na cama", para o
cérebro não ficar a pensar que ele desapareceu do quarto.

Uso: docker exec cerebro python -m tests.simular_cao <zona> <segundos>
     ex.: docker exec cerebro python -m tests.simular_cao quarto 130
"""
import json
import sys
import time

import paho.mqtt.client as mqtt

zona, segundos = sys.argv[1], float(sys.argv[2])
oid = f"teste-{int(time.time())}"
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"simulador-{oid}")
c.connect("mosquitto", 1883)
c.loop_start()


def evento(tipo, zonas, parado, fim=None):
    after = {"id": oid, "camera": "quarto_ipad", "label": "dog", "current_zones": zonas, "entered_zones": zonas,
             "stationary": parado, "false_positive": False, "start_time": inicio, "end_time": fim,
             "top_score": 0.9, "score": 0.9, "box": [400, 300, 700, 600]}
    c.publish("frigate/events", json.dumps({"type": tipo, "before": after, "after": after})).wait_for_publish()


inicio = time.time()
evento("new", [zona], False)
print(f"{time.strftime('%H:%M:%S')} cão falso {oid} a andar em {zona} durante {segundos:.0f} s", flush=True)
while time.time() - inicio < segundos:
    time.sleep(3)
    evento("update", [zona], False)
evento("update", ["cama"], True)
evento("end", ["cama"], True, time.time())
print(f"{time.strftime('%H:%M:%S')} fim: cão falso deitado na cama e terminado", flush=True)
c.loop_stop()
