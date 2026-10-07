"""Comandos do Telegram. Só o chat do Pedro (TELEGRAM_CHAT_ID) é atendido."""
from datetime import datetime
from zoneinfo import ZoneInfo

from .regras import duracao

TZ = ZoneInfo("Europe/Lisbon")
AJUDA = ("Comandos:\n/foto - imagem de agora\n/estado - onde está e últimos eventos\n"
         "/pausa [min] - pausa os alertas do cão (30 min se não disseres)\n/retomar - volta a avisar\n"
         "/diario - resumo das últimas 12 h")
NOMES = {"anda_demasiado": "anda demasiado", "varanda": "varanda", "desapareceu": "desapareceu",
         "sem_imagem": "câmara sem imagem", "imagem_voltou": "câmara voltou", "sem_corrente": "sem corrente",
         "corrente_voltou": "corrente voltou", "cpu_presa": "CPU presa", "suspeita": "suspeita (Claude)",
         "suspeita_nao_confirmada": "suspeita NÃO confirmada", "limite_claude": "limite do Claude"}
FEEDBACK = {1: " ✅", 0: " ❌", None: ""}


def inicio_do_dia(agora: float) -> float:
    d = datetime.fromtimestamp(agora, TZ)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def hora(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%H:%M")


class Comandos:
    def __init__(self, chat_id, regras, db, enviar_texto, enviar_foto, limites, lock):
        self.chat_id = str(chat_id) if chat_id else None
        self.r, self.db, self.l, self.lock = regras, db, limites, lock
        self.texto, self.foto = enviar_texto, enviar_foto

    def tratar(self, msg: dict, agora: float) -> bool:
        """Devolve False se a mensagem não vem do chat do Pedro (ignorada)."""
        if self.chat_id is None or str(msg.get("chat", {}).get("id")) != self.chat_id:
            return False
        partes = (msg.get("text") or "").strip().split()
        if not partes:
            return True
        cmd = partes[0].split("@")[0].lower()
        if cmd == "/foto":
            self.foto(f"Agora ({hora(agora)})")
        elif cmd == "/estado":
            self.texto(self.estado(agora))
        elif cmd == "/pausa":
            try:
                minutos = float(partes[1]) if len(partes) > 1 else self.l["pausa_padrao_min"]
            except ValueError:
                self.texto("Usa /pausa ou /pausa 45 (minutos).")
                return True
            minutos = max(1, min(minutos, 24 * 60))
            with self.lock:
                self.r.pausar(minutos, agora)
            self.texto(f"Alertas do cão em pausa até às {hora(agora + minutos * 60)}. "
                       "Os avisos de câmara e de servidor continuam. /retomar para voltar já.")
        elif cmd == "/retomar":
            with self.lock:
                self.r.retomar()
            self.texto("Alertas do cão retomados.")
        elif cmd == "/diario":
            self.texto(self.diario(agora))
        else:
            self.texto(AJUDA)
        return True

    def botao(self, cb: dict, agora: float) -> str | None:
        """Resposta a ✅ Certo / ❌ Errado de um alerta. None se não vier do chat do Pedro."""
        chat = (cb.get("message") or {}).get("chat", {}).get("id")
        if self.chat_id is None or str(chat) != self.chat_id:
            return None
        try:
            _, aid, valor = (cb.get("data") or "").split(":")
            aid, certo = int(aid), valor == "1"
        except ValueError:
            return "Botão desconhecido."
        tipo = self.db.feedback(aid, certo, agora)
        if tipo is None:
            return "Esse alerta já não existe."
        return f"Registado: {'✅ Certo' if certo else '❌ Errado'} ({NOMES.get(tipo, tipo)})"

    def estado(self, agora: float) -> str:
        with self.lock:
            r = self.r
            if r.visivel_certo():
                onde = f"Visto agora em {r.ultimo_sitio}" + (f" (desde as {hora(r.sitio_desde)}, há {duracao(agora - r.sitio_desde)})" if r.sitio_desde else "")
            elif r.ultimo_visto:
                onde = f"Não é visto há {duracao(agora - r.ultimo_visto)}. Último sítio: {r.ultimo_sitio} às {hora(r.ultimo_visto)}"
            else:
                onde = "Ainda não foi visto desde que o cérebro arrancou"
            if r.varanda_desde:
                onde += f"\nNa varanda desde as {hora(r.varanda_desde)}"
            camara = f"ok ({r.fps:.1f} imagens/s)" if r.camara_ok() else (
                f"SEM IMAGEM há {duracao(agora - r.sem_imagem_desde)}" if r.sem_imagem_desde else "a verificar")
            pausa = f"em pausa até às {hora(r.pausa_ate)}" if r.em_pausa(agora) else "ativos"
            mov = "sim" if r.movimento else "não"
        n, custo = self.db.chamadas(inicio_do_dia(agora))
        claude = f"Verificações com o Claude hoje: {n} de {self.l['claude_por_dia']} ({custo:.3f} USD)"
        linhas = [onde, f"Movimento na câmara: {mov}", f"Câmara: {camara}", f"Alertas do cão: {pausa}", claude, "", "Últimos eventos:"]
        linhas += [f"{hora(ts)} {t}" for ts, t in self.db.ultimos_eventos(5)] or ["(nenhum)"]
        return "\n".join(linhas)

    def diario(self, agora: float) -> str:
        horas = self.l["diario_horas"]
        x = self.db.resumo(agora - horas * 3600)
        linhas = [f"Diário das últimas {horas} h (desde as {hora(agora - horas * 3600)})", ""]
        sitios = ", ".join(f"{s} {n}" for s, n in x["por_sitio"])
        linhas.append(f"Deteções do cão: {x['deteccoes']}" + (f" ({sitios})" if sitios else ""))
        linhas.append(f"Idas à varanda: {len(x['varanda'])}")
        for saida, regresso, dur, alertado in x["varanda"]:
            linhas.append(f"  {hora(saida)} -> " + (f"{hora(regresso)} ({duracao(dur)})" if regresso else "ainda lá")
                          + (" [alerta]" if alertado else ""))
        linhas.append(f"Alertas: {len(x['alertas'])}")
        for ts, tipo, texto, enviado, fb in x["alertas"]:
            linhas.append(f"  {hora(ts)} {NOMES.get(tipo, tipo)}" + ("" if enviado else " (não enviado)") + FEEDBACK.get(fb, ""))
        n, ok, custo = x["verificacoes"]
        linhas.append(f"Verificações com o Claude: {n} ({n - ok} falhadas), {custo:.3f} USD")
        if x["camara"]:
            linhas.append("Câmara sem imagem:")
            linhas += [f"  {hora(ts)} {t}" for ts, t in x["camara"]]
        return "\n".join(linhas)
