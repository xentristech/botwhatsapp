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
    function_tool,
)

from datetime import date, timedelta

from agent import catalogo
from agent.db import (
    DB_PATH,
    actualizar_cita_email,
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

# ── Agenda de la asesora Patricia ────────────────────────────────────────
ASESORA = "Patricia"
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
                "precio": catalogo.precio_de(p, es_mayorista),
                "observaciones": p["observaciones"],
            }
        )
    return json.dumps(
        {"encontrados": len(salida), "tipo_precio": estado["tipo_precio"],
         "productos": salida},
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
                "precio": catalogo.precio_de(p, es_mayorista),
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
    estado = get_estado(ctx.context.jid)
    cantidad = max(1, int(cantidad))

    # Si el precio no viene o viene 0, lo tomamos del catalogo segun tipo.
    prod = catalogo.obtener(codigo)
    if (not precio or precio <= 0) and prod:
        precio = catalogo.precio_de(prod, estado.get("tipo_precio") == "mayoreo")
    precio = int(precio)
    subtotal = precio * cantidad

    # Si el item ya existe, acumula cantidad.
    for it in estado["items"]:
        if it["codigo"] == codigo:
            it["cantidad"] += cantidad
            it["precio"] = precio
            it["subtotal"] = it["precio"] * it["cantidad"]
            break
    else:
        estado["items"].append(
            {
                "codigo": codigo,
                "nombre": nombre or (prod["nombre"] if prod else codigo),
                "cantidad": cantidad,
                "precio": precio,
                "subtotal": subtotal,
            }
        )

    save_estado(ctx.context.jid, estado)
    total = sum(i["subtotal"] for i in estado["items"])
    return json.dumps(
        {"ok": True, "items": len(estado["items"]), "total": total},
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
    es_mayorista: bool = False,
) -> str:
    """Registra contacto del cliente y define tipo de precio.
    Si es_mayorista=True usa precios de mayoreo para la sesion."""
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

    # Si cambio el tipo de precio, recalcular items existentes.
    if cambio_tipo:
        for it in estado["items"]:
            prod = catalogo.obtener(it["codigo"])
            if prod:
                it["precio"] = catalogo.precio_de(prod, es_mayorista)
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

Tu trabajo es:
1. Asesorar al cliente sobre que productos necesita segun su actividad o riesgo
2. Buscar productos en el catalogo usando tus herramientas
3. Comparar opciones cuando el cliente duda entre productos similares
4. Armar la cotizacion con los productos y cantidades confirmados
5. Pedir los datos del cliente (nombre, empresa, email, telefono)
6. Generar y enviar la cotizacion en PDF por WhatsApp y email

REGLAS:
- Saluda siempre mencionando PLATIM en el primer mensaje
- Pregunta si es cliente minorista o mayorista cuando pregunten precios
- SIEMPRE usa buscar_productos antes de mencionar productos
- Si el cliente pide la LISTA o el CATÁLOGO completo, usa enviar_catalogo_pdf
  para mandarle el PDF con todos los productos
- Cuando el cliente confirme cantidad de un producto, usa agregar_item_cotizacion
- Si el cliente pide VARIOS productos en un mensaje (ej. "3 botas, 1 gafa y un
  uniforme"), agrégalos TODOS: una llamada a agregar_item_cotizacion por CADA
  producto. No dejes ninguno por fuera.
- Si el cliente dice "escógelos tú" o te pide elegir, busca en el catálogo, elige
  productos concretos y agrégalos uno por uno con agregar_item_cotizacion.
- ANTES de generar la cotización o el link de pago, usa ver_cotizacion_actual,
  muéstrale al cliente el RESUMEN de TODOS los productos y el total, y pide su
  confirmación. Verifica que estén TODOS los que pidió.
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


async def procesar_mensaje(jid: str, texto: str, registrar_in: bool = True) -> str:
    """Corre el agente para un mensaje entrante y devuelve la respuesta.

    Mantiene la memoria de la conversacion por jid mediante SQLiteSession.
    registrar_in: si es False no registra el mensaje entrante (lo hace quien llama,
    p.ej. el webhook cuando agrupa varios mensajes).
    """
    if registrar_in:
        registrar_mensaje(jid, "in", texto)
    ctx = PlatimContext(jid=jid)
    sesion = _get_sesion(jid)

    result = await Runner.run(
        platim_agent,
        texto,
        context=ctx,
        session=sesion,
    )
    respuesta = (result.final_output or "").strip()
    respuesta = formatear_whatsapp(respuesta)
    if respuesta:
        registrar_mensaje(jid, "out", respuesta)
    return respuesta
