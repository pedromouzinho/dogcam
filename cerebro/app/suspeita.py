"""SUSPEITA de lamber/morder o penso na cama ou no descanso, confirmada pelo Claude.

Recebe uma leitura de ~0,4 em ~0,4 s (movimento dentro da caixa do cão) e devolve o
que fazer: PedirVerificacao (o cérebro manda 3 recortes ao Claude), Alerta ou Diario.
Não fala com a rede: os testes usam um Claude falso.
"""
from dataclasses import dataclass

from .regras import Alerta, Diario, duracao
from .verificador import Resultado

SITIOS = ("cama", "descanso")
NOMES = {"lamber_ou_morder_penso": "lamber/morder o penso", "sem_cone": "sem cone",
         "de_pe_ou_a_saltar": "de pé/a saltar"}


@dataclass
class PedirVerificacao:
    pass


class Suspeita:
    def __init__(self, limites: dict, alerta):
        """alerta: Regras._alerta (respeita a pausa e não repete o mesmo alerta em 5 min)."""
        self.l = limites
        self.alerta = alerta
        self.mexe_desde = None
        self.ultimo_mexer = None
        self.desde = None              # suspeita ativa desde
        self.ultima_chamada = None
        self.em_curso = False
        self.motivo_duvida = None      # porque não está confirmada (erro, tempo, JSON, limite)
        self.duvida_enviada = False
        self.limite_avisado_dia = None

    def _acabou(self):
        self.mexe_desde = self.ultimo_mexer = self.desde = None
        self.motivo_duvida = None
        self.duvida_enviada = False

    def amostra(self, agora, sitio, visivel, parado, mov_caixa, chamadas_hoje, dia, em_pausa):
        out = []
        if sitio not in SITIOS or em_pausa:
            self._acabou()
            return out
        em_pe = visivel and not parado
        if mov_caixa >= self.l["movimento_caixa_min"] or em_pe:
            if self.mexe_desde is None:
                self.mexe_desde = agora
            self.ultimo_mexer = agora
        elif self.ultimo_mexer is None or agora - self.ultimo_mexer > self.l["movimento_pausa_s"]:
            self._acabou()
            return out
        if agora - self.mexe_desde < self.l["suspeita_s"]:
            return out

        if self.desde is None:
            self.desde = agora
            como = "de pé" if em_pe else "a mexer-se deitado"
            out.append(Diario("suspeita", f"Suspeita: {como} na {sitio}", sitio))

        if chamadas_hoje >= self.l["claude_por_dia"]:
            if self.limite_avisado_dia != dia:
                self.limite_avisado_dia = dia
                out.append(Alerta("limite_claude", f"Cheguei ao limite de {self.l['claude_por_dia']} verificações "
                                  "com o Claude hoje. Até amanhã só uso as regras locais: as suspeitas na cama "
                                  "chegam como \"NÃO confirmadas\".", foto=False))
            self.motivo_duvida = "limite diário de verificações atingido"
        elif not self.em_curso and (self.ultima_chamada is None
                                    or agora - self.ultima_chamada >= self.l["claude_intervalo_s"]):
            self.em_curso = True
            self.ultima_chamada = agora
            out.append(PedirVerificacao())

        # NA DÚVIDA, AVISA
        if self.motivo_duvida and not self.duvida_enviada and agora - self.desde >= self.l["duvida_s"]:
            r = self.alerta("suspeita_nao_confirmada",
                            f"Suspeita NÃO confirmada: mexe-se na {sitio} há {duracao(agora - self.desde)} "
                            f"e não consegui confirmar com o Claude ({self.motivo_duvida}). Vê a imagem.", agora)
            self.duvida_enviada = bool(r)
            out += r
        return out

    def resultado(self, res: Resultado, agora, sitio):
        self.em_curso = False
        if not res.ok:
            self.motivo_duvida = res.erro
            return [Diario("verificacao_falhou", f"Verificação com o Claude falhou: {res.erro}", sitio)]
        self.motivo_duvida = None
        altos = {k: v for k, v in res.valores.items() if v >= self.l["claude_limiar"]}
        valores = ", ".join(f"{NOMES[k]} {v:.2f}".replace(".", ",") for k, v in res.valores.items())
        if not altos:
            return [Diario("verificado", f"Claude: sem problema ({valores}). {res.descricao}", sitio)]
        titulo = " e ".join(NOMES[k] for k in altos)
        return self.alerta("suspeita", f"Suspeita confirmada pelo Claude: {titulo}.\n{res.descricao}\n({valores})", agora)
