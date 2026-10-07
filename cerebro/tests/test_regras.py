"""Testes das regras com mensagens MQTT falsas do Frigate (sem câmara)."""
import json

import pytest

from app.regras import Alerta, Diario, Regras

LIMITES = {"anda_demasiado_s": 120, "varanda_s": 600, "desapareceu_s": 60, "sem_imagem_s": 30,
           "repetir_alerta_s": 300, "tolerancia_perda_s": 15, "cpu_minima_mhz": 1000,
           "pausa_padrao_min": 30, "diario_horas": 12}


class Cena:
    """Um Frigate falso: publica eventos e movimento como no MQTT real."""

    def __init__(self, **estado):
        self.r = Regras(LIMITES, "quarto_ipad", **estado)
        self.t = 1_000_000.0
        self.saidas = []
        self.r.camara_fps(3.0, self.t)

    def _guarda(self, acoes):
        self.saidas += acoes
        return acoes

    def mqtt(self, topico, payload):
        if not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
        return self._guarda(self.r.mqtt(topico, payload, self.t))

    def cao(self, tipo="update", zonas=("quarto",), parado=False, falso=False, oid="cao1", camara="quarto_ipad", label="dog"):
        after = {"id": oid, "camera": camara, "label": label, "current_zones": list(zonas),
                 "entered_zones": list(zonas), "stationary": parado, "false_positive": falso,
                 "start_time": self.t - 1, "end_time": self.t if tipo == "end" else None,
                 "top_score": 0.8, "score": 0.8, "active": not parado}
        return self.mqtt("frigate/events", {"type": tipo, "before": after, "after": after})

    def movimento(self, ligado):
        return self.mqtt("frigate/quarto_ipad/motion", b"ON" if ligado else b"OFF")

    def passa(self, segundos, passo=5, fps=3.0, cao=None):
        """Avança o relógio de `passo` em `passo` segundos, como o ciclo de 5 s do cérebro.
        cao=dict(...) repete uma atualização do cão a cada passo (cão a ser seguido)."""
        fim = self.t + segundos
        while self.t < fim:
            self.t = min(self.t + passo, fim)
            if cao is not None:
                self.cao(**cao)
            self._guarda(self.r.camara_fps(fps, self.t))
            self._guarda(self.r.tick(self.t))
        return self

    def alertas(self, tipo=None):
        return [a for a in self.saidas if isinstance(a, Alerta) and (tipo is None or a.tipo == tipo)]

    def diario(self, tipo):
        return [d for d in self.saidas if isinstance(d, Diario) and d.tipo == tipo]


# ---------------------------------------------------------------- ANDA DEMASIADO
def test_anda_demasiado_ao_fim_de_2_min_em_movimento_no_quarto():
    c = Cena()
    c.cao("new", ("quarto",))
    c.passa(115, cao=dict(zonas=("quarto",)))
    assert c.alertas() == []
    c.passa(10, cao=dict(zonas=("quarto",)))
    assert [a.tipo for a in c.alertas()] == ["anda_demasiado"]


def test_anda_demasiado_nao_conta_na_cama_nem_no_descanso():
    c = Cena()
    c.cao("new", ("cama",))
    c.passa(300, cao=dict(zonas=("cama",)))
    c.cao(zonas=("descanso", "quarto"))
    c.passa(300, cao=dict(zonas=("descanso", "quarto")))
    assert c.alertas() == []


def test_anda_demasiado_recomeca_se_parar():
    c = Cena()
    c.cao("new", ("quarto",))
    c.passa(100, cao=dict(zonas=("quarto",)))
    c.cao(zonas=("quarto",), parado=True)                    # parou (stationary)
    c.passa(10)
    c.passa(100, cao=dict(zonas=("quarto",)))                # volta a andar: só 100 s seguidos
    assert c.alertas() == []


def test_anda_demasiado_perda_breve_da_deteccao_nao_interrompe():
    c = Cena()
    c.cao("new", ("quarto",), oid="a")
    c.passa(70, cao=dict(zonas=("quarto",), oid="a"))
    c.cao("end", ("quarto",), oid="a")
    c.passa(10)                                               # 10 s sem deteção (< 15 s)
    c.cao("new", ("tigelas",), oid="b")
    c.passa(45, cao=dict(zonas=("tigelas",), oid="b"))
    assert [a.tipo for a in c.alertas()] == ["anda_demasiado"]


def test_anda_demasiado_perda_longa_recomeca():
    c = Cena()
    c.cao("new", ("quarto",), oid="a")
    c.passa(70, cao=dict(zonas=("quarto",), oid="a"))
    c.cao("end", ("quarto",), oid="a")
    c.movimento(True)                                         # movimento: evita o desapareceu
    c.passa(30)
    c.cao("new", ("quarto",), oid="b")
    c.passa(100, cao=dict(zonas=("quarto",), oid="b"))
    assert c.alertas("anda_demasiado") == []


# ---------------------------------------------------------------- VARANDA
def test_varanda_vai_para_o_diario_sem_alerta_e_volta():
    c = Cena()
    c.cao("new", ("quarto",)); c.cao(zonas=("porta_varanda",)); c.cao("end", ("porta_varanda",))
    assert len(c.diario("varanda_saida")) == 1
    c.passa(300)
    assert c.alertas() == []
    c.cao("new", ("porta_varanda",), oid="cao2")
    regresso = c.diario("varanda_regresso")
    assert len(regresso) == 1 and regresso[0].dados["duracao_s"] == pytest.approx(300)
    c.passa(600, cao=dict(zonas=("cama",), oid="cao2"))
    assert c.alertas() == []


def test_varanda_alerta_se_nao_voltar_em_10_min():
    c = Cena()
    c.cao("new", ("porta_varanda",)); c.cao("end", ("porta_varanda",))
    c.passa(595)
    assert c.alertas() == []
    c.passa(10)
    assert [a.tipo for a in c.alertas()] == ["varanda"]
    c.passa(1200)
    assert len(c.alertas("varanda")) == 1                     # um alerta por ida à varanda
    assert c.alertas("desapareceu") == []                     # na varanda não "desaparece"


# ---------------------------------------------------------------- DESAPARECEU
def test_desapareceu_sem_ser_visto_sem_movimento_fora_dos_sitios_de_descanso():
    c = Cena()
    c.cao("new", ("quarto",), parado=True); c.cao("end", ("quarto",))
    c.passa(55)
    assert c.alertas() == []
    c.passa(10)
    assert [a.tipo for a in c.alertas()] == ["desapareceu"]
    c.passa(1200)
    assert len(c.alertas("desapareceu")) == 1                 # só uma vez até voltar a ser visto


def test_desapareceu_nao_alerta_com_movimento_na_camara():
    c = Cena()
    c.cao("new", ("tigelas",), parado=True); c.cao("end", ("tigelas",))
    c.movimento(True)
    c.passa(300)
    c.movimento(False)
    c.passa(55)                                               # movimento acabou há menos de 60 s
    assert c.alertas() == []


@pytest.mark.parametrize("sitio", ["cama", "descanso", "porta_varanda"])
def test_desapareceu_nao_alerta_se_o_ultimo_sitio_for_de_descanso_ou_a_porta(sitio):
    c = Cena()
    c.cao("new", (sitio,), parado=True); c.cao("end", (sitio,))
    c.passa(400)
    assert c.alertas("desapareceu") == []


def test_cao_deitado_nao_detetado_com_movimento_na_cama_nao_da_alarme():
    """Deitado com o cone deixa de ser detetado; mexe-se um pouco na cama."""
    c = Cena()
    c.cao("new", ("cama",)); c.cao(zonas=("cama",), parado=True); c.cao("end", ("cama",))
    for _ in range(20):                                       # 20 min: movimento intermitente
        c.movimento(True); c.passa(20)
        c.movimento(False); c.passa(40)
    assert c.alertas() == []


def test_desapareceu_deteccao_de_baixa_confianca_conta_como_visto():
    c = Cena()
    c.cao("new", ("quarto",)); c.cao("end", ("quarto",))
    c.passa(30)
    c.cao("new", ("quarto",), falso=True, oid="fraco")        # o Frigate ainda não tem a certeza
    c.passa(300, cao=dict(zonas=("quarto",), falso=True, oid="fraco"))
    assert c.alertas() == []


def test_desapareceu_volta_a_armar_depois_de_ser_visto():
    c = Cena()
    c.cao("new", ("quarto",)); c.cao("end", ("quarto",)); c.passa(70)
    c.cao("new", ("quarto",), oid="b"); c.cao("end", ("quarto",), oid="b")
    c.passa(400)                                              # passa a janela de 5 min
    assert len(c.alertas("desapareceu")) == 2


# ---------------------------------------------------------------- CÂMARA SEM IMAGEM
def test_camara_sem_imagem_30_s_alerta_e_avisa_quando_volta():
    c = Cena()
    c.passa(25, fps=0.0)
    assert c.alertas() == []
    c.passa(10, fps=0.0)
    assert [a.tipo for a in c.alertas()] == ["sem_imagem"]
    c.passa(60, fps=0.0)
    assert len(c.alertas("sem_imagem")) == 1
    c.passa(5, fps=3.0)
    assert [a.tipo for a in c.alertas()] == ["sem_imagem", "imagem_voltou"]
    assert len(c.diario("camara")) == 1


def test_frigate_sem_resposta_conta_como_sem_imagem():
    c = Cena()
    c.passa(35, fps=None)
    assert [a.tipo for a in c.alertas()] == ["sem_imagem"]


def test_falha_curta_da_camara_nao_alerta_nem_diz_que_voltou():
    c = Cena()
    c.passa(20, fps=0.0); c.passa(5)
    assert c.alertas() == []


def test_sem_imagem_suspende_o_desapareceu():
    c = Cena()
    c.cao("new", ("quarto",)); c.cao("end", ("quarto",))      # a câmara cai: o Frigate termina a deteção
    c.passa(300, fps=0.0)
    c.passa(30)                                               # imagem volta; o relógio recomeça
    assert c.alertas("desapareceu") == []


# ---------------------------------------------------------------- SERVIDOR
def test_servidor_so_com_bateria_e_corrente_a_voltar():
    c = Cena()
    assert [a.tipo for a in c._guarda(c.r.servidor(False, 40, 2300, c.t))] == ["sem_corrente"]
    c.t += 60
    assert c.r.servidor(False, 39, 2300, c.t) == []           # não repete antes de 5 min
    c.t += 60
    assert [a.tipo for a in c.r.servidor(True, 41, 2300, c.t)] == ["corrente_voltou"]
    assert c.r.servidor(True, 42, 2300, c.t + 60) == []


def test_servidor_cpu_presa_abaixo_de_1_ghz():
    c = Cena()
    assert [a.tipo for a in c.r.servidor(True, 80, 400, c.t)] == ["cpu_presa"]
    assert c.r.servidor(True, 80, 2300, c.t + 60) == []


# ---------------------------------------------------------------- REPETIÇÃO E PAUSA
def test_mesmo_alerta_nao_se_repete_durante_5_min():
    c = Cena()
    for _ in range(3):                                        # 3 desaparecimentos em 3 min
        c.cao("new", ("quarto",), oid=str(c.t)); c.cao("end", ("quarto",), oid=str(c.t)); c.passa(65)
    assert len(c.alertas("desapareceu")) == 1


def test_pausa_cala_os_alertas_do_cao_mas_nao_os_da_camara():
    c = Cena()
    c.r.pausar(30, c.t)
    c.cao("new", ("quarto",)); c.cao("end", ("quarto",)); c.passa(70)
    assert c.alertas() == [] and len(c.diario("alerta_em_pausa")) == 1
    c.passa(35, fps=0.0)
    assert [a.tipo for a in c.alertas()] == ["sem_imagem"]


def test_retomar_volta_a_avisar():
    c = Cena()
    c.r.pausar(30, c.t); c.r.retomar()
    c.cao("new", ("quarto",)); c.cao("end", ("quarto",)); c.passa(70)
    assert [a.tipo for a in c.alertas()] == ["desapareceu"]


# ---------------------------------------------------------------- OUTROS
def test_ignora_outras_camaras_e_outros_objetos():
    c = Cena()
    c.cao("new", ("quarto",), camara="varanda")
    c.cao("new", ("quarto",), label="person", oid="p")
    assert not c.r.visivel() and c.r.ultimo_sitio is None


def test_sitio_mais_especifico_quando_zonas_se_sobrepoem():
    c = Cena()
    c.cao("new", ("quarto", "descanso"))
    assert c.r.ultimo_sitio == "descanso"


def test_estado_restaurado_da_base_de_dados_na_cama_nao_da_alarme():
    c = Cena(ultimo_sitio="cama", ultimo_visto=1_000_000.0 - 600)
    c.passa(300)
    assert c.alertas() == []


def test_sem_nunca_ver_o_cao_nao_ha_desapareceu():
    c = Cena()
    c.passa(600)
    assert c.alertas() == []
