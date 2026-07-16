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
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

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

            CREATE TABLE IF NOT EXISTS control_conversacion (
                jid         TEXT PRIMARY KEY,
                humano      INTEGER DEFAULT 0,  -- 1 = humano tomó control, bot en pausa
                etiqueta    TEXT DEFAULT '',    -- estado de venta (Compró, No compró, ...)
                actualizado TEXT
            );

            CREATE TABLE IF NOT EXISTS producto_override (
                codigo         TEXT PRIMARY KEY,
                precio_publico INTEGER,
                precio_mayoreo INTEGER,
                nombre         TEXT,
                observaciones  TEXT,
                sin_stock      INTEGER,
                actualizado    TEXT
            );

            CREATE TABLE IF NOT EXISTS producto_nuevo (
                codigo         TEXT PRIMARY KEY,
                categoria      TEXT,
                nombre         TEXT,
                descripcion    TEXT,
                material       TEXT,
                uso            TEXT,
                tallas         TEXT,
                colores        TEXT,
                precio_publico INTEGER,
                precio_mayoreo INTEGER,
                marca          TEXT,
                observaciones  TEXT,
                creado         TEXT
            );
            """
        )
        # Columna 'origen' en mensajes (cliente | bot | humano) — migracion suave.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(mensajes)")]
        if "origen" not in cols:
            conn.execute("ALTER TABLE mensajes ADD COLUMN origen TEXT DEFAULT 'bot'")
        # Columna 'etiqueta' en control_conversacion — migracion suave.
        ccols = [r["name"] for r in conn.execute("PRAGMA table_info(control_conversacion)")]
        if "etiqueta" not in ccols:
            conn.execute("ALTER TABLE control_conversacion ADD COLUMN etiqueta TEXT DEFAULT ''")
        # Columna 'sin_stock' en producto_override — migracion suave.
        ocols = [r["name"] for r in conn.execute("PRAGMA table_info(producto_override)")]
        if "sin_stock" not in ocols:
            conn.execute("ALTER TABLE producto_override ADD COLUMN sin_stock INTEGER")
        # Columna 'estado_pago' en cotizaciones — migracion suave.
        cotcols = [r["name"] for r in conn.execute("PRAGMA table_info(cotizaciones)")]
        if "estado_pago" not in cotcols:
            conn.execute(
                "ALTER TABLE cotizaciones ADD COLUMN estado_pago TEXT DEFAULT 'pendiente'"
            )
        # Columna 'seguimiento_ts' en control_conversacion — migracion suave.
        cc2 = [r["name"] for r in conn.execute("PRAGMA table_info(control_conversacion)")]
        if "seguimiento_ts" not in cc2:
            conn.execute("ALTER TABLE control_conversacion ADD COLUMN seguimiento_ts TEXT")
        # Columna 'token' en cotizaciones — link público único por cliente (descarga).
        cot2 = [r["name"] for r in conn.execute("PRAGMA table_info(cotizaciones)")]
        if "token" not in cot2:
            conn.execute("ALTER TABLE cotizaciones ADD COLUMN token TEXT")


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
        token = cot.get("token") or secrets.token_urlsafe(9)
        conn.execute(
            """
            INSERT OR REPLACE INTO cotizaciones
                (codigo, jid, nombre, empresa, email, telefono,
                 tipo_precio, items_json, total, ts, token)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                token,
            ),
        )
        return codigo


def get_cotizacion_por_token(token: str) -> dict | None:
    """Busca una cotización por su token público (link de descarga)."""
    token = (token or "").strip()
    if not token:
        return None
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM cotizaciones WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["items"] = json.loads(d.pop("items_json") or "[]")
    return d


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


def ultima_cotizacion_de(jid: str) -> dict | None:
    """Devuelve la cotización más reciente de un cliente (para cobrarla)."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM cotizaciones WHERE jid = ? ORDER BY ts DESC LIMIT 1",
            (jid,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["items"] = json.loads(d.pop("items_json") or "[]")
    return d


def marcar_cotizacion_pagada(codigo: str) -> dict | None:
    """Marca una cotización como pagada. Devuelve la cotización, o None."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM cotizaciones WHERE codigo = ?", (codigo,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE cotizaciones SET estado_pago = 'pagado' WHERE codigo = ?",
            (codigo,),
        )
    d = dict(row)
    d["items"] = json.loads(d.pop("items_json") or "[]")
    d["estado_pago"] = "pagado"
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

def registrar_mensaje(
    jid: str, direccion: str, texto: str, origen: str | None = None
) -> dict:
    """Guarda un mensaje y lo devuelve.
    direccion: 'in' | 'out'. origen: 'cliente' | 'bot' | 'humano'
    (si no se pasa, se deduce de la direccion)."""
    if origen is None:
        origen = "cliente" if direccion == "in" else "bot"
    ts = _now()
    with _lock, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO mensajes (jid, direccion, texto, ts, origen) "
            "VALUES (?, ?, ?, ?, ?)",
            (jid, direccion, texto, ts, origen),
        )
        msg_id = cur.lastrowid
    return {"id": msg_id, "jid": jid, "direccion": direccion,
            "texto": texto, "ts": ts, "origen": origen}


# ── CONTROL DE CONVERSACION (bot vs humano) ──────────────────────────────

def es_modo_humano(jid: str) -> bool:
    """True si un humano tomó el control de la conversación (bot en pausa)."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT humano FROM control_conversacion WHERE jid = ?", (jid,)
        ).fetchone()
        return bool(row and row["humano"])


def set_modo_humano(jid: str, humano: bool) -> None:
    """Activa/desactiva el modo humano (pausa/reanuda el bot) para un jid."""
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO control_conversacion (jid, humano, actualizado)
            VALUES (?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET humano = ?, actualizado = ?
            """,
            (jid, 1 if humano else 0, _now(), 1 if humano else 0, _now()),
        )


def set_etiqueta(jid: str, etiqueta: str) -> None:
    """Asigna la etiqueta / estado de venta a una conversación (Compró,
    No compró, Interesado, etc.)."""
    etiqueta = (etiqueta or "").strip()
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO control_conversacion (jid, etiqueta, actualizado)
            VALUES (?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET etiqueta = ?, actualizado = ?
            """,
            (jid, etiqueta, _now(), etiqueta, _now()),
        )


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
                   l.empresa                     AS empresa,
                   COALESCE(c.humano, 0)         AS humano,
                   COALESCE(c.etiqueta, '')      AS etiqueta
            FROM mensajes m
            LEFT JOIN leads l ON l.jid = m.jid
            LEFT JOIN control_conversacion c ON c.jid = m.jid
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


def agregar_item_estado(jid: str, item: dict) -> dict:
    """Agrega o acumula un item en el estado en curso de forma ATÓMICA (todo el
    leer-modificar-guardar bajo el mismo _lock y conexion). Evita que llamadas
    concurrentes a agregar_item se pisen (el modelo suele llamarlas en paralelo
    cuando el cliente pide varios productos en un mismo mensaje).
    Devuelve {'items': [...], 'total': n}."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT tipo_precio, items_json, cliente_json "
            "FROM estado_cotizacion WHERE jid = ?",
            (jid,),
        ).fetchone()
        if row:
            tipo_precio = row["tipo_precio"] or "publico"
            items = json.loads(row["items_json"] or "[]")
            cliente_json = row["cliente_json"] or "{}"
        else:
            tipo_precio = "publico"
            items = []
            cliente_json = "{}"
        for it in items:
            if it["codigo"] == item["codigo"]:
                it["cantidad"] += item["cantidad"]
                it["precio"] = item["precio"]
                it["subtotal"] = it["precio"] * it["cantidad"]
                break
        else:
            items.append(item)
        conn.execute(
            """
            INSERT OR REPLACE INTO estado_cotizacion
                (jid, tipo_precio, items_json, cliente_json, actualizado)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                jid,
                tipo_precio,
                json.dumps(items, ensure_ascii=False),
                cliente_json,
                _now(),
            ),
        )
        total = sum(i["subtotal"] for i in items)
        return {"items": items, "total": total}


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


def cita_existente(jid: str, fecha: str, hora: str) -> dict | None:
    """Devuelve la cita (no cancelada) de ese cliente en esa fecha/hora, o None.
    Sirve para no chocar con la propia cita del cliente al reintentar."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM citas WHERE jid = ? AND fecha = ? AND hora = ? "
            "AND estado != 'cancelada' LIMIT 1",
            (jid, fecha, hora),
        ).fetchone()
        return dict(row) if row else None


def actualizar_cita_email(cita_id: int, email: str) -> None:
    """Actualiza el correo de una cita ya creada."""
    with _lock, _conn() as conn:
        conn.execute(
            "UPDATE citas SET email = ? WHERE id = ?", (email, cita_id)
        )


def citas_de_cliente(jid: str) -> list[dict]:
    """Devuelve las citas activas (no canceladas) de un cliente, ordenadas."""
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM citas WHERE jid = ? AND estado != 'cancelada' "
            "ORDER BY fecha ASC, hora ASC",
            (jid,),
        ).fetchall()
        return [dict(r) for r in rows]


def cancelar_cita(cita_id: int, jid: str) -> dict | None:
    """Cancela una cita del cliente (marca estado='cancelada', libera el
    horario). Devuelve la cita cancelada, o None si no existe o no es suya."""
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM citas WHERE id = ? AND jid = ? AND estado != 'cancelada'",
            (cita_id, jid),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE citas SET estado = 'cancelada' WHERE id = ?", (cita_id,)
        )
        return dict(row)


def listar_citas(limite: int = 100) -> list[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM citas WHERE estado != 'cancelada' "
            "ORDER BY fecha ASC, hora ASC LIMIT ?",
            (limite,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── SEGUIMIENTO (recuperar clientes que dejaron en visto) ────────────────

def candidatos_seguimiento(horas: float, ventana_horas: float = 22) -> list[str]:
    """Devuelve los jids a los que hay que enviar un mensaje de seguimiento:
    - el último mensaje fue del bot (el cliente no respondió),
    - han pasado al menos 'horas' desde ese último mensaje,
    - el cliente escribió hace menos de 'ventana_horas' (dentro de la ventana de
      24h de WhatsApp, para poder enviar texto libre),
    - no está en modo humano,
    - no se le envió ya un seguimiento desde su último mensaje."""
    ahora = datetime.now(timezone.utc)
    limite_silencio = (ahora - timedelta(hours=horas)).isoformat()
    limite_ventana = (ahora - timedelta(hours=ventana_horas)).isoformat()
    candidatos = []
    with _lock, _conn() as conn:
        jids = [r["jid"] for r in conn.execute("SELECT DISTINCT jid FROM mensajes")]
        for jid in jids:
            um = conn.execute(
                "SELECT direccion, ts FROM mensajes WHERE jid = ? "
                "ORDER BY id DESC LIMIT 1", (jid,)
            ).fetchone()
            if not um or um["direccion"] != "out":
                continue
            if um["ts"] > limite_silencio:   # aún no pasa el tiempo de silencio
                continue
            ui = conn.execute(
                "SELECT ts FROM mensajes WHERE jid = ? AND direccion = 'in' "
                "ORDER BY id DESC LIMIT 1", (jid,)
            ).fetchone()
            if not ui or ui["ts"] < limite_ventana:  # fuera de la ventana 24h
                continue
            cc = conn.execute(
                "SELECT humano, seguimiento_ts FROM control_conversacion WHERE jid = ?",
                (jid,),
            ).fetchone()
            if cc:
                if cc["humano"]:
                    continue
                if cc["seguimiento_ts"] and cc["seguimiento_ts"] > ui["ts"]:
                    continue  # ya se le hizo seguimiento tras su último mensaje
            candidatos.append(jid)
    return candidatos


def marcar_seguimiento(jid: str) -> None:
    """Registra que a este cliente ya se le envió un seguimiento."""
    with _lock, _conn() as conn:
        conn.execute(
            """
            INSERT INTO control_conversacion (jid, seguimiento_ts, actualizado)
            VALUES (?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET seguimiento_ts = ?, actualizado = ?
            """,
            (jid, _now(), _now(), _now(), _now()),
        )


# ── AJUSTES DE PRODUCTOS (precios/nombre editables desde el dashboard) ────

def get_overrides() -> dict:
    """Devuelve {codigo: {campo: valor}} con los ajustes guardados (solo los
    campos con valor). Se aplican sobre el catálogo base."""
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT * FROM producto_override").fetchall()
    result = {}
    for r in rows:
        d = dict(r)
        codigo = d.pop("codigo")
        d.pop("actualizado", None)
        campos = {k: v for k, v in d.items() if v is not None and v != ""}
        if campos:
            result[codigo] = campos
    return result


def set_override(codigo: str, campos: dict) -> None:
    """Guarda/actualiza el ajuste de un producto. Solo toca los campos dados."""
    codigo = (codigo or "").strip().upper()
    permitidos = {"precio_publico", "precio_mayoreo", "nombre", "observaciones",
                  "sin_stock"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    if not campos:
        return
    for k in ("precio_publico", "precio_mayoreo"):
        if k in campos and campos[k] is not None and campos[k] != "":
            campos[k] = int(campos[k])
    if "sin_stock" in campos:
        campos["sin_stock"] = 1 if campos["sin_stock"] else 0
    with _lock, _conn() as conn:
        existe = conn.execute(
            "SELECT 1 FROM producto_override WHERE codigo = ?", (codigo,)
        ).fetchone()
        if existe:
            sets = ", ".join(f"{k} = ?" for k in campos)
            conn.execute(
                f"UPDATE producto_override SET {sets}, actualizado = ? WHERE codigo = ?",
                (*campos.values(), _now(), codigo),
            )
        else:
            cols = ["codigo", *campos.keys(), "actualizado"]
            vals = [codigo, *campos.values(), _now()]
            ph = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO producto_override ({', '.join(cols)}) VALUES ({ph})",
                vals,
            )


def listar_productos_nuevos() -> list[dict]:
    """Productos creados desde el dashboard (se suman al catálogo del bot)."""
    campos = ["codigo", "categoria", "nombre", "descripcion", "material", "uso",
              "tallas", "colores", "precio_publico", "precio_mayoreo", "marca",
              "observaciones"]
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT * FROM producto_nuevo ORDER BY creado DESC").fetchall()
    return [{k: dict(r).get(k) for k in campos} for r in rows]


def existe_producto_codigo(codigo: str) -> bool:
    """True si el código ya existe entre los productos nuevos."""
    codigo = (codigo or "").strip().upper()
    with _lock, _conn() as conn:
        r = conn.execute(
            "SELECT 1 FROM producto_nuevo WHERE codigo = ?", (codigo,)
        ).fetchone()
        return bool(r)


def crear_producto(campos: dict) -> str:
    """Crea un producto nuevo. Genera un código si no viene. Devuelve el código."""
    codigo = (campos.get("codigo") or "").strip().upper()
    with _lock, _conn() as conn:
        if not codigo:
            n = conn.execute("SELECT COUNT(*) AS c FROM producto_nuevo").fetchone()["c"]
            codigo = f"NEW-{n + 1:04d}"
        conn.execute(
            """
            INSERT OR REPLACE INTO producto_nuevo
                (codigo, categoria, nombre, descripcion, material, uso, tallas,
                 colores, precio_publico, precio_mayoreo, marca, observaciones, creado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                codigo,
                campos.get("categoria", "") or "General",
                campos.get("nombre", ""),
                campos.get("descripcion", ""),
                campos.get("material", "") or "—",
                campos.get("uso", ""),
                campos.get("tallas", "") or "—",
                campos.get("colores", "") or "—",
                int(campos.get("precio_publico") or 0),
                int(campos.get("precio_mayoreo") or 0),
                campos.get("marca", "") or "—",
                campos.get("observaciones", ""),
                _now(),
            ),
        )
        return codigo


# Inicializa la base al importar el modulo.
init_db()
