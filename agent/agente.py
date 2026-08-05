"""
Logica principal del agente PLATIM con OpenAI Agents SDK (>= 0.17.7).

Expone:
    procesar_mensaje(jid, texto) -> str   # corre el agente para un mensaje entrante

Define las 7 tools del flujo de cotizacion y mantiene el estado de la
cotizacion en curso por numero de WhatsApp (jid).
"""

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from dotenv import load_dotenv

from agents import (
    Agent,
    RunContextWrapper,
    Runner,
    SQLiteSession,
    WebSearchTool,
    function_tool,
)

from datetime import date, timedelta

from agent import catalogo
from agent.db import (
    DB_PATH,
    actualizar_cita_email,
    agregar_item_estado,
    cancelar_cita,
    cita_existente,
    citas_de_cliente,
    crear_cita,
    get_cotizacion,
    get_estado_cot,
    guardar_cotizacion,
    horas_tomadas,
    registrar_mensaje,
    save_estado_cot,
    ultima_cotizacion_de,
    upsert_lead,
)

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ── Cotización por link (conversión Google Ads) ──────────────────────────
# Página estática "solicitud enviada" en platim.co: al abrirla dispara la
# conversión de Google Ads. Recibe ?c=<token> y muestra/descarga el PDF.
COTIZACION_LANDING = os.getenv(
    "COTIZACION_LANDING", "https://www.platim.co/solicitud-enviada"
)
# Base pública del bot que sirve el PDF/JSON de cada cotización por token.
API_BASE_URL = os.getenv("API_BASE_URL", "https://api.platim.co").rstrip("/")
# Base pública REAL desde donde WhatsApp descarga las fotos de producto
# (debe ser HTTPS y resolver: por defecto la URL sslip.io del VPS).
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "https://157-137-224-141.sslip.io"
).rstrip("/")

# ── Agenda de la asesora Patricia ────────────────────────────────────────
ASESORA = "Patricia"
# WhatsApp(s) que reciben las alertas de "producto no encontrado".
# Uno o varios números separados por coma (Patricia + avisos internos).
# (El correo de la alerta usa las copias internas de email_service: ventas+info.)
ASESORA_WHATSAPP = os.getenv(
    "ASESORA_WHATSAPP", "573188940939,573003730876"
)


def _numeros_alerta() -> list[str]:
    """Lista de números (sin duplicados) que reciben las alertas por WhatsApp."""
    vistos, salida = set(), []
    for n in ASESORA_WHATSAPP.split(","):
        n = n.strip()
        if n and n not in vistos:
            vistos.add(n)
            salida.append(n)
    return salida


# ── Modo administrador por WhatsApp (Patricia + Eathan) ──────────────────
# Estos números, al escribirle al bot, entran en modo ADMIN (gestionan el
# catálogo por chat) en vez de ser atendidos como clientes.
ADMIN_WHATSAPP = os.getenv("ADMIN_WHATSAPP", "573188940939,573003730876")


def _solo_digitos(x: str) -> str:
    return "".join(c for c in (x or "") if c.isdigit())


def es_admin(jid: str) -> bool:
    """True si el número (jid) es un administrador autorizado."""
    n = _solo_digitos(jid)
    admins = {_solo_digitos(a) for a in ADMIN_WHATSAPP.split(",") if a.strip()}
    return bool(n) and n in admins


# Prefijo del "chat de prueba" del dashboard: en este modo las herramientas NO
# ejecutan efectos reales (no envían WhatsApp/correo, no avisan a Patricia).
PRUEBA_PREFIX = "test:"


def es_prueba(jid: str) -> bool:
    """True si la conversación es del chat de prueba del dashboard."""
    return (jid or "").startswith(PRUEBA_PREFIX)


# Franjas de 30 min dentro de 2-4 PM (hora Colombia).
SLOTS_ASESORA = ["14:00", "14:30", "15:00", "15:30"]
_HORA_LEGIBLE = {
    "14:00": "2:00 PM", "14:30": "2:30 PM",
    "15:00": "3:00 PM", "15:30": "3:30 PM",
}
_DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _hoy_colombia() -> date:
    """Fecha actual en hora Colombia (UTC-5, sin horario de verano)."""
    return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def _fecha_es(f: date) -> str:
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{_DIAS_ES[f.weekday()]} {f.day} de {meses[f.month - 1]}"

# Ruta de la base donde el SDK guarda la memoria de conversacion.
SESSIONS_DB = os.path.join(os.path.dirname(DB_PATH), "sessions.db")


# ── Contexto y estado por conversacion ───────────────────────────────────

@dataclass
class PlatimContext:
    """Contexto que viaja con cada corrida del agente."""
    jid: str


# Sesiones de conversacion del SDK por jid (memoria de turnos, persistida).
_sesiones: dict[str, SQLiteSession] = {}


def get_estado(jid: str) -> dict:
    """Carga el estado de la cotizacion en curso desde SQLite.
    Si no existe, devuelve un estado vacio por defecto."""
    estado = get_estado_cot(jid)
    if estado is None:
        estado = {"tipo_precio": "publico", "items": [], "cliente": {}}
    return estado


def save_estado(jid: str, estado: dict) -> None:
    """Persiste el estado de la cotizacion en curso (write-through)."""
    save_estado_cot(jid, estado)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _moneda(valor) -> str:
    try:
        n = int(round(float(valor)))
    except (TypeError, ValueError):
        n = 0
    return "$" + f"{n:,}".replace(",", ".")


# Validacion de email: formato basico y dominio con TLD razonable.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def email_valido(email: str) -> bool:
    """True si el email tiene un formato valido (usuario@dominio.tld)."""
    e = (email or "").strip()
    if not e or ".." in e or e.count("@") != 1:
        return False
    return bool(_EMAIL_RE.match(e))


def dominio_recibe_correo(email: str) -> bool:
    """Verifica que el dominio del email pueda recibir correo (tiene registros
    MX, o A/AAAA como respaldo). Sirve para detectar dominios inexistentes o mal
    escritos (ej. 'gmail.con'). Ante un error de red/DNS temporal NO bloquea:
    devuelve True para no rechazar correos legitimos por un fallo de conexion."""
    try:
        import dns.resolver

        dominio = email.strip().rsplit("@", 1)[-1]
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5.0
        try:
            respuestas = resolver.resolve(dominio, "MX")
            if len(respuestas) > 0:
                return True
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            # Sin MX: probar A/AAAA (respaldo segun RFC 5321).
            for tipo in ("A", "AAAA"):
                try:
                    if len(resolver.resolve(dominio, tipo)) > 0:
                        return True
                except Exception:  # noqa: BLE001
                    continue
            return False
        return False
    except dns.resolver.NXDOMAIN:
        return False
    except Exception:  # noqa: BLE001
        # Timeout / sin red / error inesperado: no bloquear.
        return True


def formatear_whatsapp(texto: str) -> str:
    """Convierte formato Markdown (que suele producir el LLM) al formato que
    entiende WhatsApp: negrita con un solo *, sin encabezados #, sin ** ni ###,
    enlaces legibles. Las listas con '- ', '* ' o '1.' y las citas '> ' se
    mantienen porque WhatsApp ya las soporta."""
    if not texto:
        return texto
    t = texto
    # Encabezados Markdown (#, ##, ###...) al inicio de linea -> negrita WhatsApp
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", r"*\1*", t)
    # Negrita+italica combinada ***x*** -> *x*
    t = re.sub(r"\*\*\*(.+?)\*\*\*", r"*\1*", t)
    # Negrita **x** -> *x*  (WhatsApp usa un solo asterisco)
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)
    # Negrita Markdown __x__ -> *x*
    t = re.sub(r"__(.+?)__", r"*\1*", t)
    # Enlaces [texto](url) -> texto (url)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", t)
    # Reglas horizontales (---, ***, ___ en linea sola) -> quitar
    t = re.sub(r"(?m)^\s*([-*_])\1{2,}\s*$", "", t)
    return t


def validar_email_cliente(email: str) -> tuple[bool, str]:
    """Valida el correo del cliente en dos niveles.
    Devuelve (es_valido, motivo). motivo: "" si valido, "formato" si el formato
    es incorrecto, "dominio" si el dominio no existe / no puede recibir correo."""
    if not email_valido(email):
        return False, "formato"
    if not dominio_recibe_correo(email):
        return False, "dominio"
    return True, ""


def _formato_texto_cotizacion(items: list[dict], total: int, codigo: str) -> str:
    """Fallback en texto plano para WhatsApp si falla el envio del PDF."""
    lineas = [f"*Cotización PLATIM {codigo}*", ""]
    for it in items:
        lineas.append(
            f"• *{it['nombre']}* ({it['codigo']}) x{it['cantidad']} — "
            f"{_moneda(it['subtotal'])}"
        )
    lineas.append("")
    lineas.append(f"*TOTAL: {_moneda(total)} COP*")
    lineas.append("Vigencia: 30 días.")
    return "\n".join(lineas)


# ── Las 7 tools del agente ───────────────────────────────────────────────

@function_tool
def buscar_productos(
    ctx: RunContextWrapper[PlatimContext], query: str, categoria: str = ""
) -> str:
    """Busca productos por nombre, descripcion, uso o categoria.
    Categorias disponibles: Uniformes, Buzos/Overoles, Pantalones,
    Alta visibilidad, Protección de cabeza, Protección ocular,
    Protección respiratoria, Protección auditiva, Protección manos,
    Protección corporal, Calzado de seguridad, Seguridad en altura,
    Señalización, Primeros auxilios, Emergencias, Accesorios."""
    estado = get_estado(ctx.context.jid)
    es_mayorista = estado.get("tipo_precio") == "mayoreo"
    resultados = catalogo.buscar(query, categoria)[:12]
    if not resultados:
        return json.dumps(
            {"encontrados": 0, "mensaje": "Sin coincidencias. Sugiere alternativas."},
            ensure_ascii=False,
        )
    salida = []
    for p in resultados:
        salida.append(
            {
                "codigo": p["codigo"],
                "nombre": p["nombre"],
                "categoria": p["categoria"],
                "uso": p["uso"],
                "tallas": p["tallas"],
                "colores": p["colores"],
                "marca": p["marca"],
                "precio": int(p.get("precio_publico") or 0),
                "precio_100_unidades": int(p.get("precio_volumen") or 0),
                "observaciones": p["observaciones"],
                "tiene_foto": bool(p.get("tiene_foto")),
            }
        )
    return json.dumps(
        {"encontrados": len(salida),
         "nota_precio": ("'precio' es unitario al público. "
                         "'precio_100_unidades' (si es > 0) es el precio unitario "
                         "cuando el cliente lleva 100 o más de ESE producto."),
         "productos": salida},
        ensure_ascii=False,
    )


@function_tool
async def enviar_fotos_productos(
    ctx: RunContextWrapper[PlatimContext], codigos: list[str]
) -> str:
    """Envía por WhatsApp la FOTO de uno o varios productos (con su nombre y
    precio como pie de foto). Úsalo SOLO con productos cuyo buscar_productos
    marque "tiene_foto": true. Ideal al recomendar o al mostrar la cotización
    para que el cliente vea el producto. Ignora los que no tengan foto."""
    jid = ctx.context.jid
    estado = get_estado(jid)
    es_mayorista = estado.get("tipo_precio") == "mayoreo"
    enviadas, sin_foto = [], []
    from agent.whatsapp import send_image

    for cod in codigos:
        p = catalogo.obtener(cod)
        if not p or not p.get("tiene_foto"):
            sin_foto.append(cod)
            continue
        precio = catalogo.precio_de(p, es_mayorista)
        caption = f"*{p['nombre']}* — ${precio:,.0f} COP".replace(",", ".")
        link = f"{PUBLIC_BASE_URL}/fotos/{p['codigo']}"
        try:
            await send_image(jid, link, caption)
            enviadas.append(p["codigo"])
        except Exception as e:  # noqa: BLE001
            print(f"Error enviando foto {cod}: {e}")
            sin_foto.append(cod)
    return json.dumps(
        {"enviadas": enviadas, "sin_foto": sin_foto}, ensure_ascii=False
    )


@function_tool
async def reportar_producto_no_encontrado(
    ctx: RunContextWrapper[PlatimContext],
    descripcion: str,
    referencia_web: str = "",
) -> str:
    """Avisa a la asesora Patricia (correo + WhatsApp) que un cliente pidió un
    producto que NO está en el catálogo (buscar_productos no lo encontró), para
    que ella decida si se agrega y a qué valor. Úsalo UNA sola vez por producto
    faltante y SOLO cuando el cliente muestre interés real de comprarlo/cotizarlo.
    'descripcion' = qué pidió el cliente, lo más claro posible (tipo, material,
    marca, talla/cantidad si la dijo).
    'referencia_web' = referencia que encontraste con la búsqueda web (marca/
    modelo típico, especificaciones y precio referencial de mercado) para AYUDAR
    a Patricia a decidir. Es solo para Patricia, NUNCA para dársela al cliente
    como precio nuestro."""
    jid = ctx.context.jid
    if es_prueba(jid):
        return json.dumps(
            {"ok": True, "modo_prueba": True,
             "instruccion": ("Pídele al cliente que espere mientras verificas en "
                             "bodega y con la asesora; no le des precio ni lo "
                             "mandes con un humano. (PRUEBA: no se envió alerta real.)")},
            ensure_ascii=False,
        )
    estado = get_estado(jid)
    cliente = estado.get("cliente", {})
    nombre = cliente.get("nombre", "")
    telefono = cliente.get("telefono") or jid.split("@")[0]
    referencia_web = (referencia_web or "").strip()

    from agent.db import crear_solicitud_producto
    from agent.email_service import enviar_alerta_producto
    from agent.whatsapp import send_text

    sid = crear_solicitud_producto(jid, nombre, telefono, descripcion, referencia_web)

    email_ok = False
    try:
        email_ok = await enviar_alerta_producto(
            nombre, telefono, descripcion, referencia_web
        )
    except Exception as e:  # noqa: BLE001
        print(f"[alerta_producto] error email: {e}")

    aviso = (
        f"🔔 *Producto solicitado NO disponible* (Solicitud #{sid})\n"
        f"Cliente: {nombre or 'sin nombre'} ({telefono})\n"
        f"Pidió: {descripcion}\n"
    )
    if referencia_web:
        aviso += f"Referencia (web): {referencia_web}\n"
    aviso += (
        f"\n¿Lo agregamos? Responde aquí: *autoriza la {sid} a <precio>* "
        "(ej: \"autoriza la {sid} a 180000\"), o cárgalo en el dashboard."
    ).replace("{sid}", str(sid))
    from agent.whatsapp import send_template

    params_plantilla = [str(sid), nombre or "sin nombre", descripcion, str(sid)]
    wa_ok = False
    for numero in _numeros_alerta():
        enviado = False
        # 1) Plantilla aprobada: llega SIEMPRE (fuera de la ventana de 24h).
        try:
            await send_template(
                numero, "platim_producto_solicitado", "es", params_plantilla
            )
            enviado = True
        except Exception as e:  # noqa: BLE001
            print(f"[alerta_producto] plantilla falló a {numero}: {e}")
        # 2) Respaldo: texto libre (solo llega dentro de la ventana de 24h).
        if not enviado:
            try:
                await send_text(numero, aviso)
                enviado = True
            except Exception as e:  # noqa: BLE001
                print(f"[alerta_producto] texto falló a {numero}: {e}")
        wa_ok = wa_ok or enviado

    return json.dumps(
        {
            "ok": True,
            "email_enviado": email_ok,
            "whatsapp_enviado": wa_ok,
            "instruccion": (
                "Pídele al cliente que espere un momento mientras verificas "
                "disponibilidad en bodega y con la asesora Patricia, y que le "
                "confirmarás enseguida. NUNCA le des precio ni le digas de una "
                "que no lo tienes; NUNCA le pases el precio de la web como si "
                "fuera el nuestro."
            ),
        },
        ensure_ascii=False,
    )


@function_tool
def comparar_productos(
    ctx: RunContextWrapper[PlatimContext], codigos: list[str]
) -> str:
    """Genera una tabla comparativa entre productos por SKU.
    Usar cuando el cliente quiere elegir entre opciones similares."""
    estado = get_estado(ctx.context.jid)
    es_mayorista = estado.get("tipo_precio") == "mayoreo"
    comparacion = []
    for cod in codigos:
        p = catalogo.obtener(cod)
        if not p:
            comparacion.append({"codigo": cod, "error": "No existe en catálogo"})
            continue
        comparacion.append(
            {
                "codigo": p["codigo"],
                "nombre": p["nombre"],
                "material": p["material"],
                "uso": p["uso"],
                "tallas": p["tallas"],
                "colores": p["colores"],
                "marca": p["marca"],
                "precio": int(p.get("precio_publico") or 0),
                "precio_100_unidades": int(p.get("precio_volumen") or 0),
                "observaciones": p["observaciones"],
            }
        )
    return json.dumps({"comparacion": comparacion}, ensure_ascii=False)


@function_tool
async def enviar_catalogo_pdf(ctx: RunContextWrapper[PlatimContext]) -> str:
    """Genera el catálogo COMPLETO de productos en PDF y se lo envía al cliente
    por WhatsApp. Usar cuando el cliente pida la lista o el catálogo completo."""
    jid = ctx.context.jid
    estado = get_estado(jid)
    tipo = estado.get("tipo_precio", "publico")
    productos = catalogo.buscar("")  # todos los disponibles (excluye agotados)
    if not productos:
        return json.dumps({"error": "No hay productos disponibles."})

    from agent.pdf_service import generar_pdf_catalogo

    pdf = generar_pdf_catalogo(productos, tipo)
    try:
        from agent.whatsapp import send_document, upload_media

        media_id = await upload_media(pdf, "Catalogo_PLATIM.pdf", "application/pdf")
        caption = (
            f"📋 Catálogo PLATIM ({len(productos)} productos) — precios "
            + ("de mayoreo" if tipo == "mayoreo" else "al público")
        )
        await send_document(jid, media_id, "Catalogo_PLATIM.pdf", caption)
        return json.dumps({"ok": True, "productos": len(productos)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        print(f"Error enviando catálogo: {e}")
        return json.dumps({"error": "No se pudo enviar el catálogo por WhatsApp."})


@function_tool
def agregar_item_cotizacion(
    ctx: RunContextWrapper[PlatimContext],
    codigo: str,
    nombre: str,
    cantidad: int,
    precio: int,
) -> str:
    """Agrega un producto confirmado a la cotizacion.
    Llamar cada vez que el cliente confirme producto + cantidad."""
    jid = ctx.context.jid
    cantidad = max(1, int(cantidad))

    # Precios unitarios base del catalogo (publico y volumen 100+). El precio
    # efectivo lo calcula agregar_item_estado segun la cantidad FINAL.
    prod = catalogo.obtener(codigo)
    precio_publico = int((prod.get("precio_publico") if prod else 0) or 0)
    precio_volumen = int((prod.get("precio_volumen") if prod else 0) or 0)
    # Si el modelo pasó un precio válido y el producto no está en catálogo, se
    # respeta como precio público (caso raro de ítem fuera de catálogo).
    if not prod and precio and precio > 0:
        precio_publico = int(precio)

    item = {
        "codigo": codigo,
        "nombre": nombre or (prod["nombre"] if prod else codigo),
        "cantidad": cantidad,
        "precio_publico": precio_publico,
        "precio_volumen": precio_volumen,
        "precio": precio_publico,          # se recalcula en agregar_item_estado
        "subtotal": precio_publico * cantidad,
    }

    # Agregado ATÓMICO: leer-modificar-guardar bajo el candado de la DB para que
    # varias llamadas en paralelo (varios productos en un mensaje) no se pisen.
    res = agregar_item_estado(jid, item)
    return json.dumps(
        {"ok": True, "items": len(res["items"]), "total": res["total"]},
        ensure_ascii=False,
    )


@function_tool
def ver_cotizacion_actual(ctx: RunContextWrapper[PlatimContext]) -> str:
    """Muestra items y total parcial de la cotizacion en curso."""
    estado = get_estado(ctx.context.jid)
    items = estado.get("items", [])
    total = sum(i["subtotal"] for i in items)
    return json.dumps(
        {
            "items": items,
            "total": total,
            "tipo_precio": estado.get("tipo_precio", "publico"),
            "cliente": estado.get("cliente", {}),
        },
        ensure_ascii=False,
    )


@function_tool
def registrar_datos_cliente(
    ctx: RunContextWrapper[PlatimContext],
    nombre: str,
    empresa: str = "",
    email: str = "",
    telefono: str = "",
) -> str:
    """Registra el contacto del cliente. Todos los clientes son minoristas:
    la cotizacion SIEMPRE va al precio publico."""
    # Todos los clientes son minoristas: nunca se usan precios de mayoreo.
    es_mayorista = False
    jid = ctx.context.jid
    estado = get_estado(jid)
    cliente = estado.setdefault("cliente", {})
    if nombre:
        cliente["nombre"] = nombre
    if empresa:
        cliente["empresa"] = empresa
    if telefono:
        cliente["telefono"] = telefono

    # Validar el email: formato Y que el dominio pueda recibir correo.
    # Si algo falla, NO se guarda y se avisa para pedir otro.
    email_invalido = False
    email_motivo = ""
    if email:
        valido, email_motivo = validar_email_cliente(email)
        if valido:
            cliente["email"] = email.strip()
        else:
            email_invalido = True

    nuevo_tipo = "mayoreo" if es_mayorista else "publico"
    cambio_tipo = nuevo_tipo != estado.get("tipo_precio")
    estado["tipo_precio"] = nuevo_tipo

    # Recalcular items existentes respetando el precio por cantidad (volumen).
    if cambio_tipo:
        for it in estado["items"]:
            prod = catalogo.obtener(it["codigo"])
            if prod:
                it["precio_publico"] = int(prod.get("precio_publico") or 0)
                it["precio_volumen"] = int(prod.get("precio_volumen") or 0)
                it["precio"] = catalogo.precio_por_cantidad(prod, it["cantidad"])
                it["subtotal"] = it["precio"] * it["cantidad"]

    save_estado(jid, estado)

    # Persistir como lead.
    upsert_lead(
        jid,
        nombre=cliente.get("nombre"),
        empresa=cliente.get("empresa"),
        email=cliente.get("email"),
        telefono=cliente.get("telefono") or jid.split("@")[0],
        es_mayorista=es_mayorista,
    )

    resultado = {
        "ok": True,
        "cliente": cliente,
        "tipo_precio": estado["tipo_precio"],
        "email_invalido": email_invalido,
    }
    if email_invalido:
        if email_motivo == "dominio":
            resultado["aviso"] = (
                "El correo tiene buena forma pero su dominio no existe o no está "
                "activo (no puede recibir correo). NO se guardó. Dile al cliente "
                "que ese correo parece no estar activo y pídele otro válido."
            )
        else:
            resultado["aviso"] = (
                "El correo no tiene un formato válido y NO se guardó. Pídele al "
                "cliente que lo escriba de nuevo (ejemplo: nombre@dominio.com)."
            )
    return json.dumps(resultado, ensure_ascii=False)


@function_tool
async def generar_y_enviar_cotizacion(
    ctx: RunContextWrapper[PlatimContext],
    enviar_pdf_directo: bool = False,
) -> str:
    """ACCION FINAL: genera el PDF y lo pone a disposición del cliente.
    Por defecto envía por WhatsApp un BOTÓN con el link a la página donde el
    cliente ve y descarga su cotización (esto es lo normal). El email interno
    siempre sale con el PDF adjunto.

    enviar_pdf_directo: pásalo True SOLO si el cliente pide explícitamente que
    le mandes el ARCHIVO/PDF por WhatsApp; en ese caso además del link se le
    manda el documento PDF. Normalmente déjalo en False.

    Solo llamar cuando el cliente confirme y tenga datos de contacto
    (al menos nombre y email o telefono)."""
    jid = ctx.context.jid
    if es_prueba(jid):
        return json.dumps(
            {"ok": True, "modo_prueba": True,
             "mensaje": "(PRUEBA) Aquí generaría y enviaría la cotización; no se "
                        "envió nada real. Dile al cliente que ya quedó lista."},
            ensure_ascii=False,
        )
    estado = get_estado(jid)
    items = estado.get("items", [])
    cliente = estado.get("cliente", {})

    if not items:
        return json.dumps({"error": "No hay items en la cotizacion."})
    if not cliente.get("nombre"):
        return json.dumps(
            {"error": "Faltan datos del cliente. Pedir al menos nombre y email o telefono."}
        )

    total = sum(i["subtotal"] for i in items)
    tipo_precio = estado.get("tipo_precio", "publico")
    ts = _now()
    token = secrets.token_urlsafe(9)  # link público único por cliente

    # Guardar cotizacion en DB (genera el codigo).
    codigo = guardar_cotizacion(
        {
            "jid": jid,
            "nombre": cliente.get("nombre", ""),
            "empresa": cliente.get("empresa", ""),
            "email": cliente.get("email", ""),
            "telefono": cliente.get("telefono", "") or jid.split("@")[0],
            "tipo_precio": tipo_precio,
            "items": items,
            "total": total,
            "ts": ts,
            "token": token,
        }
    )

    cot_data = {
        "codigo": codigo,
        "nombre": cliente.get("nombre", ""),
        "empresa": cliente.get("empresa", ""),
        "email": cliente.get("email", ""),
        "telefono": cliente.get("telefono", "") or jid.split("@")[0],
        "tipo_precio": tipo_precio,
        "items": items,
        "total": total,
        "ts": ts,
        "token": token,
    }

    # 1. Generar PDF (siempre: para el email y por si el cliente pide el archivo).
    from agent.pdf_service import generar_pdf_cotizacion

    pdf_bytes = generar_pdf_cotizacion(cot_data)

    # 2. Enviar por WhatsApp el LINK a la página de "solicitud enviada".
    #    Al abrirla se dispara la conversión de Google Ads y desde ahí el
    #    cliente ve y descarga su cotización (por token). Este es el flujo normal.
    link = f"{COTIZACION_LANDING}?cot={token}"
    primer_nombre = (cliente.get("nombre", "") or "").split(" ")[0]
    wa_ok = False
    try:
        from agent.whatsapp import send_cta_button

        cuerpo = (
            f"¡Listo{(' ' + primer_nombre) if primer_nombre else ''}! 🎉 "
            f"Tu cotización *{codigo}* por {_moneda(total)} COP ya está lista.\n"
            f"Ábrela y descárgala en el siguiente botón 👇 (vigencia 30 días)"
        )
        await send_cta_button(jid, cuerpo, "Ver mi cotización 📄", link)
        wa_ok = True
    except Exception as e:  # noqa: BLE001
        print(f"Error enviando link cotización WA: {e}")
        try:
            from agent.whatsapp import send_text

            await send_text(
                jid,
                f"¡Listo! Tu cotización {codigo} por {_moneda(total)} COP: {link}",
            )
            wa_ok = True
        except Exception as e2:  # noqa: BLE001
            print(f"Error enviando fallback texto: {e2}")

    # 2b. Solo si el cliente pidió el ARCHIVO: además mandar el PDF adjunto.
    pdf_directo_ok = False
    if enviar_pdf_directo:
        try:
            from agent.whatsapp import send_document, upload_media

            media_id = await upload_media(pdf_bytes, f"Cotizacion_{codigo}.pdf")
            caption = (
                f"Cotización PLATIM {codigo}\n"
                f"Total: {_moneda(total)} COP\nVigencia: 30 días"
            )
            await send_document(
                jid, media_id, f"Cotizacion_PLATIM_{codigo}.pdf", caption
            )
            pdf_directo_ok = True
        except Exception as e:  # noqa: BLE001
            print(f"Error enviando PDF directo WA: {e}")

    # 3. Enviar email con PDF adjunto.
    email_ok = False
    email_error = ""
    correo = cliente.get("email", "")
    if not correo:
        email_error = "sin_email"
    elif not email_valido(correo):
        email_error = "email_invalido"
    else:
        try:
            from agent.email_service import enviar_cotizacion_email

            email_ok = await enviar_cotizacion_email(cot_data, pdf_bytes)
        except Exception as e:  # noqa: BLE001
            print(f"Error enviando email: {e}")
            import aiosmtplib

            if isinstance(e, aiosmtplib.SMTPRecipientsRefused):
                email_error = "email_rechazado"
            else:
                email_error = "error_envio"

    # Limpiar items de la cotizacion (se conservan cliente y tipo de precio).
    save_estado(jid, {"tipo_precio": tipo_precio, "items": [], "cliente": cliente})

    resultado = {
        "ok": True,
        "codigo": codigo,
        "total": total,
        "link_enviado": wa_ok,
        "link": link,
        "pdf_directo_enviado": pdf_directo_ok,
        "email_enviado": email_ok,
    }
    if not email_ok and email_error:
        avisos = {
            "sin_email": "No había un correo válido, así que la cotización solo se envió por WhatsApp. Si el cliente quiere copia por email, pídele un correo válido.",
            "email_invalido": "El correo del cliente no es válido; el email no se envió. Dile que su correo parece incorrecto y pídele que lo escriba de nuevo.",
            "email_rechazado": "El servidor de correo rechazó la dirección (no existe o está mal). Dile al cliente que su correo no es válido y pídele que lo escriba de nuevo.",
            "error_envio": "Hubo un problema técnico enviando el email; la cotización sí salió por WhatsApp.",
        }
        resultado["email_error"] = email_error
        resultado["aviso"] = avisos.get(email_error, "")
    return json.dumps(resultado, ensure_ascii=False)


@function_tool
def ver_disponibilidad_asesora(
    ctx: RunContextWrapper[PlatimContext], dias: int = 7
) -> str:
    """Muestra los próximos días y horarios LIBRES para una cita presencial con
    la asesora Patricia. Patricia atiende de lunes a viernes, de 2:00 a 4:00 PM
    (hora Colombia). Usar cuando el cliente pida hablar con un asesor/asesora o
    agendar una cita. Devuelve fechas concretas (YYYY-MM-DD) con sus horas
    disponibles para que el cliente elija."""
    dias = max(1, min(int(dias), 21))
    ahora = datetime.now(timezone.utc) - timedelta(hours=5)
    hoy = ahora.date()
    hhmm_ahora = ahora.strftime("%H:%M")
    disponibilidad = []
    for offset in range(0, dias + 1):
        d = hoy + timedelta(days=offset)
        if d.weekday() >= 5:  # sábado(5)/domingo(6): Patricia no atiende
            continue
        tomadas = horas_tomadas(d.isoformat())
        libres = [h for h in SLOTS_ASESORA if h not in tomadas]
        if d == hoy:  # hoy solo horas que aún no han pasado
            libres = [h for h in libres if h > hhmm_ahora]
        if libres:
            disponibilidad.append({
                "fecha": d.isoformat(),
                "dia": _fecha_es(d),
                "horarios": [
                    {"hora": h, "hora_legible": _HORA_LEGIBLE[h]} for h in libres
                ],
            })
        if len(disponibilidad) >= 5:
            break
    return json.dumps(
        {
            "asesora": ASESORA,
            "atencion": "lunes a viernes de 2:00 a 4:00 PM (hora Colombia)",
            "disponibilidad": disponibilidad,
        },
        ensure_ascii=False,
    )


@function_tool
async def agendar_cita_asesora(
    ctx: RunContextWrapper[PlatimContext],
    fecha: str,
    hora: str,
    nombre: str,
    email: str = "",
    telefono: str = "",
) -> str:
    """Agenda una cita presencial con la asesora Patricia en una fecha y hora
    concretas. 'fecha' en formato YYYY-MM-DD y 'hora' en formato de 24h de la
    lista disponible (14:00, 14:30, 15:00, 15:30). Validar SIEMPRE la
    disponibilidad con ver_disponibilidad_asesora antes de agendar. Requiere al
    menos el nombre del cliente."""
    jid = ctx.context.jid
    if es_prueba(jid):
        return json.dumps(
            {"ok": True, "modo_prueba": True,
             "mensaje": "(PRUEBA) Aquí agendaría la cita; no se envió ni guardó "
                        "nada real."},
            ensure_ascii=False,
        )

    # Validaciones de fecha/hora dentro de las reglas de atención.
    try:
        d = date.fromisoformat(fecha)
    except ValueError:
        return json.dumps({"error": "Fecha inválida. Usa formato YYYY-MM-DD."})
    if d < _hoy_colombia():
        return json.dumps({"error": "Esa fecha ya pasó. Ofrece una fecha futura."})
    if d.weekday() >= 5:
        return json.dumps({
            "error": "Patricia solo atiende de lunes a viernes. Ofrece un día hábil."
        })
    if hora not in SLOTS_ASESORA:
        return json.dumps({
            "error": "Hora fuera del horario. Solo 14:00, 14:30, 15:00 o 15:30 "
                     "(2:00 a 4:00 PM)."
        })
    ahora_col = datetime.now(timezone.utc) - timedelta(hours=5)
    if d == ahora_col.date() and hora <= ahora_col.strftime("%H:%M"):
        return json.dumps({
            "error": "Esa hora de hoy ya pasó. Ofrece un horario futuro."
        })
    if not nombre:
        return json.dumps({"error": "Falta el nombre del cliente para agendar."})

    # Validar email (si lo dieron) para poder enviar la confirmación.
    correo = ""
    email_invalido = False
    if email:
        valido, _ = validar_email_cliente(email)
        if valido:
            correo = email.strip()
        else:
            email_invalido = True

    from agent.email_service import enviar_cita_email

    # ¿El cliente YA tiene esta misma cita? (no chocar consigo mismo)
    propia = cita_existente(jid, fecha, hora)
    if propia is None and hora in horas_tomadas(fecha):
        return json.dumps({
            "error": "Ese horario ya está ocupado. Ofrece otro de los disponibles."
        })

    base_resp = {
        "ok": True,
        "asesora": ASESORA,
        "fecha": fecha,
        "dia": _fecha_es(d),
        "hora": hora,
        "hora_legible": _HORA_LEGIBLE.get(hora, hora),
        "email_invalido": email_invalido,
    }

    # Caso: ya estaba agendada -> si ahora dio un correo válido nuevo, se guarda
    # y se reenvía la confirmación (no se crea otra cita ni se marca "ocupado").
    if propia is not None:
        email_ok = False
        if correo and correo != (propia.get("email") or ""):
            actualizar_cita_email(propia["id"], correo)
            try:
                email_ok = await enviar_cita_email({**propia, "email": correo})
            except Exception as e:  # noqa: BLE001
                print(f"Error enviando email de cita: {e}")
        return json.dumps(
            {**base_resp, "cita_id": propia["id"], "ya_agendada": True,
             "email_enviado": email_ok},
            ensure_ascii=False,
        )

    # Caso: nueva cita.
    cita = {
        "jid": jid,
        "nombre": nombre,
        "email": correo,
        "telefono": telefono or jid.split("@")[0],
        "fecha": fecha,
        "hora": hora,
        "asesora": ASESORA,
    }
    cita_id = crear_cita(cita)
    email_ok = False
    try:
        email_ok = await enviar_cita_email(cita)
    except Exception as e:  # noqa: BLE001
        print(f"Error enviando email de cita: {e}")

    return json.dumps(
        {**base_resp, "cita_id": cita_id, "email_enviado": email_ok},
        ensure_ascii=False,
    )


@function_tool
def mis_citas_asesora(ctx: RunContextWrapper[PlatimContext]) -> str:
    """Lista las citas activas (no canceladas) del cliente con la asesora.
    Usar cuando el cliente pregunte qué citas tiene, o antes de cancelar si no
    se sabe cuál."""
    jid = ctx.context.jid
    citas = []
    for c in citas_de_cliente(jid):
        try:
            d = date.fromisoformat(c["fecha"])
            dia = _fecha_es(d)
        except ValueError:
            dia = c["fecha"]
        citas.append({
            "cita_id": c["id"],
            "fecha": c["fecha"],
            "dia": dia,
            "hora": c["hora"],
            "hora_legible": _HORA_LEGIBLE.get(c["hora"], c["hora"]),
            "asesora": c.get("asesora", ASESORA),
        })
    return json.dumps({"total": len(citas), "citas": citas}, ensure_ascii=False)


@function_tool
async def cancelar_cita_asesora(
    ctx: RunContextWrapper[PlatimContext], cita_id: int = 0
) -> str:
    """Cancela una cita del cliente con la asesora (libera el horario y notifica
    por correo). Si el cliente tiene UNA sola cita, se puede llamar sin cita_id.
    Si tiene varias, primero muestra sus citas con mis_citas_asesora y pide que
    indique cuál (usa el cita_id de esa lista)."""
    jid = ctx.context.jid
    activas = citas_de_cliente(jid)

    if not activas:
        return json.dumps({"error": "El cliente no tiene citas activas para cancelar."})
    if cita_id == 0:
        if len(activas) == 1:
            cita_id = activas[0]["id"]
        else:
            return json.dumps({
                "error": "El cliente tiene varias citas. Pídele cuál cancelar.",
                "citas": [
                    {"cita_id": c["id"], "fecha": c["fecha"], "hora": c["hora"]}
                    for c in activas
                ],
            })

    cancelada = cancelar_cita(cita_id, jid)
    if not cancelada:
        return json.dumps({"error": "No encontré esa cita a nombre del cliente."})

    email_ok = False
    try:
        from agent.email_service import enviar_cancelacion_email

        if cancelada.get("email"):
            email_ok = await enviar_cancelacion_email(cancelada)
    except Exception as e:  # noqa: BLE001
        print(f"Error enviando email de cancelación: {e}")

    try:
        d = date.fromisoformat(cancelada["fecha"])
        dia = _fecha_es(d)
    except ValueError:
        dia = cancelada["fecha"]

    return json.dumps(
        {
            "ok": True,
            "cancelada": True,
            "cita_id": cita_id,
            "dia": dia,
            "hora": cancelada["hora"],
            "hora_legible": _HORA_LEGIBLE.get(cancelada["hora"], cancelada["hora"]),
            "email_enviado": email_ok,
        },
        ensure_ascii=False,
    )


@function_tool
async def generar_link_pago(
    ctx: RunContextWrapper[PlatimContext], codigo: str = ""
) -> str:
    """Genera un link de pago (Mercado Pago) y le envía al cliente un botón
    'Pagar ahora' por WhatsApp. Usar cuando el cliente diga que quiere pagar.
    Si no se da 'codigo', usa la última cotización del cliente."""
    jid = ctx.context.jid
    cot = get_cotizacion(codigo) if codigo else ultima_cotizacion_de(jid)
    if not cot:
        return json.dumps({
            "error": "No hay una cotización para cobrar. Genera la cotización primero."
        })
    cot["jid"] = jid

    try:
        from agent.pagos_service import crear_link_pago

        res = await crear_link_pago(cot)
    except Exception as e:  # noqa: BLE001
        print(f"Error creando link de pago: {e}")
        return json.dumps({"error": "No se pudo generar el link de pago ahora."})

    url = res.get("url")
    if not url:
        return json.dumps({"error": "El proveedor de pagos no devolvió un link."})

    boton_ok = False
    try:
        from agent.whatsapp import send_cta_button

        await send_cta_button(
            jid,
            f"Ya puedes pagar tu cotización {cot['codigo']} por "
            f"{_moneda(cot['total'])} COP de forma segura 👇",
            "Pagar ahora",
            url,
        )
        boton_ok = True
    except Exception as e:  # noqa: BLE001
        print(f"Error enviando botón de pago: {e}")
        try:
            from agent.whatsapp import send_text

            await send_text(
                jid, f"Paga tu cotización {cot['codigo']} aquí: {url}"
            )
        except Exception:  # noqa: BLE001
            pass

    return json.dumps({
        "ok": True,
        "codigo": cot["codigo"],
        "total": cot["total"],
        "link": url,
        "boton_enviado": boton_ok,
    }, ensure_ascii=False)


@function_tool
def limpiar_cotizacion(ctx: RunContextWrapper[PlatimContext]) -> str:
    """Reinicia la cotizacion actual. Usar si el cliente quiere cambiar
    todo o empezar de nuevo. Conserva los datos de contacto ya registrados."""
    jid = ctx.context.jid
    estado = get_estado(jid)
    save_estado(
        jid,
        {
            "tipo_precio": estado.get("tipo_precio", "publico"),
            "items": [],
            "cliente": estado.get("cliente", {}),
        },
    )
    return json.dumps({"ok": True, "mensaje": "Cotización reiniciada."})


# ── System prompt ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente virtual de PLATIM, empresa colombiana
especializada en dotaciones industriales y equipos de proteccion personal (EPP)
ubicada en Palmira, Valle del Cauca.

Eres un ASESOR COMERCIAL CONSULTIVO, no un cotizador automatico. Tu funcion
principal es COMPRENDER la necesidad real del cliente ANTES de ofrecer productos
o generar una cotizacion. Atiendes con cortesia, respeto y un tono profesional
pero amigable en todas las conversaciones.

FILOSOFIA (el orden nunca se invierte):
  PRIMERO comprender. LUEGO asesorar. FINALMENTE cotizar.

===== APERTURA DE LA CONVERSACION =====
En tu PRIMER mensaje: saluda mencionando PLATIM, preséntate como su asistente y
PREGUNTA EL NOMBRE del cliente. Usa su nombre durante la charla.
- Averigua de forma natural si ya es cliente de PLATIM (ej. "¿ya has comprado con
  nosotros o es tu primera vez?").
- Si es su PRIMERA vez: dale una idea breve de qué es PLATIM (dotaciones
  industriales y EPP) y sus líneas destacadas (uniformes, calzado de seguridad,
  protección para cabeza/ojos/manos/respiratoria), y pregúntale en qué lo ayudas.
- Si YA es cliente: salúdalo con cercanía y pregúntale en qué puede ayudarlo hoy.
- Respeta siempre la regla de 1-2 preguntas por mensaje (no acumules preguntas).

===== IMÁGENES Y ARCHIVOS (visión y PDF) =====
El cliente PUEDE enviarte FOTOS (de un producto, una prenda, una talla/etiqueta,
un logo para bordado) o archivos PDF (una orden de compra, una lista de
requerimientos). Cuando recibas una imagen o un PDF:
- Describe brevemente lo que ves/lees y CONFIRMA con el cliente antes de asumir.
  No inventes datos que no se distingan con claridad (pídele que aclare).
- Si es un producto, identifícalo y usa buscar_productos para ofrecer lo que
  PLATIM tiene igual o similar (recuerda: manejamos dotaciones y EPP).
- Si es un logo o diseño para marcar prendas, dile que se puede personalizar
  (Accesorios: bordado/logo) y sigue con la asesoría.
- Si es un PDF con una lista de productos y cantidades, léelo, RESUME lo que
  entendiste (productos + cantidades) y pídele confirmación antes de cotizar.

===== ETAPA 1: DESCUBRIMIENTO (antes de recomendar o cotizar) =====
Antes de recomendar o cotizar CUALQUIER producto, entiende primero:
- Que necesita realmente el cliente y para que lo va a usar
- Que problema quiere resolver o que objetivo busca con la compra
- Si ya sabe exactamente el producto que quiere, o necesita asesoria
- El contexto: tipo de trabajo, riesgos, cantidad de personas, empresa o personal

Si el cliente llega con una peticion GENERAL o AMBIGUA como "quiero una cotizacion",
"necesito uniformes", "necesito dotacion" o "una solucion para mi empresa", NO
respondas con precios ni con una lista de productos: INICIA UNA CONVERSACION para
entender el requerimiento. Ejemplo: pregunta para que area es, cuantas personas,
que tipo de trabajo hacen.

===== NUNCA SUPONER =====
No asumas NADA que el cliente no haya dicho: ni productos, ni cantidades, ni
tallas, ni caracteristicas, ni personalizaciones (logos/bordados), ni servicios
adicionales. Toda informacion relevante se CONFIRMA con el cliente antes de armar
una propuesta.

===== CONVERSACION GUIADA (experiencia natural) =====
- Conduce la charla con preguntas PROGRESIVAS: cada respuesta del cliente decide
  la siguiente pregunta. NO pidas toda la informacion de una sola vez.
- Haz solo UNA o DOS preguntas por mensaje. Nada de interrogatorios largos.
- Explica brevemente POR QUE pides cada dato (ej. "para recomendarte la bota
  correcta, cuentame en que superficie trabajan").
- Manten un tono cercano y natural. El flujo de preguntas se ADAPTA al tipo de
  cliente y de producto: no todas las solicitudes necesitan las mismas preguntas.

===== ASESORAR ANTES QUE COTIZAR =====
Cuando el cliente NO tenga claro que necesita, actua como asesor: comprende el
contexto, recomienda alternativas, explica las diferencias entre opciones y
ayudalo a elegir la solucion mas adecuada. La cotizacion es el RESULTADO de la
asesoria, no el punto de partida.

===== INFORMACION MINIMA ANTES DE COTIZAR =====
Antes de generar una cotizacion valida que tienes TODO lo necesario para ese caso
(productos, cantidades, tallas/caracteristicas si aplican, personalizaciones). Si
falta algo, sigue preguntando hasta completarlo. NUNCA cotices con datos incompletos.

===== VALIDACION ANTES DE CALCULAR =====
Antes de calcular precios, RESUME lo que entendiste y pide confirmacion cuando
haya cualquier ambiguedad: productos solicitados, cantidades, personalizaciones,
caracteristicas especiales y observaciones. Solo DESPUES de que el cliente confirme,
genera la cotizacion. Antes de entregarla verifica que: la info esta completa, no
hay contradicciones, NO incluye productos que el cliente no pidio, y la propuesta
corresponde a lo que el cliente expreso.

===== PERSONALIZACION DE LA OFERTA =====
La propuesta se arma SOLO con los productos/servicios que el cliente confirmo
necesitar. NUNCA incluyas automaticamente todo el catalogo ni productos no pedidos.

===== HERRAMIENTAS Y MECANICA =====
REGLAS:
- TODOS los clientes son minoristas: SIEMPRE cotiza al precio público. NUNCA
  preguntes si es minorista o mayorista, ni menciones precios de mayoreo.
- PRECIO POR VOLUMEN (100+ unidades): si un producto trae "precio_100_unidades"
  mayor que 0, ese es el precio unitario cuando el cliente lleva 100 o más de
  ESE mismo producto. Úsalo como incentivo: cuando el cliente pida cantidades
  altas (cerca de 100) o pregunte por descuentos, cuéntale que "a partir de 100
  unidades el precio por unidad baja a $X". El sistema aplica ese precio SOLO y
  automáticamente cuando la cantidad del producto llega a 100; por debajo de 100
  es el precio público. No lo apliques manualmente ni lo ofrezcas para productos
  cuyo precio_100_unidades sea 0.
- SIEMPRE usa buscar_productos antes de mencionar productos. Al recomendar un
  producto, da su NOMBRE, PRECIO y una breve descripción de su uso/beneficio,
  usando SOLO los datos que devuelve la herramienta.
- SOLO INFORMACIÓN OFICIAL: nunca inventes fotos, links de compra, marcas,
  especificaciones técnicas, existencias ni datos que la herramienta no te dé. Si
  no tienes un dato, dilo con honestidad y ofrece confirmarlo con un asesor.
- HONESTIDAD DE CATÁLOGO: nunca inventes productos, precios ni existencias. Si el
  cliente pregunta por o pide algo que NO aparece en buscar_productos, PRIMERO,
  si hay algo realmente similar en el catálogo, ofréceselo; y SIEMPRE corre el
  flujo "PRODUCTO NO DISPONIBLE" de abajo para dejar registrada su solicitud.
- PRODUCTO NO DISPONIBLE — REGLA CLAVE. Se activa SIEMPRE que el cliente
  PREGUNTE por o PIDA un producto que NO aparece en buscar_productos, AUNQUE sea
  solo una pregunta tipo "¿tienen taladros?" y AUNQUE sea algo que normalmente no
  manejamos (herramientas, etc.). NO respondas "no lo tenemos" y NO lo transfieras
  a un agente humano por no tener el producto: en su lugar haz EXACTAMENTE esto,
  en este orden:
  1) Dile al cliente que ESPERE un momento mientras verificas la disponibilidad
     en bodega y con la asesora, y que le confirmas enseguida (ej: "Permíteme un
     momento, estoy verificando la disponibilidad de eso en bodega y con nuestra
     asesora 🔎. Te confirmo enseguida."). NO le digas de una que no lo tienes,
     NI le des precios, NI lo mandes con un humano.
  2) Usa la búsqueda web SOLO para encontrar una REFERENCIA de ese producto
     (marca/modelo típico, características y un precio referencial de mercado)
     que le sirva a Patricia para decidir. No uses la web para nada más.
  3) Llama a reportar_producto_no_encontrado con 'descripcion' (lo que pidió el
     cliente) y 'referencia_web' (lo que encontraste). Eso avisa a Patricia por
     correo y WhatsApp. Hazlo UNA sola vez por producto faltante.
  NUNCA inventes precio ni existencias, y NUNCA le pases al cliente el precio de
  la web como si fuera el nuestro: el precio real lo confirma Patricia. La única
  excepción para NO correr este flujo es que el cliente EXPLÍCITAMENTE pida hablar
  con una persona (ahí sí usa AGENTE HUMANO).
- FOTOS: si buscar_productos marca "tiene_foto": true en un producto, envíasela
  al cliente con enviar_fotos_productos (pásale los códigos). Hazlo cuando
  RECOMIENDES ese producto y también al mostrar el RESUMEN de la cotización, para
  que vea lo que va a comprar. No envíes fotos de productos sin foto (no las hay).
- Si el cliente pide la LISTA o el CATÁLOGO completo, usa enviar_catalogo_pdf
  para mandarle el PDF con todos los productos
- Cuando el cliente confirme cantidad de un producto, usa agregar_item_cotizacion
- Si el cliente pide VARIOS productos en un mensaje (ej. "3 botas, 1 gafa y un
  uniforme"), agrégalos TODOS: una llamada a agregar_item_cotizacion por CADA
  producto. No dejes ninguno por fuera.
- Si el cliente dice "escógelos tú" o te pide elegir, PRIMERO entiende el contexto
  (para qué área, qué riesgo, cuántas personas); luego busca en el catálogo, elige
  productos concretos que encajen y agrégalos uno por uno con agregar_item_cotizacion.
- ANTES de generar la cotización o el link de pago, usa ver_cotizacion_actual,
  muéstrale al cliente el RESUMEN de TODOS los productos y el total, y pide su
  confirmación. Verifica que estén TODOS los que pidió y NINGUNO que no pidió.
- NUNCA generes la cotización ni cobres si faltan productos que el cliente pidió.
  Si algo falla al agregar, reintenta agregar_item_cotizacion; no continúes con
  una cotización incompleta.
- Cuando tengas nombre + (email o telefono), usa registrar_datos_cliente
- VALIDA EL CORREO: si registrar_datos_cliente devuelve "email_invalido": true,
  usa el texto de "aviso" para decirle al cliente que su correo está mal escrito
  o no está activo, y pídele que te lo envíe de nuevo. NO continúes ni generes la
  cotización por email hasta tener un correo válido
- Si generar_y_enviar_cotizacion devuelve "email_error", avísale al cliente que
  su correo no es válido o no está activo y pídele que lo escriba de nuevo; el
  resto de la cotización (WhatsApp) sí se envió
- Solo usa generar_y_enviar_cotizacion cuando el cliente lo confirme
- ENVÍO DE LA COTIZACIÓN: por defecto el bot le manda al cliente un BOTÓN con el
  link donde ve y descarga su cotización (NO mandes el PDF por defecto). Después
  de llamar generar_y_enviar_cotizacion, dile algo como "Te acabo de enviar el
  botón para ver y descargar tu cotización 👆". NO pegues tú el link en el texto:
  el botón ya se envió solo.
- Solo si el cliente pide EXPLÍCITAMENTE el archivo/PDF ("mándame el PDF",
  "quiero el archivo", "envíame el documento"), llama generar_y_enviar_cotizacion
  con enviar_pdf_directo=true para que además le llegue el PDF adjunto.
- PAGOS: si el cliente dice que quiere PAGAR, PRIMERO verifica con
  ver_cotizacion_actual que la cotización tenga TODOS los productos y el total
  correcto; luego usa generar_link_pago para enviarle el botón de pago (Mercado
  Pago). Confírmale que le llegó el botón "Pagar ahora" y que el pago es seguro.
  Nunca cobres una cotización a la que le falten productos.
- Muestra precios como: $85.000 COP
- FORMATO WHATSAPP (NO uses Markdown): la negrita es con UN SOLO asterisco
  *así*, NUNCA con doble **así**. La cursiva es con guion bajo _así_. NO uses
  encabezados con # ni ##, NO uses ### ni negritas dobles, NO uses enlaces
  [texto](url). Para listas usa "- " o "1. " y para resaltar precios/nombres
  usa un solo asterisco: *Bota puntera acero* - *$240.000 COP*
- Mensajes cortos y directos (es WhatsApp, no email)
- Si el cliente pide algo que no tenemos, sugiere alternativas similares
- Si hay riesgo especifico (altura, electrico, quimico), recomienda EPP adecuado

CITAS CON ASESORA (Patricia):
- Si el cliente pide hablar con un asesor/asesora, atención personalizada o
  agendar una cita, ofrécele una cita presencial con la asesora *Patricia*
- Patricia atiende SOLO de lunes a viernes, de 2:00 a 4:00 PM (hora Colombia)
- Usa ver_disponibilidad_asesora para mostrar días y horas libres; muestra las
  opciones y deja que el cliente elija una
- ANTES de agendar, pide el NOMBRE y el CORREO del cliente (el correo es para
  enviarle la confirmación). Solo agenda cuando ya tengas ambos
- Usa agendar_cita_asesora con la fecha (YYYY-MM-DD) y hora exactas que elija
- Al confirmar, dile la fecha y hora en palabras (ej. "martes 8 de julio a las
  2:30 PM") y que le llegará copia al correo
- Si el cliente da el correo DESPUÉS de agendar, vuelve a llamar
  agendar_cita_asesora con la misma fecha/hora y el correo: el sistema
  reconoce que es su cita y solo le adjunta el correo (no la duplica)
- Si email_invalido is true, pídele un correo válido; la cita igual queda hecha
- Si el cliente pregunta qué citas tiene, usa mis_citas_asesora
- Si quiere CANCELAR, usa cancelar_cita_asesora. Si tiene una sola cita puedes
  cancelar directo; si tiene varias, muéstraselas y pregunta cuál (usa el
  cita_id). Confírmale la cancelación con la fecha y hora
- Para REPROGRAMAR: primero cancela con cancelar_cita_asesora y luego agenda la
  nueva con ver_disponibilidad_asesora + agendar_cita_asesora

PREGUNTAS FRECUENTES (envíos, garantías, devoluciones, existencias):
- Responde con información OFICIAL de PLATIM. Si NO tienes la política exacta
  cargada, NO la inventes: dile al cliente que un asesor se la confirma y ofrécele
  el contacto humano (ver AGENTE HUMANO). Nunca prometas plazos, coberturas ni
  condiciones que no puedas respaldar.
- Existencias/stock: guíate por lo que devuelve buscar_productos. Si un producto
  no aparece o está agotado, ofrece alternativas similares del catálogo y corre
  el flujo PRODUCTO NO DISPONIBLE (no lo mandes con un humano por eso).
- Dudas técnicas muy complejas o casos especiales: sugiere hablar con un agente
  humano (soporte) para una asesoría más detallada. (Ojo: que un producto no esté
  en el catálogo NO es un caso para humano; para eso está PRODUCTO NO DISPONIBLE.)

AGENTE HUMANO (transferencia a un representante por WhatsApp):
- Úsalo SOLO cuando el cliente EXPLÍCITAMENTE pida hablar con una persona, o para
  quejas/reclamos. NUNCA lo uses solo porque no tengamos un producto (eso va por
  el flujo PRODUCTO NO DISPONIBLE).
- Si el cliente PIDE hablar con una persona/agente/representante, responde con
  amabilidad: "Por supuesto, permíteme transferirte con uno de nuestros
  representantes vía WhatsApp".
- Envíale este enlace (mándalo como texto plano, NO como enlace con corchetes):
  https://wa.me/573188940939
  y dile: "Haz clic en el enlace para chatear con un agente; cuéntale tu consulta
  o requerimiento para que te asista mejor".
- Si la consulta es sobre un PRODUCTO específico, arma el enlace con el mensaje
  precargado (URL-encoded), por ejemplo:
  https://wa.me/573188940939?text=Estoy%20interesado%20en%20las%20botas%20de%20seguridad
- Agradécele su paciencia y confírmale que un representante lo atenderá a la
  brevedad. (Esto es distinto a una CITA con Patricia: el agente humano es
  atención inmediata por WhatsApp; Patricia es una cita presencial agendada.)

CIERRE DE LA CONVERSACION:
- Pregunta si tiene alguna otra duda y ofrécele ayuda adicional.
- Pídele una breve retroalimentación sobre cómo te fue en la atención (para
  mejorar).
- Invítalo a seguir a PLATIM en redes para novedades:
  Instagram: https://www.instagram.com/dotaindustriaplatim/
  Facebook: https://www.facebook.com/dotaindustriaplatim04

RESTRICCIONES (no negociables):
- NO ofrezcas promociones, descuentos ni información que no esté respaldada
  oficialmente por PLATIM.
- NO hagas recomendaciones inseguras o que puedan poner en riesgo al cliente;
  ante un riesgo, recomienda el EPP adecuado.
- Mantén la conversación dentro del contexto de los productos y servicios de
  PLATIM.

CATEGORIAS DEL CATALOGO:
Uniformes, Buzos/Overoles, Pantalones, Alta visibilidad,
Protección de cabeza, Protección ocular, Protección respiratoria,
Protección auditiva, Protección manos, Protección corporal,
Calzado de seguridad, Seguridad en altura, Señalización,
Primeros auxilios, Emergencias, Accesorios"""


# ── Construccion del agente ──────────────────────────────────────────────

platim_agent = Agent[PlatimContext](
    name="PLATIM Asistente",
    instructions=SYSTEM_PROMPT,
    model=MODEL,
    tools=[
        buscar_productos,
        enviar_fotos_productos,
        reportar_producto_no_encontrado,
        WebSearchTool(),
        comparar_productos,
        enviar_catalogo_pdf,
        agregar_item_cotizacion,
        ver_cotizacion_actual,
        registrar_datos_cliente,
        generar_y_enviar_cotizacion,
        ver_disponibilidad_asesora,
        agendar_cita_asesora,
        mis_citas_asesora,
        cancelar_cita_asesora,
        generar_link_pago,
        limpiar_cotizacion,
    ],
)


def _get_sesion(jid: str) -> SQLiteSession:
    if jid not in _sesiones:
        _sesiones[jid] = SQLiteSession(jid, SESSIONS_DB)
    return _sesiones[jid]


async def procesar_mensaje(
    jid: str,
    texto: str,
    registrar_in: bool = True,
    adjuntos: list | None = None,
) -> str:
    """Corre el agente para un mensaje entrante y devuelve la respuesta.

    Mantiene la memoria de la conversacion por jid mediante SQLiteSession.
    registrar_in: si es False no registra el mensaje entrante (lo hace quien llama,
    p.ej. el webhook cuando agrupa varios mensajes).
    adjuntos: lista de items multimodales (imagen/PDF) para que el agente los
    "vea"/lea junto con el texto (visión y lectura de documentos).
    """
    if registrar_in:
        registrar_mensaje(jid, "in", texto)
    ctx = PlatimContext(jid=jid)
    sesion = _get_sesion(jid)

    # Si vienen adjuntos (imagen/PDF) armamos un mensaje multimodal; si no, texto.
    if adjuntos:
        contenido: list = []
        if texto:
            contenido.append({"type": "input_text", "text": texto})
        contenido.extend(adjuntos)
        entrada: object = [{"role": "user", "content": contenido}]
    else:
        entrada = texto

    result = await Runner.run(
        platim_agent,
        entrada,
        context=ctx,
        session=sesion,
    )
    respuesta = (result.final_output or "").strip()
    respuesta = formatear_whatsapp(respuesta)
    if respuesta:
        registrar_mensaje(jid, "out", respuesta)
    return respuesta


# ── Chat de PRUEBA del dashboard (agente de cliente, sin efectos reales) ──
_sesiones_prueba: dict[str, SQLiteSession] = {}


async def procesar_mensaje_prueba(texto: str, sesion_id: str = "dashboard") -> str:
    """Corre el agente de CLIENTE en modo prueba (chat del dashboard): responde
    igual que con un cliente real, pero SIN efectos externos (no envía WhatsApp
    ni correo, no avisa a Patricia) y sin registrar nada en el historial."""
    jid = f"{PRUEBA_PREFIX}{sesion_id}"
    ctx = PlatimContext(jid=jid)
    if jid not in _sesiones_prueba:
        _sesiones_prueba[jid] = SQLiteSession(jid, SESSIONS_DB)
    sesion = _sesiones_prueba[jid]
    result = await Runner.run(platim_agent, texto, context=ctx, session=sesion)
    return formatear_whatsapp((result.final_output or "").strip())


async def reiniciar_prueba(sesion_id: str = "dashboard") -> None:
    """Borra la memoria y la cotización en curso del chat de prueba."""
    jid = f"{PRUEBA_PREFIX}{sesion_id}"
    ses = _sesiones_prueba.pop(jid, None)
    if ses is not None:
        try:
            await ses.clear_session()
        except Exception:  # noqa: BLE001
            pass
    save_estado(jid, {"items": [], "cliente": {}, "tipo_precio": "publico"})


# ── Modo administrador: gestión del catálogo por WhatsApp ────────────────
ADMIN_FOTOS_DIR = os.path.join(os.path.dirname(DB_PATH), "fotos")


@function_tool
def admin_listar_solicitudes(ctx: RunContextWrapper[PlatimContext]) -> str:
    """Lista las solicitudes de 'producto no encontrado' que están PENDIENTES
    (las que generó el flujo cuando un cliente pidió algo fuera del catálogo)."""
    from agent.db import listar_solicitudes_producto

    pendientes = [
        s for s in listar_solicitudes_producto(100) if s.get("estado") == "pendiente"
    ]
    salida = [
        {
            "id": s["id"],
            "cliente": s.get("nombre") or s.get("telefono"),
            "pidio": s.get("descripcion"),
            "referencia": s.get("referencia"),
        }
        for s in pendientes
    ]
    return json.dumps(
        {"pendientes": len(salida), "solicitudes": salida}, ensure_ascii=False
    )


@function_tool
def admin_agregar_producto(
    ctx: RunContextWrapper[PlatimContext],
    nombre: str,
    precio_publico: int,
    categoria: str = "General",
    precio_volumen: int = 0,
    descripcion: str = "",
    uso: str = "",
) -> str:
    """Crea un producto NUEVO en el catálogo (disponible al instante para los
    clientes). 'precio_volumen' es opcional (precio a 100+ unidades del mismo
    producto)."""
    from agent.db import crear_producto

    if not (nombre or "").strip():
        return json.dumps({"error": "Falta el nombre del producto."}, ensure_ascii=False)
    if not precio_publico or int(precio_publico) <= 0:
        return json.dumps(
            {"error": "Falta un precio público válido."}, ensure_ascii=False
        )
    codigo = crear_producto(
        {
            "nombre": nombre.strip(),
            "categoria": categoria or "General",
            "precio_publico": int(precio_publico),
            "precio_volumen": int(precio_volumen or 0),
            "descripcion": descripcion or "",
            "uso": uso or "",
        }
    )
    return json.dumps(
        {
            "ok": True,
            "codigo": codigo,
            "nombre": nombre.strip(),
            "precio_publico": int(precio_publico),
            "sugerir_foto": (
                "Quedó creado SIN foto. Sugiérele al admin ponerle una: si "
                "encuentras en la web un enlace de imagen del producto, "
                "propóneselo y usa admin_poner_foto; o que la suba en el "
                "dashboard."
            ),
        },
        ensure_ascii=False,
    )


@function_tool
def admin_autorizar_solicitud(
    ctx: RunContextWrapper[PlatimContext],
    id: int,
    precio_publico: int,
    precio_volumen: int = 0,
    categoria: str = "General",
    nombre: str = "",
) -> str:
    """Autoriza una solicitud PENDIENTE: crea el producto con lo que pidió el
    cliente y el precio que da el admin, y marca la solicitud como 'agregado'.
    Usa admin_listar_solicitudes para ver los IDs. Si 'nombre' viene vacío, usa
    la descripción de la solicitud como nombre del producto."""
    from agent.db import crear_producto, get_solicitud, marcar_solicitud

    sol = get_solicitud(int(id))
    if not sol:
        return json.dumps({"error": f"No existe la solicitud #{id}."}, ensure_ascii=False)
    if not precio_publico or int(precio_publico) <= 0:
        return json.dumps(
            {"error": "Falta un precio público válido."}, ensure_ascii=False
        )
    nombre_final = (nombre or sol.get("descripcion") or "Producto").strip()
    codigo = crear_producto(
        {
            "nombre": nombre_final,
            "categoria": categoria or "General",
            "precio_publico": int(precio_publico),
            "precio_volumen": int(precio_volumen or 0),
            "descripcion": sol.get("referencia") or "",
        }
    )
    marcar_solicitud(int(id), "agregado")
    return json.dumps(
        {
            "ok": True,
            "codigo": codigo,
            "nombre": nombre_final,
            "solicitud": int(id),
            "sugerir_foto": (
                "Quedó creado SIN foto. Sugiere ponerle una: si hallas en la web "
                "un enlace de imagen, propóneselo y usa admin_poner_foto; o que "
                "la suba en el dashboard."
            ),
        },
        ensure_ascii=False,
    )


@function_tool
def admin_descartar_solicitud(
    ctx: RunContextWrapper[PlatimContext], id: int
) -> str:
    """Marca una solicitud pendiente como 'descartado' (no se va a agregar)."""
    from agent.db import get_solicitud, marcar_solicitud

    if not get_solicitud(int(id)):
        return json.dumps({"error": f"No existe la solicitud #{id}."}, ensure_ascii=False)
    marcar_solicitud(int(id), "descartado")
    return json.dumps(
        {"ok": True, "solicitud": int(id), "estado": "descartado"}, ensure_ascii=False
    )


@function_tool
def admin_buscar_catalogo(
    ctx: RunContextWrapper[PlatimContext], query: str
) -> str:
    """Busca productos en el catálogo (para encontrar el CÓDIGO y precios antes
    de editar). Devuelve código, nombre, categoría, precio público, precio 100+ y
    si está sin stock."""
    res = catalogo.buscar(query, incluir_sin_stock=True)[:15]
    salida = [
        {
            "codigo": p["codigo"],
            "nombre": p["nombre"],
            "categoria": p["categoria"],
            "precio_publico": int(p.get("precio_publico") or 0),
            "precio_100_unidades": int(p.get("precio_volumen") or 0),
            "sin_stock": bool(p.get("sin_stock")),
        }
        for p in res
    ]
    return json.dumps(
        {"encontrados": len(salida), "productos": salida}, ensure_ascii=False
    )


@function_tool
def admin_editar_producto(
    ctx: RunContextWrapper[PlatimContext],
    codigo: str,
    precio_publico: int = 0,
    precio_volumen: int = -1,
    nombre: str = "",
    stock: str = "",
) -> str:
    """Edita un producto EXISTENTE del catálogo. Cambia solo lo que indiques:
    'precio_publico' (>0), 'precio_volumen' (precio 100+; usa 0 para quitar el
    descuento, -1 = no cambiar), 'nombre', y 'stock' = "agotado" o "disponible".
    Usa admin_buscar_catalogo para hallar el código."""
    from agent.db import set_override

    codigo = (codigo or "").strip().upper()
    if not catalogo.obtener(codigo):
        return json.dumps({"error": f"No existe el producto {codigo}."}, ensure_ascii=False)
    campos = {}
    if precio_publico and int(precio_publico) > 0:
        campos["precio_publico"] = int(precio_publico)
    if precio_volumen is not None and int(precio_volumen) >= 0:
        campos["precio_volumen"] = int(precio_volumen)
    if nombre and nombre.strip():
        campos["nombre"] = nombre.strip()
    if stock:
        s = stock.strip().lower()
        if s in ("agotado", "sin stock", "no"):
            campos["sin_stock"] = 1
        elif s in ("disponible", "en stock", "si", "sí"):
            campos["sin_stock"] = 0
    if not campos:
        return json.dumps(
            {"error": "No indicaste qué cambiar."}, ensure_ascii=False
        )
    set_override(codigo, campos)
    p = catalogo.obtener(codigo)
    return json.dumps(
        {
            "ok": True,
            "codigo": codigo,
            "cambios": campos,
            "ahora": {
                "nombre": p["nombre"],
                "precio_publico": int(p.get("precio_publico") or 0),
                "precio_100_unidades": int(p.get("precio_volumen") or 0),
                "sin_stock": bool(p.get("sin_stock")),
            },
        },
        ensure_ascii=False,
    )


@function_tool
async def admin_poner_foto(
    ctx: RunContextWrapper[PlatimContext], codigo: str, url: str
) -> str:
    """Pone la foto de un producto a partir del ENLACE de una imagen: la
    descarga, la convierte a JPEG y la deja lista para que el bot la envíe a los
    clientes. El enlace debe apuntar a una imagen (jpg/png/webp)."""
    import httpx

    from agent.db import set_foto
    from agent.imagen_service import a_jpeg

    codigo = (codigo or "").strip().upper()
    if not catalogo.obtener(codigo):
        return json.dumps({"error": f"No existe el producto {codigo}."}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as cli:
            r = await cli.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            data = r.content
    except Exception as e:  # noqa: BLE001
        return json.dumps(
            {"error": f"No pude descargar la imagen: {e}"}, ensure_ascii=False
        )
    if len(data) > 8 * 1024 * 1024:
        return json.dumps({"error": "La imagen es muy grande (máx 8MB)."}, ensure_ascii=False)
    jpeg = a_jpeg(data)
    if not jpeg:
        return json.dumps(
            {"error": "El enlace no era una imagen válida. Pide otro enlace o "
                      "súbela en el dashboard."},
            ensure_ascii=False,
        )
    os.makedirs(ADMIN_FOTOS_DIR, exist_ok=True)
    archivo = f"{codigo}.jpg"
    with open(os.path.join(ADMIN_FOTOS_DIR, archivo), "wb") as f:
        f.write(jpeg)
    set_foto(codigo, archivo)
    return json.dumps({"ok": True, "codigo": codigo, "foto": "puesta"}, ensure_ascii=False)


ADMIN_PROMPT = """Eres el asistente ADMINISTRATIVO interno de PLATIM por WhatsApp.
Hablas con el equipo (Patricia o Eathan), NO con clientes. Ayúdales a gestionar
el catálogo rápido y sin vueltas.

PUEDES:
- Mostrar las solicitudes de "producto no encontrado" PENDIENTES (admin_listar_solicitudes).
- AUTORIZAR una solicitud dándole precio (admin_autorizar_solicitud): crea el
  producto y la marca agregada. Si no dieron precio, pídelo.
- AGREGAR un producto nuevo cualquiera (admin_agregar_producto) con nombre y precio.
- EDITAR un producto existente (admin_editar_producto): cambiar precio público,
  precio 100+, nombre o marcarlo agotado/disponible. Usa admin_buscar_catalogo
  para encontrar el código.
- BUSCAR en el catálogo (admin_buscar_catalogo) por nombre/código.
- DESCARTAR una solicitud (admin_descartar_solicitud).
- Poner FOTO a un producto desde un enlace de imagen (admin_poner_foto).
- INVESTIGAR en la web (búsqueda web): características, precios de mercado,
  marcas/modelos y productos SIMILARES de cualquier producto.

REGLAS:
- Sé breve y directo (es chat de trabajo). Confirma lo hecho con el código del producto.
- Interpreta órdenes naturales:
  "autoriza la 12 a 180000" -> admin_autorizar_solicitud(id=12, precio_publico=180000).
  "agrega Taladro Bosch a 180000, 100+ a 165000" -> admin_agregar_producto(nombre="Taladro Bosch", precio_publico=180000, precio_volumen=165000).
  "cambia el precio de la SST-027 a 160000" -> admin_editar_producto(codigo="SST-027", precio_publico=160000).
  "marca agotada la UNF-001" -> admin_editar_producto(codigo="UNF-001", stock="agotado").
- Precios en pesos colombianos: "180 mil" / "180000" / "$180.000" = 180000.
- INVESTIGACIÓN antes de fijar precio: cuando vayas a agregar/autorizar un producto
  o el admin pida ayuda con el precio, USA la búsqueda web para estudiar ese
  producto Y varios SIMILARES (marcas/modelos típicos, características y precios de
  mercado en Colombia si es posible). Presenta un breve resumen y un PRECIO o RANGO
  de referencia para que el admin decida. NO fijes precio tú solo sin que el admin
  confirme, salvo que ya te haya dado el precio.
- Después de crear un producto SIEMPRE sugiere ponerle FOTO. Si puedes, busca en la
  web un enlace de imagen real del producto y propóneselo; si el admin acepta o te
  pasa un enlace, usa admin_poner_foto. Si el enlace falla, diles que la suban en
  el dashboard (🏷️ Productos).
- Si te piden algo fuera de esto (cotizar, atender a un cliente), aclara que aquí
  es solo gestión interna del catálogo.
"""

admin_agent = Agent[PlatimContext](
    name="PLATIM Admin",
    instructions=ADMIN_PROMPT,
    model=MODEL,
    tools=[
        admin_listar_solicitudes,
        admin_autorizar_solicitud,
        admin_agregar_producto,
        admin_editar_producto,
        admin_buscar_catalogo,
        admin_descartar_solicitud,
        admin_poner_foto,
        WebSearchTool(),
    ],
)

_sesiones_admin: dict[str, SQLiteSession] = {}


async def procesar_mensaje_admin(jid: str, texto: str) -> str:
    """Corre el agente administrativo para un número admin (Patricia/Eathan)."""
    ctx = PlatimContext(jid=jid)
    if jid not in _sesiones_admin:
        _sesiones_admin[jid] = SQLiteSession(f"admin:{jid}", SESSIONS_DB)
    sesion = _sesiones_admin[jid]
    result = await Runner.run(admin_agent, texto, context=ctx, session=sesion)
    return formatear_whatsapp((result.final_output or "").strip())


async def reiniciar_admin(jid: str = "dashboard") -> None:
    """Borra la memoria de una sesión admin (p. ej. la consola del dashboard)."""
    ses = _sesiones_admin.pop(jid, None)
    if ses is not None:
        try:
            await ses.clear_session()
        except Exception:  # noqa: BLE001
            pass
