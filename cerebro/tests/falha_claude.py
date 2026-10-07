"""Teste ao vivo da falha da API do Claude: corre a regra da SUSPEITA com um cliente
Anthropic apontado para um endereço que não existe e envia o alerta verdadeiro
("suspeita NÃO confirmada") para o Telegram, marcado como TESTE.

Uso: docker exec cerebro python -m tests.falha_claude
"""
import time

import anthropic

from app import visao
from app.main import L, Cerebro
from app.verificador import Verificador

c = Cerebro()
# Cliente real do SDK, mas com a API num endereço inexistente: falha de ligação verdadeira.
c.verificador = Verificador(anthropic.Anthropic(api_key="falso", base_url="http://claude-em-baixo.invalid",
                                                timeout=L["claude_tempo_max_s"], max_retries=0), L)
jpg = c.imagem(bbox=0)
img = visao.abrir(jpg)
c.quadros.extend([(time.time(), img)] * 3)
t0 = time.time()
for passo in range(80):                         # ~32 s de "cabeça a mexer na cama"
    agora = t0 + passo * 0.4
    acoes = c.suspeita.amostra(agora, "cama", False, True, 0.05, 0, "teste", False)
    for a in acoes:
        if type(a).__name__ == "PedirVerificacao":
            res = c.verificador.verificar(c.recortes([300, 200, 900, 600]))
            print(f"{time.strftime('%H:%M:%S')} Claude: ok={res.ok} erro={res.erro!r} ({res.duracao_s:.1f} s)", flush=True)
            for b in c.suspeita.resultado(res, agora, "cama"):
                if type(b).__name__ == "Alerta":
                    b.texto = "TESTE (falha simulada da API): " + b.texto
                    b.imagens = c.recortes([300, 200, 900, 600])
                c.tratar([b], time.time())
        elif type(a).__name__ == "Alerta":
            a.texto = "TESTE (falha simulada da API): " + a.texto
            a.imagens = c.recortes([300, 200, 900, 600])
            print(f"{time.strftime('%H:%M:%S')} alerta {a.tipo} aos {agora - t0:.0f} s de suspeita", flush=True)
            c.tratar([a], time.time())
