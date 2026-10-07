"""Regras do cérebro DogCam para a câmara quarto_ipad.

Este módulo não fala com a rede nem com o disco: recebe as mensagens MQTT do
Frigate, o estado da câmara e o do servidor, e devolve o que fazer (Alerta para o
Telegram, Diario para o SQLite). Por isso os testes alimentam-no com mensagens
MQTT falsas e um relógio controlado.
"""
import json
from dataclasses import dataclass, field

SITIOS_DE_DESCANSO = ("cama", "descanso")
# Ele deitado com o cone às vezes não é detetado; à porta pode estar na varanda.
SEM_ALARME_DE_DESAPARECEU = ("cama", "descanso", "porta_varanda")
# descanso sobrepõe-se a quarto: fica o sítio mais específico.
PRIORIDADE_DOS_SITIOS = ("cama", "descanso", "porta_varanda", "tigelas", "quarto")
ALERTAS_DO_CAO = ("anda_demasiado", "varanda", "desapareceu", "suspeita", "suspeita_nao_confirmada", "teste_cao")


@dataclass
class Alerta:
    tipo: str
    texto: str
    foto: bool = True
    imagens: list | None = None   # recortes (JPEG) a enviar e guardar em vez da imagem da câmara


@dataclass
class Diario:
    tipo: str
    texto: str
    zona: str | None = None
    dados: dict = field(default_factory=dict)


def duracao(segundos: float) -> str:
    s = int(round(segundos))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min" + (f" {s} s" if s else "")
    h, m = divmod(m, 60)
    return f"{h} h {m} min"


def sitio_de(zonas) -> str:
    for z in PRIORIDADE_DOS_SITIOS:
        if z in zonas:
            return z
    return sorted(zonas)[0] if zonas else "fora das zonas"


class Regras:
    def __init__(self, limites: dict, camara: str = "quarto_ipad",
                 ultimo_sitio: str | None = None, ultimo_visto: float | None = None,
                 varanda_desde: float | None = None):
        self.l = limites
        self.camara = camara
        self.ativos: dict[str, dict] = {}       # deteções de dog em curso no Frigate
        self.caixa = None                        # última caixa do cão [x1, y1, x2, y2] (fica depois de deixar de ser visto)
        self.parado = True
        self.ultimo_sitio = ultimo_sitio
        self.sitio_desde = ultimo_visto
        self.ultimo_visto = ultimo_visto
        self.anda_desde = None                   # em movimento fora de cama/descanso desde
        self.perdido_em = None
        self.varanda_desde = varanda_desde
        self.varanda_alertado = False
        self.desapareceu_alertado = False
        self.movimento = False
        self.movimento_em = None
        self.fps = None
        self.sem_imagem_desde = None
        self.sem_imagem_alertado = False
        self.sem_imagem_longa_alertado = False
        self.sem_corrente = False
        self.enviados: dict[str, float] = {}
        self.pausa_ate = None

    # -- estado ---------------------------------------------------------------
    def visivel(self) -> bool:
        """Há alguma deteção de dog em curso (mesmo de confiança baixa)."""
        return bool(self.ativos)

    def visivel_certo(self) -> bool:
        return any(not o["falso"] for o in self.ativos.values())

    def em_pausa(self, agora) -> bool:
        return self.pausa_ate is not None and agora < self.pausa_ate

    def pausar(self, minutos: float, agora):
        self.pausa_ate = agora + minutos * 60

    def retomar(self):
        self.pausa_ate = None

    def _alerta(self, tipo, texto, agora, foto=True):
        if tipo in ALERTAS_DO_CAO and self.em_pausa(agora):
            return [Diario("alerta_em_pausa", texto, self.ultimo_sitio)]
        ultimo = self.enviados.get(tipo)
        if ultimo is not None and agora - ultimo < self.l["repetir_alerta_s"]:
            return []
        self.enviados[tipo] = agora
        return [Alerta(tipo, texto, foto)]

    # -- entradas -------------------------------------------------------------
    def mqtt(self, topico: str, payload: bytes, agora):
        if topico == "frigate/events":
            return self.evento(json.loads(payload), agora)
        if topico == f"frigate/{self.camara}/motion":
            return self.movimento_msg(payload.decode().strip() == "ON", agora)
        return []

    def evento(self, msg: dict, agora):
        a = msg.get("after") or {}
        if a.get("camera") != self.camara or a.get("label") != "dog":
            return []
        out = []
        oid = a["id"]
        if msg.get("type") == "end" or a.get("end_time"):
            self.ativos.pop(oid, None)
            if not self.ativos:
                self.ultimo_visto = agora
                self.perdido_em = agora
                if self.ultimo_sitio == "porta_varanda" and self.varanda_desde is None:
                    self.varanda_desde = agora
                    self.varanda_alertado = False
                    out.append(Diario("varanda_saida", "Foi para a varanda (deixou de ser visto à porta)", "porta_varanda"))
            return out

        falso = bool(a.get("false_positive"))
        self.ativos[oid] = {"falso": falso}
        self.ultimo_visto = agora
        self.perdido_em = None
        self.desapareceu_alertado = False
        if falso:  # conta como "visto" (evita o alarme de desapareceu), mais nada
            return out

        if self.varanda_desde is not None:
            tempo = agora - self.varanda_desde
            out.append(Diario("varanda_regresso", f"Voltou da varanda ao fim de {duracao(tempo)}",
                              "porta_varanda", {"duracao_s": tempo}))
            self.varanda_desde = None
            self.varanda_alertado = False

        if a.get("box"):
            self.caixa = list(a["box"])
        self.parado = bool(a.get("stationary"))
        zonas = set(a.get("current_zones") or [])
        # O Frigate manda as primeiras atualizações de cada deteção sem zonas (ainda não as
        # confirmou): uma lista vazia não apaga o último sítio conhecido.
        sitio = sitio_de(zonas) if zonas else (self.ultimo_sitio or "fora das zonas")
        if zonas and sitio != self.ultimo_sitio:
            self.ultimo_sitio = sitio
            self.sitio_desde = agora
            out.append(Diario("sitio", f"Visto em {sitio}", sitio))

        if not a.get("stationary") and sitio not in SITIOS_DE_DESCANSO:
            if self.anda_desde is None:
                self.anda_desde = agora
        else:
            self.anda_desde = None
        return out

    def movimento_msg(self, ligado: bool, agora):
        self.movimento = ligado
        self.movimento_em = agora
        return []

    def camara_fps(self, fps: float | None, agora):
        """fps da câmara lido da API do Frigate (None se o Frigate não responde)."""
        self.fps = fps
        if fps is None or fps < 0.5:
            if self.sem_imagem_desde is None:
                self.sem_imagem_desde = agora
            return []
        out = []
        if self.sem_imagem_desde is not None:
            tempo = agora - self.sem_imagem_desde
            if self.sem_imagem_alertado:
                out.append(Alerta("imagem_voltou", f"A câmara voltou a ter imagem (esteve sem imagem {duracao(tempo)}).", True))
            out.append(Diario("camara", f"Câmara sem imagem durante {duracao(tempo)}"))
            self.sem_imagem_desde = None
            self.sem_imagem_alertado = False
            self.sem_imagem_longa_alertado = False
            # Sem imagem não se pode ter visto o cão: o relógio de desapareceu recomeça.
            if self.ultimo_visto is not None:
                self.ultimo_visto = agora
        return out

    def servidor(self, adaptador: bool, bateria: int | None, mhz: int | None, agora):
        out = []
        if not adaptador:
            r = self._alerta("sem_corrente", f"Servidor sem corrente: está só com a bateria ({bateria}%).", agora, foto=False)
            if any(isinstance(x, Alerta) for x in r):
                self.sem_corrente = True
            out += r
        elif self.sem_corrente:
            self.sem_corrente = False
            out.append(Alerta("corrente_voltou", f"A corrente voltou (bateria a {bateria}%).", False))
        if mhz is not None and mhz < self.l["cpu_minima_mhz"]:
            out += self._alerta("cpu_presa", f"CPU presa a {mhz} MHz (travão PROCHOT?). Ver o carregador e fazer reset ao SMC.", agora, foto=False)
        return out

    # -- relógio --------------------------------------------------------------
    def tick(self, agora):
        out = []
        if self.sem_imagem_desde is not None:
            tempo = agora - self.sem_imagem_desde
            if not self.sem_imagem_alertado and tempo >= self.l["sem_imagem_s"]:
                r = self._alerta("sem_imagem", f"Câmara sem imagem há {duracao(tempo)}.", agora, foto=False)
                if any(isinstance(x, Alerta) for x in r):
                    self.sem_imagem_alertado = True
                out += r
            # Depois de uma falha longa a OctoStream deixa de tentar (testes de 2026-10-07).
            if not self.sem_imagem_longa_alertado and tempo >= self.l["sem_imagem_longa_s"]:
                r = self._alerta("sem_imagem_longa", f"Câmara sem imagem há {duracao(tempo)}: abre e fecha a OctoStream "
                                 "no iPad (desliza para cima e fecha a app, volta a abri-la e carrega em Go Live).", agora, foto=False)
                if any(isinstance(x, Alerta) for x in r):
                    self.sem_imagem_longa_alertado = True
                out += r
            return out  # sem imagem, as regras do cão ficam suspensas

        # ANDA DEMASIADO (uma perda breve da deteção não interrompe a contagem)
        if self.anda_desde is not None and not self.visivel_certo() and self.perdido_em is not None \
                and agora - self.perdido_em > self.l["tolerancia_perda_s"]:
            self.anda_desde = None
        if self.anda_desde is not None and self.visivel_certo() \
                and agora - self.anda_desde >= self.l["anda_demasiado_s"]:
            out += self._alerta("anda_demasiado",
                                f"Anda demasiado: em movimento fora da cama há {duracao(agora - self.anda_desde)} (agora em {self.ultimo_sitio}).",
                                agora)

        # VARANDA
        if self.varanda_desde is not None and not self.varanda_alertado and not self.visivel_certo() \
                and agora - self.varanda_desde >= self.l["varanda_s"]:
            r = self._alerta("varanda", f"Está na varanda há {duracao(agora - self.varanda_desde)} e ainda não voltou.", agora)
            self.varanda_alertado = bool(r)  # se a janela de repetição o calou, tenta no próximo ciclo
            out += r

        # DESAPARECEU
        sem_movimento = not self.movimento and (self.movimento_em is None
                                                or agora - self.movimento_em >= self.l["desapareceu_s"])
        if (not self.visivel() and not self.desapareceu_alertado and self.ultimo_visto is not None
                and agora - self.ultimo_visto >= self.l["desapareceu_s"] and sem_movimento
                and self.ultimo_sitio is not None and self.ultimo_sitio not in SEM_ALARME_DE_DESAPARECEU
                and self.varanda_desde is None):
            r = self._alerta("desapareceu",
                                f"Desapareceu: não é visto há {duracao(agora - self.ultimo_visto)}, sem movimento na câmara. Último sítio: {self.ultimo_sitio}.",
                                agora)
            self.desapareceu_alertado = bool(r)
            out += r
        return out

    def camara_ok(self) -> bool:
        return self.fps is not None and self.fps >= 0.5 and self.sem_imagem_desde is None
