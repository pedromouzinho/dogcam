"""SQLite do cérebro: deteções do Frigate, diário, alertas e idas à varanda."""
import json
import sqlite3
import threading

from .regras import Alerta, Diario

ESQUEMA = """
CREATE TABLE IF NOT EXISTS deteccoes (
    id TEXT PRIMARY KEY, inicio REAL, fim REAL, ultimo_sitio TEXT, zonas TEXT, score REAL
);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY, ts REAL, tipo TEXT, zona TEXT, texto TEXT, dados TEXT
);
CREATE TABLE IF NOT EXISTS alertas (
    id INTEGER PRIMARY KEY, ts REAL, tipo TEXT, texto TEXT, enviado INTEGER
);
CREATE TABLE IF NOT EXISTS varanda (
    id INTEGER PRIMARY KEY, saida REAL, regresso REAL, duracao_s REAL, alertado INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS eventos_ts ON eventos(ts);
CREATE INDEX IF NOT EXISTS alertas_ts ON alertas(ts);
"""


class DB:
    def __init__(self, caminho: str):
        self.c = sqlite3.connect(caminho, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.c:
            self.c.executescript(ESQUEMA)

    def deteccao(self, msg: dict, sitio: str | None):
        a = msg.get("after") or {}
        if a.get("label") != "dog":
            return
        with self.lock, self.c:
            self.c.execute(
                "INSERT INTO deteccoes(id, inicio, fim, ultimo_sitio, zonas, score) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET fim=excluded.fim, ultimo_sitio=COALESCE(excluded.ultimo_sitio, ultimo_sitio), "
                "zonas=excluded.zonas, score=MAX(score, excluded.score)",
                (a["id"], a.get("start_time"), a.get("end_time"), sitio,
                 json.dumps(a.get("entered_zones") or []), a.get("top_score") or a.get("score") or 0))

    def diario(self, d: Diario, ts: float):
        with self.lock, self.c:
            self.c.execute("INSERT INTO eventos(ts, tipo, zona, texto, dados) VALUES (?,?,?,?,?)",
                           (ts, d.tipo, d.zona, d.texto, json.dumps(d.dados)))
            if d.tipo == "varanda_saida":
                self.c.execute("INSERT INTO varanda(saida) VALUES (?)", (ts,))
            elif d.tipo == "varanda_regresso":
                self.c.execute("UPDATE varanda SET regresso=?, duracao_s=? WHERE id=(SELECT MAX(id) FROM varanda WHERE regresso IS NULL)",
                               (ts, d.dados.get("duracao_s")))

    def alerta(self, a: Alerta, ts: float, enviado: bool):
        with self.lock, self.c:
            self.c.execute("INSERT INTO alertas(ts, tipo, texto, enviado) VALUES (?,?,?,?)", (ts, a.tipo, a.texto, int(enviado)))
            if a.tipo == "varanda":
                self.c.execute("UPDATE varanda SET alertado=1 WHERE id=(SELECT MAX(id) FROM varanda WHERE regresso IS NULL)")

    def estado_inicial(self) -> dict:
        """Último sítio visto e ida à varanda em curso, para sobreviver a reinícios."""
        with self.lock:
            r = self.c.execute("SELECT zona, ts FROM eventos WHERE tipo='sitio' ORDER BY ts DESC LIMIT 1").fetchone()
            v = self.c.execute("SELECT saida FROM varanda WHERE regresso IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        return {"ultimo_sitio": r[0] if r else None, "ultimo_visto": r[1] if r else None,
                "varanda_desde": v[0] if v else None}

    def ultimos_eventos(self, n=5):
        with self.lock:
            return self.c.execute("SELECT ts, texto FROM eventos ORDER BY ts DESC LIMIT ?", (n,)).fetchall()

    def resumo(self, desde: float) -> dict:
        with self.lock:
            q = self.c.execute
            return {
                "deteccoes": q("SELECT COUNT(*) FROM deteccoes WHERE inicio >= ?", (desde,)).fetchone()[0],
                "por_sitio": q("SELECT COALESCE(ultimo_sitio, '?'), COUNT(*) FROM deteccoes WHERE inicio >= ? "
                               "GROUP BY 1 ORDER BY 2 DESC", (desde,)).fetchall(),
                "varanda": q("SELECT saida, regresso, duracao_s, alertado FROM varanda WHERE saida >= ? ORDER BY saida", (desde,)).fetchall(),
                "alertas": q("SELECT ts, tipo, texto, enviado FROM alertas WHERE ts >= ? ORDER BY ts", (desde,)).fetchall(),
                "camara": q("SELECT ts, texto FROM eventos WHERE tipo='camara' AND ts >= ? ORDER BY ts", (desde,)).fetchall(),
            }
