"""Fase 2B: verificador com um Claude falso, regra da SUSPEITA, imagem e botões."""
import base64
import io
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app import visao
from app.comandos import Comandos
from app.db import DB
from app.regras import Alerta, Diario, Regras
from app.suspeita import PedirVerificacao, Suspeita
from app.verificador import Resultado, Verificador, validar
from tests.test_regras import LIMITES

BOM = '{"lamber_ou_morder_penso": 0.85, "sem_cone": 0.05, "de_pe_ou_a_saltar": 0.1, "descricao": "Lambe a ligadura verde."}'
CALMO = '{"lamber_ou_morder_penso": 0.1, "sem_cone": 0.0, "de_pe_ou_a_saltar": 0.2, "descricao": "Deitado a dormir."}'
JPG = b"\xff\xd8falso\xff\xd9"


class ClaudeFalso:
    """Imita anthropic.Anthropic().messages.create: devolve texto, levanta um erro ou demora."""

    def __init__(self, texto=BOM, erro=None, demora=0.0, stop="end_turn"):
        self.texto, self.erro, self.demora, self.stop = texto, erro, demora, stop
        self.pedidos = []
        self.messages = self

    def create(self, **kw):
        self.pedidos.append(kw)
        if self.demora:
            time.sleep(self.demora)
        if self.erro:
            raise self.erro
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.texto)], stop_reason=self.stop,
                               usage=SimpleNamespace(input_tokens=1500, output_tokens=80))


def ver(cliente, **mudar):
    return Verificador(cliente, {**LIMITES, **mudar}).verificar([JPG, JPG, JPG])


# ---------------------------------------------------------------- VERIFICADOR
def test_resposta_boa_e_pedido_certo():
    c = ClaudeFalso()
    r = ver(c)
    assert r.ok and r.valores["lamber_ou_morder_penso"] == 0.85 and r.descricao == "Lambe a ligadura verde."
    assert r.custo_usd == pytest.approx(1500 / 1e6 * 1.0 + 80 / 1e6 * 5.0)
    p = c.pedidos[0]
    assert p["model"] == "claude-haiku-5-5"
    imagens = [b for b in p["messages"][0]["content"] if b["type"] == "image"]
    assert len(imagens) == 3 and base64.b64decode(imagens[0]["source"]["data"]) == JPG
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert "ligadura verde" in p["system"] and "cone" in p["system"]


def test_erro_na_chamada():
    r = ver(ClaudeFalso(erro=ConnectionError("rede")))
    assert not r.ok and "falhou" in r.erro


def test_tempo_esgotado():
    t0 = time.monotonic()
    r = ver(ClaudeFalso(demora=1.0), claude_tempo_max_s=0.2)
    assert not r.ok and "tempo esgotado" in r.erro and time.monotonic() - t0 < 0.8


@pytest.mark.parametrize("texto", ["isto não é JSON", "[1, 2]", '{"lamber_ou_morder_penso": 2, "sem_cone": 0, "de_pe_ou_a_saltar": 0, "descricao": "x"}',
                                   '{"lamber_ou_morder_penso": 0.5, "sem_cone": 0.1, "descricao": "falta um campo"}',
                                   '{"lamber_ou_morder_penso": true, "sem_cone": 0, "de_pe_ou_a_saltar": 0, "descricao": "x"}'])
def test_json_invalido(texto):
    r = ver(ClaudeFalso(texto=texto))
    assert not r.ok and "JSON" in r.erro


def test_recusa_ou_resposta_cortada_conta_como_falha():
    assert not ver(ClaudeFalso(stop="refusal")).ok
    assert not ver(ClaudeFalso(stop="max_tokens")).ok


def test_sem_chave_conta_como_falha():
    assert ver(None).erro == "sem ANTHROPIC_API_KEY"


# ---------------------------------------------------------------- SUSPEITA
class Vigia:
    """Corre a Suspeita com um relógio controlado e um Claude falso (resposta imediata)."""

    def __init__(self, resposta=BOM, ok=True, **limites):
        self.r = Regras({**LIMITES, **limites})
        self.s = Suspeita({**LIMITES, **limites}, self.r._alerta)
        self.t, self.saidas, self.pedidos = 1_000_000.0, [], 0
        self.resposta, self.ok = resposta, ok
        self.chamadas_hoje = 0
        self.atraso = 0      # leituras até a resposta do Claude chegar

    def passa(self, segundos, mov=0.05, sitio="cama", visivel=True, parado=True, pausa=False):
        pendente = []
        fim = self.t + segundos
        while self.t < fim:
            self.t += 0.4
            acoes = self.s.amostra(self.t, sitio, visivel, parado, mov, self.chamadas_hoje, "2026-10-07", pausa)
            for a in acoes:
                if isinstance(a, PedirVerificacao):
                    self.pedidos += 1
                    self.chamadas_hoje += 1
                    pendente.append(self.atraso)
                else:
                    self.saidas.append(a)
            for i, falta in enumerate(pendente):
                if falta <= 0:
                    res = (Resultado(True, *validar(self.resposta)) if self.ok
                           else Resultado(False, erro="a chamada falhou (ConnectionError)"))
                    self.saidas += self.s.resultado(res, self.t, sitio)
                pendente[i] = falta - 1
            pendente = [p for p in pendente if p >= 0]
        return self

    def alertas(self, tipo=None):
        return [a for a in self.saidas if isinstance(a, Alerta) and (tipo is None or a.tipo == tipo)]


def test_cabeca_a_mexer_na_cama_mais_de_5_s_pede_verificacao_e_alerta():
    v = Vigia().passa(4.5)
    assert v.pedidos == 0
    v.passa(1)
    assert v.pedidos == 1
    a = v.alertas("suspeita")
    assert len(a) == 1 and "lamber/morder o penso" in a[0].texto and "Lambe a ligadura verde." in a[0].texto


def test_claude_diz_que_esta_tudo_bem_sem_alerta():
    v = Vigia(resposta=CALMO).passa(25)
    assert v.pedidos == 1 and v.alertas() == []
    assert any(isinstance(d, Diario) and d.tipo == "verificado" for d in v.saidas)


def test_sem_movimento_ou_fora_da_cama_nao_ha_suspeita():
    assert Vigia().passa(60, mov=0.0).pedidos == 0
    assert Vigia().passa(60, sitio="quarto").pedidos == 0
    assert Vigia().passa(60, pausa=True).pedidos == 0


def test_de_pe_na_cama_conta_como_suspeita():
    v = Vigia().passa(6, mov=0.0, sitio="descanso", visivel=True, parado=False)
    assert v.pedidos == 1


def test_no_maximo_uma_chamada_a_cada_30_s():
    v = Vigia(resposta=CALMO).passa(70)
    assert v.pedidos == 3                                     # aos ~5 s, ~35 s e ~65 s


def test_nao_pede_outra_enquanto_a_anterior_nao_responde():
    v = Vigia(resposta=CALMO)
    v.atraso = 200                                            # ~80 s sem resposta
    v.passa(70)
    assert v.pedidos == 1


def test_na_duvida_avisa_se_falhar_e_a_suspeita_durar_20_s():
    v = Vigia(ok=False).passa(15 + 5)
    assert v.alertas() == []                                  # suspeita só tem ~15 s
    v.passa(6)
    a = v.alertas("suspeita_nao_confirmada")
    assert len(a) == 1 and "NÃO confirmada" in a[0].texto and "ConnectionError" in a[0].texto


def test_falha_mas_a_suspeita_acaba_antes_dos_20_s_nao_avisa():
    v = Vigia(ok=False).passa(15)
    v.passa(30, mov=0.0)
    assert v.alertas() == []


def test_limite_diario_avisa_uma_vez_e_passa_a_regras_locais():
    v = Vigia()
    v.chamadas_hoje = 300
    v.passa(30)
    assert v.pedidos == 0
    assert [a.tipo for a in v.alertas()] == ["limite_claude", "suspeita_nao_confirmada"]
    v.passa(2, mov=0.0); v.passa(400)                         # nova suspeita mais tarde
    assert len(v.alertas("limite_claude")) == 1


def test_alerta_de_suspeita_nao_se_repete_em_5_min():
    v = Vigia().passa(100)
    assert v.pedidos == 4 and len(v.alertas("suspeita")) == 1


def test_pausa_cala_a_suspeita():
    v = Vigia()
    v.r.pausar(30, v.t)
    v.passa(60)
    assert v.alertas() == [] and any(isinstance(d, Diario) and d.tipo == "alerta_em_pausa" for d in v.saidas)


# ---------------------------------------------------------------- IMAGEM
def _img(cor_caixa=0):
    a = np.full((720, 1280, 3), 120, np.uint8)
    a[300:400, 500:700] = cor_caixa
    return Image.fromarray(a)


def test_movimento_so_dentro_da_caixa():
    antes, depois = _img(0), _img(255)
    assert visao.movimento(antes, depois, [500, 300, 700, 400]) > 0.5
    assert visao.movimento(antes, depois, [0, 0, 200, 200]) == 0.0


def test_recorte_com_margem_e_ampliado_para_512():
    jpg = visao.recorte(_img(), [500, 300, 700, 400], margem=0.3, lado_minimo=512)
    r = Image.open(io.BytesIO(jpg))
    assert min(r.size) >= 512 and r.width / r.height == pytest.approx(320 / 160, rel=0.02)  # 200x100 + 30% de cada lado


# ---------------------------------------------------------------- BOTÕES E SQLITE
def test_botoes_certo_errado_guardados_com_os_recortes():
    db = DB(":memory:")
    aid = db.alerta(Alerta("suspeita", "x"), 1.0)
    db.guardar_recortes(aid, [JPG, JPG, JPG])
    cmd = Comandos("123", Regras(LIMITES), db, lambda *a: True, lambda *a: True, LIMITES, threading.RLock())
    cb = lambda chat, dados: {"id": "c", "data": dados, "message": {"chat": {"id": chat}, "message_id": 9}}
    assert cmd.botao(cb(999, f"fb:{aid}:1"), 2.0) is None                     # outro chat: ignorado
    assert "Certo" in cmd.botao(cb(123, f"fb:{aid}:1"), 2.0)
    assert "Errado" in cmd.botao(cb(123, f"fb:{aid}:0"), 3.0)                 # pode mudar de ideias
    assert db.c.execute("SELECT feedback FROM alertas WHERE id=?", (aid,)).fetchone()[0] == 0
    assert db.c.execute("SELECT COUNT(*) FROM recortes WHERE alerta_id=?", (aid,)).fetchone()[0] == 3
    assert cmd.botao(cb(123, "fb:999:1"), 2.0) == "Esse alerta já não existe."


def test_chamadas_e_custo_contam_para_o_limite_diario():
    db = DB(":memory:")
    db.verificacao(Resultado(True, custo_usd=0.002), 100.0)
    db.verificacao(Resultado(False, erro="x"), 200.0)
    assert db.chamadas(150.0) == (1, 0.0) and db.chamadas(0.0) == (2, 0.002)
