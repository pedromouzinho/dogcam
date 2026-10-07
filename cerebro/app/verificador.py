"""Verificação de uma suspeita com o Claude (3 recortes do cão -> JSON com probabilidades)."""
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as TempoEsgotado
from dataclasses import dataclass, field

CAMPOS = ("lamber_ou_morder_penso", "sem_cone", "de_pe_ou_a_saltar")

SISTEMA = (
    "Estás a vigiar por câmara um cão em recuperação de uma fratura na pata da frente direita. "
    "Essa pata tem uma ligadura verde e o cão usa um cone (colar isabelino) para não lamber nem morder o penso. "
    "O cone é de plástico branco ou translúcido e está preso à coleira: de frente parece um disco ou prato branco "
    "à volta da cabeça, de lado parece um funil. Se vês esse disco ou funil à volta da cabeça, o cone ESTÁ posto. "
    "Recebes 3 recortes seguidos do cão (cerca de meio segundo entre cada um), tirados de uma câmara no quarto, "
    "quando ele está deitado ou na cama e se mexe. Dá, de 0 a 1, a probabilidade de: "
    "lamber_ou_morder_penso (boca ou língua junto à ligadura verde, ou a tentar chegar-lhe por baixo ou à volta do cone); "
    "sem_cone (o cone não está à volta do pescoço); "
    "de_pe_ou_a_saltar (de pé, a levantar-se, a saltar ou a apoiar a pata partida). "
    "Se a imagem não deixar ver bem, não inventes: usa valores intermédios e di-lo na descrição. "
    "Primeiro escreve a descricao (uma frase curta em português de Portugal sobre o que vês, incluindo onde está o cone), "
    "depois os valores."
)

ESQUEMA = {
    "type": "object",
    # descricao primeiro: o Claude descreve o que vê antes de dar os números
    "properties": {"descricao": {"type": "string"}, **{c: {"type": "number"} for c in CAMPOS}},
    "required": ["descricao", *CAMPOS],
    "additionalProperties": False,
}


@dataclass
class Resultado:
    ok: bool
    valores: dict = field(default_factory=dict)
    descricao: str = ""
    erro: str = ""
    duracao_s: float = 0.0
    tokens_entrada: int = 0
    tokens_saida: int = 0
    custo_usd: float = 0.0
    resposta: str = ""


def validar(texto: str) -> tuple[dict, str]:
    """Devolve (valores, descricao) ou levanta ValueError se não for o JSON pedido."""
    d = json.loads(texto)
    if not isinstance(d, dict):
        raise ValueError("não é um objeto")
    valores = {}
    for c in CAMPOS:
        v = d.get(c)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError(f"{c} fora de 0-1")
        valores[c] = float(v)
    if not isinstance(d.get("descricao"), str):
        raise ValueError("sem descricao")
    return valores, d["descricao"].strip()


class Verificador:
    def __init__(self, cliente, limites: dict):
        """cliente: anthropic.Anthropic (ou um falso nos testes); None se não houver chave."""
        self.cliente = cliente
        self.l = limites
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="claude")

    def _chamar(self, recortes: list[bytes]):
        conteudo = []
        for i, jpg in enumerate(recortes, 1):
            conteudo.append({"type": "text", "text": f"Recorte {i} de {len(recortes)}:"})
            conteudo.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                         "data": base64.standard_b64encode(jpg).decode()}})
        conteudo.append({"type": "text", "text": "Responde só com o JSON pedido."})
        return self.cliente.messages.create(
            model=self.l["claude_modelo"], max_tokens=400, system=SISTEMA,
            messages=[{"role": "user", "content": conteudo}],
            output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
        )

    def verificar(self, recortes: list[bytes]) -> Resultado:
        if self.cliente is None:
            return Resultado(False, erro="sem ANTHROPIC_API_KEY")
        t0 = time.monotonic()
        futuro = self.pool.submit(self._chamar, recortes)
        try:
            r = futuro.result(timeout=self.l["claude_tempo_max_s"])
        except TempoEsgotado:
            return Resultado(False, erro=f"tempo esgotado (mais de {self.l['claude_tempo_max_s']} s)",
                             duracao_s=time.monotonic() - t0)
        except Exception as e:
            return Resultado(False, erro=f"a chamada falhou ({type(e).__name__})", duracao_s=time.monotonic() - t0)
        dur = time.monotonic() - t0
        u = getattr(r, "usage", None)
        ent, sai = (getattr(u, "input_tokens", 0) or 0), (getattr(u, "output_tokens", 0) or 0)
        custo = ent / 1e6 * self.l["claude_preco_entrada_usd_mtok"] + sai / 1e6 * self.l["claude_preco_saida_usd_mtok"]
        base = dict(duracao_s=dur, tokens_entrada=ent, tokens_saida=sai, custo_usd=custo)
        texto = next((b.text for b in r.content if getattr(b, "type", None) == "text"), "")
        if r.stop_reason != "end_turn":
            return Resultado(False, erro=f"resposta incompleta ({r.stop_reason})", resposta=texto, **base)
        try:
            valores, descricao = validar(texto)
        except (ValueError, TypeError) as e:
            return Resultado(False, erro=f"resposta não é o JSON pedido ({e})", resposta=texto[:500], **base)
        return Resultado(True, valores, descricao, resposta=texto, **base)
