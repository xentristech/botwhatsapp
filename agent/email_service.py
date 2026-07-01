"""
Envio de cotizaciones por email (Gmail SMTP) con PDF adjunto.

Funcion principal:
    enviar_cotizacion_email(cot, pdf_bytes) -> bool

Envia al cliente (cot['email']) con copia (Cc) al correo interno PLATIM.
"""

import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from dotenv import load_dotenv

load_dotenv()

GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
PLATIM_EMAIL = os.getenv("PLATIM_EMAIL", "")


def _moneda(valor) -> str:
    try:
        n = int(round(float(valor)))
    except (TypeError, ValueError):
        n = 0
    return "$" + f"{n:,}".replace(",", ".")


def _build_html_body(cot: dict) -> str:
    """Construye el cuerpo HTML del email."""
    filas = ""
    for item in cot.get("items", []):
        filas += f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #e0e0e0;">{item.get('codigo','')}</td>
              <td style="padding:8px;border-bottom:1px solid #e0e0e0;">{item.get('nombre','')}</td>
              <td style="padding:8px;border-bottom:1px solid #e0e0e0;text-align:center;">{item.get('cantidad',0)}</td>
              <td style="padding:8px;border-bottom:1px solid #e0e0e0;text-align:right;">{_moneda(item.get('precio',0))}</td>
              <td style="padding:8px;border-bottom:1px solid #e0e0e0;text-align:right;">{_moneda(item.get('subtotal',0))}</td>
            </tr>"""

    tipo_str = "Mayoreo" if cot.get("tipo_precio") == "mayoreo" else "Público"

    return f"""\
<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;color:#333;margin:0;padding:0;background:#f5f5f5;">
  <div style="max-width:640px;margin:0 auto;background:#fff;">
    <div style="background:#1a237e;color:#fff;padding:24px;">
      <h1 style="margin:0;font-size:24px;">PLATIM</h1>
      <p style="margin:4px 0 0;color:#90caf9;">Dotaciones y Seguridad Industrial</p>
    </div>
    <div style="padding:24px;">
      <p>Hola <strong>{cot.get('nombre','')}</strong>,</p>
      <p>Adjuntamos tu cotización <strong>{cot.get('codigo','')}</strong>.
         Encontrarás el detalle a continuación y el PDF anexo a este correo.</p>

      <p style="margin:8px 0;"><strong>Empresa:</strong> {cot.get('empresa','—')}<br>
         <strong>Tipo de precio:</strong> {tipo_str}<br>
         <strong>Vigencia:</strong> 30 días desde la fecha de emisión.</p>

      <table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:14px;">
        <thead>
          <tr style="background:#1a237e;color:#fff;">
            <th style="padding:8px;text-align:left;">Código</th>
            <th style="padding:8px;text-align:left;">Producto</th>
            <th style="padding:8px;text-align:center;">Cant.</th>
            <th style="padding:8px;text-align:right;">Precio Unit.</th>
            <th style="padding:8px;text-align:right;">Subtotal</th>
          </tr>
        </thead>
        <tbody>{filas}</tbody>
        <tfoot>
          <tr style="background:#e8eaf6;">
            <td colspan="4" style="padding:10px;text-align:right;font-weight:bold;">TOTAL:</td>
            <td style="padding:10px;text-align:right;font-weight:bold;color:#1a237e;">{_moneda(cot.get('total',0))}</td>
          </tr>
        </tfoot>
      </table>

      <p style="margin-top:24px;color:#666;font-size:12px;">
        Precios en pesos colombianos (COP). IVA no incluido salvo indicación.
        Para confirmar el pedido responde este correo o escríbenos por WhatsApp.
      </p>
    </div>
    <div style="background:#f5f5f5;padding:16px 24px;color:#999;font-size:12px;">
      PLATIM · {PLATIM_EMAIL} · Palmira, Valle del Cauca, Colombia.
    </div>
  </div>
</body>
</html>"""


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _fecha_legible(fecha_iso: str) -> str:
    """'2026-07-07' -> 'martes 7 de julio de 2026'."""
    from datetime import date

    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    try:
        d = date.fromisoformat(fecha_iso)
        return f"{_DIAS[d.weekday()]} {d.day} de {meses[d.month - 1]} de {d.year}"
    except Exception:  # noqa: BLE001
        return fecha_iso


async def enviar_cita_email(cita: dict) -> bool:
    """Notifica una cita con la asesora al cliente (To) y a PLATIM (Cc)."""
    asesora = cita.get("asesora", "Patricia")
    fecha_txt = _fecha_legible(cita.get("fecha", ""))
    hora = cita.get("hora", "")
    cliente = cita.get("nombre", "Cliente")
    destino = cita.get("email", "")

    html = f"""\
<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;background:#f5f5f5;margin:0;">
  <div style="max-width:600px;margin:0 auto;background:#fff;">
    <div style="background:#1a237e;color:#fff;padding:24px;">
      <h1 style="margin:0;font-size:22px;">PLATIM · Cita confirmada</h1>
    </div>
    <div style="padding:24px;">
      <p>Hola <strong>{cliente}</strong>,</p>
      <p>Tu cita con la asesora <strong>{asesora}</strong> quedó agendada:</p>
      <table style="font-size:15px;margin:12px 0;">
        <tr><td style="padding:4px 8px;"><strong>Fecha:</strong></td><td>{fecha_txt}</td></tr>
        <tr><td style="padding:4px 8px;"><strong>Hora:</strong></td><td>{hora} (hora Colombia)</td></tr>
        <tr><td style="padding:4px 8px;"><strong>Modalidad:</strong></td><td>Atención personalizada</td></tr>
      </table>
      <p style="color:#666;font-size:13px;">Si no puedes asistir, responde este correo o escríbenos por WhatsApp para reprogramar.</p>
    </div>
    <div style="background:#f5f5f5;padding:16px 24px;color:#999;font-size:12px;">
      PLATIM · {PLATIM_EMAIL} · Palmira, Valle del Cauca, Colombia.
    </div>
  </div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Cita con {asesora} - {fecha_txt} {hora} | PLATIM"
    msg["From"] = GMAIL_USER
    msg["To"] = destino or PLATIM_EMAIL
    if PLATIM_EMAIL:
        msg["Cc"] = PLATIM_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    destinatarios = []
    if destino:
        destinatarios.append(destino)
    if PLATIM_EMAIL:
        destinatarios.append(PLATIM_EMAIL)
    if not destinatarios:
        return False

    await aiosmtplib.send(
        msg, hostname="smtp.gmail.com", port=587, start_tls=True,
        username=GMAIL_USER, password=GMAIL_APP_PASSWORD, recipients=destinatarios,
    )
    return True


async def enviar_cotizacion_email(cot: dict, pdf_bytes: bytes) -> bool:
    """Envia el email con el PDF adjunto al cliente y copia a PLATIM.
    Devuelve True si se envio correctamente."""
    destino = cot.get("email", "")
    if not destino:
        return False

    msg = MIMEMultipart("mixed")
    msg["Subject"] = (
        f"Cotización PLATIM {cot['codigo']} - {cot.get('nombre', 'Cliente')}"
    )
    msg["From"] = GMAIL_USER
    msg["To"] = destino
    if PLATIM_EMAIL:
        msg["Cc"] = PLATIM_EMAIL

    # Cuerpo HTML
    msg.attach(MIMEText(_build_html_body(cot), "html", "utf-8"))

    # PDF adjunto
    pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_part.add_header(
        "Content-Disposition",
        "attachment",
        filename=f"Cotizacion_PLATIM_{cot['codigo']}.pdf",
    )
    msg.attach(pdf_part)

    # Lista real de destinatarios (To + Cc)
    destinatarios = [destino]
    if PLATIM_EMAIL:
        destinatarios.append(PLATIM_EMAIL)

    await aiosmtplib.send(
        msg,
        hostname="smtp.gmail.com",
        port=587,
        start_tls=True,
        username=GMAIL_USER,
        password=GMAIL_APP_PASSWORD,
        recipients=destinatarios,
    )
    return True
