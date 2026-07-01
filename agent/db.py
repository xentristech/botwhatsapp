"""
Capa SQLite de PLATIM Agent.

Tablas:
    leads        — clientes/contactos por numero de WhatsApp (jid)
    cotizaciones — cotizaciones generadas (cabecera + items en JSON)
    mensajes     — historial de mensajes (in/out) para el dashboard

El acceso es sincrono (sqlite3) y serializado con un lock, suficiente para
el volumen esperado. Las funciones se llaman desde codigo async sin bloquear
de forma apreciable.
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "platim.db")

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Crea las tablas si no existen. Idempotente."""
    with _lock, _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS leads (
                jid         TEXT PRIMARY KEY,
                nombre      TEXT,
                empresa     TEXT,
                email       TEXT,
                telefono    TEXT,
                es_mayorista INTEGER DEFAULT 0,
                creado      TEXT,
                actualizado TEXT
            );

            CREATE TABLE IF NOT EXISTS cotizaciones (
                codigo      TEXT PRIMARY KEY,
                jid         TEXT,
                nombre      TEXT,
                empresa     TEXT,
                email       TEXT,
                telefono    TEXT,
                tipo_precio TEXT,
                items_json  TEXT,
                total       INTEGER,
                ts          TEXT
            );

            CREATE TABLE IF NOT EXISTS mensajes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                jid       TEXT,
                direccion TEXT,   -- 'in' | 'out'
                texto     TEXT,
                ts        TEXT
            );

            CREATE TABLE IF NOT EXISTS estado_cotizacion (
                jid          TEXT PRIMARY KEY,
                tipo_precio  TEXT,
                items_json   TEXT,
                cliente_json TEXT,
                actualizado  TEXT
            );

            CREATE TABLE IF NOT EXISTS citas (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                jid      TEXT,
                nombre   TEXT,
                email    TEXT,
                telefono TEXT,
                fecha    TEXT,   -- YYYY-MM-DD (hora Colombia)
                hora     TEXT,   -- HH:MM  (hora Colombia)
                asesora  TEXT,
                estado   TEXT DEFAULT 'agendada',  -- agendada | cancelada
                creado   TEXT
            );
            """
        )


# ── LEADS ────────────────────────────────────────────────────────────────

def upsert_lead(jid: str, **campos) -> None:
    """Crea o actualiza un lead. Solo sobreescribe los campos provistos."""
    permitidos = {"nombre", "empresa", "email", "telefono", "es_mayorista"}
    campos = {k: v for k, v in campos.items() if k in permitidos and v is not None}
    if "es_mayorista" in campos:
        campos["es_mayorista"] = 1 if campos["es_mayorista"] else 0
    with _lock, _conn() as conn:
        existe = conn.execute("SELECT 1 FROM leads WHERE jid = ?", (jid,)).fetchone()
        if existe:
            if campos:
                sets = ", ".join(f"{k} = ?" for k in campos)
                conn.execute(
                    f"UPDATE leads SET {sets}, actualizado = ? WHERE jid = ?",
                    (*campos.values(), _now(), jid),
                )
        else:
            cols = ["jid", *campos.keys(), "creado", "actualizado"]
            vals = [jid, *campos.values(), _now(), _now()]
            placeholders = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO leads ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )


def get_lead(jid: str) -> dict | None:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE jid = ?", (jid,)).fetchone()
        return dict(row) if row else None


def listar_leads(limite: int = 100) -> list[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY actualizado DESC LIMIT ?", (limite,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── COTIZACIONES ─────────────────────────────────────────────────────────

def _generar_codigo_cotizacion(conn: sqlite3.Connection) -> str:
    """Genera COT-YYYYMMDD-XXXX con contador diario."""
    hoy = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefijo = f"COT-{hoy}-"
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM cotizaciones WHERE codigo LIKE ?",
        (prefijo + "%",),
    ).fetchone()
    secuencia = (row["n"] if row else 0) + 1
    return f"{prefijo}{secuencia:04d}"


def guardar_cotizacion(cot: dict) -> str:
    """Persiste una cotizacion. Si no trae 'codigo', lo genera.
    Espera: jid, nombre, empresa, email, telefono, tipo_precio, items, total, ts
    Devuelve el codigo de la cotizacion."""
    with _lock, _conn() as conn:
        codigo = cot.get("codigo") or _generar_codigo_cotizacion(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO cotizaciones
                (codigo, jid, nombre, empresa, email, telefono,
                 tipo_precio, items_json, total, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                codigo,
                cot.get("jid", ""),
                cot.get("nombre", ""),
                cot.get("empresa", ""),
                cot.get("email", ""),
                cot.get("telefono", ""),
                cot.get("tipo_precio", "publico"),
                json.dumps(cot.get("items", []), ensure_ascii=False),
                int(cot.get("total", 0)),
                cot.get("ts") or _now(),
            ),
        )
        return codigo


def get_cotizacion(codigo: str) -> dict | None:
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM cotizaciones WHERE codigo = ?", (codigo,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["items"] = json.loads(d.pop("items_json") or "[]")
    return d


def listar_cotizaciones(limite: int = 100) -> list[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT codigo, jid, nombre, empresa, email, telefono, "
            "tipo_precio, total, ts FROM cotizaciones ORDER BY ts DESC LIMIT ?",
            (limite,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── MENSAJES ─────────────────────────────────────────────────────────────

def registrar_mensaje(jid: str, direccion: str, texto: str) -> dict:
    """Guarda un mensaje (direccion 'in' o 'out') y lo devuelve."""
    ts = _now()
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO mensajes (jid, direccion, texto, ts) VALUES (?, ?, ?, ?)",
            (jid, direccion, texto, ts),
        )
        msg_id = cur.lastrowid
    return {"id": msg_id, "jid": jid, "direccion": direccion, "texto": texto, "ts": ts}


def listar_conversaciones(limite: int = 100) -> list[dict]:
    """Devuelve una fila por conversacion (jid): ultimo mensaje, su fecha,
    total de mensajes y (si existe) el nombre del lead. Ordenado por el
    mensaje mas reciente."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            """
            SELECT m.jid                         AS jid,
                   COUNT(*)                      AS n_mensajes,
                   MAX(m.id)                     AS ultimo_id,
                   (SELECT texto FROM mensajes WHERE jid = m.jid
                     ORDER BY id DESC LIMIT 1)   AS ultimo_texto,
                   (SELECT ts FROM mensajes WHERE jid = m.jid
                     ORDER BY id DESC LIMIT 1)   AS ultimo_ts,
                   l.nombre                      AS nombre,
                   l.empresa                     AS empresa
            FROM mensajes m
            LEFT JOIN leads l ON l.jid = m.jid
            GROUP BY m.jid
            ORDER BY ultimo_id DESC
            LIMIT ?
            """,
            (limite,),
        ).fetchall()
        return [dict(r) for r in rows]


def listar_mensajes(jid: str | None = None, limite: int = 200) -> list[dict]:
    with _lock, _conn() as conn:
        if jid:
            rows = conn.execute(
                "SELECT * FROM mensajes WHERE jid = ? ORDER BY id DESC LIMIT ?",
                (jid, limite),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mensajes ORDER BY id DESC LIMIT ?", (limite,)
            ).fetchall()
        return [dict(r) for r in rows]


# ── ESTADO DE COTIZACION EN CURSO ────────────────────────────────────────

def get_estado_cot(jid: str) -> dict | None:
    """Devuelve el estado de la cotizacion en curso del jid, o None."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM estado_cotizacion WHERE jid = ?", (jid,)
        ).fetchone()
    if not row:
        return None
    return {
        "tipo_precio": row["tipo_precio"] or "publico",
        "items": json.loads(row["items_json"] or "[]"),
        "cliente": json.loads(row["cliente_json"] or "{}"),
    }


def save_estado_cot(jid: str, estado: dict) -> None:
    """Persiste (crea o reemplaza) el estado de la cotizacion en curso."""
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO estado_cotizacion
                (jid, tipo_precio, items_json, cliente_json, actualizado)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                jid,
                estado.get("tipo_precio", "publico"),
                json.dumps(estado.get("items", []), ensure_ascii=False),
                json.dumps(estado.get("cliente", {}), ensure_ascii=False),
                _now(),
            ),
        )


# ── CITAS (asesora Patricia) ─────────────────────────────────────────────

def horas_tomadas(fecha: str) -> set[str]:
    """Devuelve el conjunto de horas ya reservadas (no canceladas) en una fecha."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT hora FROM citas WHERE fecha = ? AND estado != 'cancelada'",
            (fecha,),
        ).fetchall()
        return {r["hora"] for r in rows}


def crear_cita(cita: dict) -> int:
    """Inserta una cita y devuelve su id.
    Espera: jid, nombre, email, telefono, fecha, hora, asesora."""
    with _lock, _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO citas (jid, nombre, email, telefono, fecha, hora,
                               asesora, estado, creado)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'agendada', ?)
            """,
            (
                cita.get("jid", ""),
                cita.get("nombre", ""),
                cita.get("email", ""),
                cita.get("telefono", ""),
                cita.get("fecha", ""),
                cita.get("hora", ""),
                cita.get("asesora", "Patricia"),
                _now(),
            ),
        )
        return cur.lastrowid


def listar_citas(limite: int = 100) -> list[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM citas WHERE estado != 'cancelada' "
            "ORDER BY fecha ASC, hora ASC LIMIT ?",
            (limite,),
        ).fetchall()
        return [dict(r) for r in rows]


# Inicializa la base al importar el modulo.
init_db()
