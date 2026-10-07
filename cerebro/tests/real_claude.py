"""Teste REAL (custa dinheiro): 3 imagens -> recortes -> Claude Haiku. Não corre com o pytest.

Uso: docker compose run --rm --no-deps -v <pasta>:/real cerebro \
       python -m tests.real_claude x1,y1,x2,y2 /real/a.jpg /real/b.jpg /real/c.jpg [margem]
"""
import json
import os
import sys

import anthropic
import yaml

from app import visao
from app.verificador import Verificador

L = yaml.safe_load(open("/config/config.yaml"))["limites"]
caixa = [int(v) for v in sys.argv[1].split(",")]
margem = float(sys.argv[5]) if len(sys.argv) > 5 else L["claude_margem"]
recortes = [visao.recorte(visao.abrir(open(p, "rb").read()), caixa, margem, L["claude_lado_minimo_px"])
            for p in sys.argv[2:5]]
for i, jpg in enumerate(recortes, 1):
    open(os.path.join(os.path.dirname(sys.argv[2]), f"recorte_m{margem}_{i}.jpg"), "wb").write(jpg)
cliente = anthropic.Anthropic(timeout=L["claude_tempo_max_s"], max_retries=0)
r = Verificador(cliente, L).verificar(recortes)
print("ok:", r.ok, "| erro:", r.erro or "-")
print("JSON:", json.dumps(json.loads(r.resposta), ensure_ascii=False, indent=2) if r.ok else r.resposta)
print(f"tempo: {r.duracao_s:.1f} s | tokens: {r.tokens_entrada} entrada + {r.tokens_saida} saída | custo: {r.custo_usd:.5f} USD")
print("alerta (algum valor >= %.1f):" % L["claude_limiar"], r.ok and any(v >= L["claude_limiar"] for v in r.valores.values()))
