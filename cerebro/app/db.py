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
CREATE TABLE IF NOT EXISTS recortes (
    alerta_id INTEGER, n INTEGER, jpg BLOB, PRIMARY KEY (alerta_id, n)
);
CREATE TABLE IF NOT EXISTS verificacoes (
    id INTEGER PRIMARY KEY, ts REAL, ok INTEGER, erro TEXT, duracao_s REAL,
    tokens_entrada INTEGER, tokens_saida INTEGER, custo_usd REAL, resposta TEXT
);
CREATE INDEX IF NOT EXISTS eventos_ts ON eventos(ts);
CREATE INDEX IF NOT EXISTS verificacoes_ts ON verificacoes(ts);
CREATE INDEX IF NOT EXISTS alertas_ts ON alertas(ts);
"""


class DB:
    def __init__(self, caminho: str):
        self.c = sqlite3.connect(caminho, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.c:
            self.c.executescript(ESQUEMA)
            colunas = {r[1] for r in self.c.execute("PRAGMA table_info(alertas)")}
            if "feedback" not in colunas:  # base criada na fase 2A
                self.c.execute("ALTER TABLE alertas ADD COLUMN feedback INTEGER")
                self.c.execute("ALTER TABLE alertas ADD COLUMN feedback_ts REAL")

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

    def alerta(self, a: Alerta, ts: float, enviado: bool = False, imagens: list | None = None) -> int:
        with self.lock, self.c:
            aid = self.c.execute("INSERT INTO alertas(ts, tipo, texto, enviado) VALUES (?,?,?,?)",
                                 (ts, a.tipo, a.texto, int(enviado))).lastrowid
            for n, jpg in enumerate(imagens or []):
                self.c.execute("INSERT INTO recortes(alerta_id, n, jpg) VALUES (?,?,?)", (aid, n, jpg))
            if a.tipo == "varanda":
                self.c.execute("UPDATE varanda SET alertado=1 WHERE id=(SELECT MAX(id) FROM varanda WHERE regresso IS NULL)")
        return aid

    def guardar_recortes(self, aid: int, imagens: list):
        with self.lock, self.c:
            for n, jpg in enumerate(imagens or []):
                self.c.execute("INSERT OR REPLACE INTO recortes(alerta_id, n, jpg) VALUES (?,?,?)", (aid, n, jpg))

    def marcar_enviado(self, aid: int, enviado: bool):
        with self.lock, self.c:
            self.c.execute("UPDATE alertas SET enviado=? WHERE id=?", (int(enviado), aid))

    def feedback(self, aid: int, certo: bool, ts: float) -> str | None:
        """Guarda ✅/❌ de um alerta; devolve o tipo do alerta, ou None se não existir."""
        with self.lock, self.c:
            r = self.c.execute("SELECT tipo FROM alertas WHERE id=?", (aid,)).fetchone()
            if r:
                self.c.execute("UPDATE alertas SET feedback=?, feedback_ts=? WHERE id=?", (int(certo), ts, aid))
        return r[0] if r else None

    def verificacao(self, res, ts: float):
        with self.lock, self.c:
            self.c.execute("INSERT INTO verificacoes(ts, ok, erro, duracao_s, tokens_entrada, tokens_saida, custo_usd, resposta) "
                           "VALUES (?,?,?,?,?,?,?,?)", (ts, int(res.ok), res.erro, res.duracao_s, res.tokens_entrada,
                                                        res.tokens_saida, res.custo_usd, res.resposta))

    def chamadas(self, desde: float) -> tuple[int, float]:
        """Chamadas ao Claude e custo (USD) desde `desde`."""
        with self.lock:
            n, c = self.c.execute("SELECT COUNT(*), COALESCE(SUM(custo_usd), 0) FROM verificacoes WHERE ts >= ?", (desde,)).fetchone()
        return n, c

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
                "alertas": q("SELECT ts, tipo, texto, enviado, feedback FROM alertas WHERE ts >= ? ORDER BY ts", (desde,)).fetchall(),
                "verificacoes": q("SELECT COUNT(*), COALESCE(SUM(ok), 0), COALESCE(SUM(custo_usd), 0) FROM verificacoes WHERE ts >= ?", (desde,)).fetchone(),
                "camara": q("SELECT ts, texto FROM eventos WHERE tipo='camara' AND ts >= ? ORDER BY ts", (desde,)).fetchall(),
            }
