"""SQLite e comandos do Telegram (sem rede)."""
import threading

from app.comandos import Comandos
from app.db import DB
from app.regras import Alerta, Diario, Regras
from tests.test_regras import LIMITES

T = 1_000_000.0


def montar(chat="123"):
    db = DB(":memory:")
    r = Regras(LIMITES, "quarto_ipad")
    textos, fotos = [], []
    cmd = Comandos(chat, r, db, lambda t: textos.append(t) or True, lambda l: fotos.append(l) or True,
                   LIMITES, threading.RLock())
    return db, r, cmd, textos, fotos


def msg(texto, chat=123):
    return {"chat": {"id": chat, "first_name": "Pedro"}, "text": texto}


def test_varanda_e_alertas_ficam_no_sqlite_e_o_estado_sobrevive_a_um_reinicio():
    db, *_ = montar()
    db.diario(Diario("sitio", "Visto em porta_varanda", "porta_varanda"), T)
    db.diario(Diario("varanda_saida", "Foi para a varanda", "porta_varanda"), T + 10)
    db.alerta(Alerta("varanda", "Está na varanda há 10 min"), T + 610, True)
    assert db.estado_inicial() == {"ultimo_sitio": "porta_varanda", "ultimo_visto": T, "varanda_desde": T + 10}
    db.diario(Diario("varanda_regresso", "Voltou", "porta_varanda", {"duracao_s": 700}), T + 710)
    assert db.resumo(T - 1)["varanda"] == [(T + 10, T + 710, 700, 1)]
    assert db.estado_inicial()["varanda_desde"] is None


def test_deteccoes_do_frigate_ficam_guardadas():
    db, *_ = montar()
    ev = {"type": "new", "after": {"id": "x", "label": "dog", "start_time": T, "end_time": None,
                                   "entered_zones": ["cama"], "top_score": 0.8}}
    db.deteccao(ev, "cama")
    ev["after"].update(end_time=T + 30, top_score=0.9)
    db.deteccao(ev, "cama")
    assert db.resumo(T - 1)["deteccoes"] == 1 and db.resumo(T - 1)["por_sitio"] == [("cama", 1)]


def test_comandos_de_outro_chat_sao_ignorados():
    _, r, cmd, textos, fotos = montar()
    assert cmd.tratar(msg("/pausa 60", chat=999), T) is False
    assert cmd.tratar(msg("/foto", chat=999), T) is False
    assert textos == [] and fotos == [] and not r.em_pausa(T)


def test_sem_chat_id_definido_nada_e_atendido():
    _, _, cmd, textos, _ = montar(chat=None)
    assert cmd.tratar(msg("/estado"), T) is False and textos == []


def test_pausa_e_retomar():
    _, r, cmd, textos, _ = montar()
    cmd.tratar(msg("/pausa 15"), T)
    assert r.em_pausa(T + 14 * 60) and not r.em_pausa(T + 16 * 60)
    cmd.tratar(msg("/retomar"), T)
    assert not r.em_pausa(T + 60)
    cmd.tratar(msg("/pausa"), T)                              # sem número: 30 min
    assert r.em_pausa(T + 29 * 60) and not r.em_pausa(T + 31 * 60)
    cmd.tratar(msg("/pausa abc"), T)
    assert "Usa /pausa" in textos[-1]


def test_foto_estado_e_diario():
    db, r, cmd, textos, fotos = montar()
    r.camara_fps(3.0, T)
    for d in r.evento({"type": "new", "after": {"id": "a", "camera": "quarto_ipad", "label": "dog",
                                                 "current_zones": ["cama"], "stationary": True}}, T):
        db.diario(d, T)
    db.alerta(Alerta("desapareceu", "Desapareceu"), T + 100, True)
    cmd.tratar(msg("/foto@dogcam_pedro_bot"), T + 200)
    assert len(fotos) == 1
    cmd.tratar(msg("/estado"), T + 200)
    assert "Visto agora em cama" in textos[-1] and "Câmara: ok" in textos[-1] and "Visto em cama" in textos[-1]
    cmd.tratar(msg("/diario"), T + 200)
    assert "Alertas: 1" in textos[-1] and "desapareceu" in textos[-1]
    cmd.tratar(msg("olá"), T + 200)
    assert "/foto" in textos[-1]                              # ajuda
