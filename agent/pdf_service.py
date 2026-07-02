"""
Generacion de PDF de cotizacion con reportlab.

Funcion principal:
    generar_pdf_cotizacion(cot: dict) -> bytes

El dict 'cot' debe contener:
    codigo, nombre, empresa, email, telefono, tipo_precio,
    items (lista), total, ts
Cada item: codigo, nombre, cantidad, precio, subtotal
"""

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

AZUL_PLATIM = colors.HexColor("#1a237e")
AZUL_CLARO = colors.HexColor("#e8eaf6")
GRIS = colors.HexColor("#f5f5f5")


def _moneda(valor) -> str:
    """Formatea un entero como precio COP: $85.000"""
    try:
        n = int(round(float(valor)))
    except (TypeError, ValueError):
        n = 0
    return "$" + f"{n:,}".replace(",", ".")


def generar_pdf_catalogo(productos: list[dict], tipo_precio: str = "publico") -> bytes:
    """Genera un PDF con el catálogo completo (agrupado por categoría)."""
    from collections import OrderedDict

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=1.5 * cm, leftMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title="Catálogo PLATIM",
    )
    getSampleStyleSheet()
    story = []

    header_style = ParagraphStyle(
        "h", fontSize=20, textColor=colors.white, fontName="Helvetica-Bold")
    sub_style = ParagraphStyle(
        "s", fontSize=10, textColor=colors.HexColor("#90caf9"))
    from datetime import datetime as _dt
    header = Table(
        [[Paragraph("PLATIM", header_style),
          Paragraph("Catálogo de productos", header_style)],
         [Paragraph("Dotaciones y Seguridad Industrial", sub_style),
          Paragraph(f"Fecha: {_dt.utcnow().isoformat()[:10]}", sub_style)]],
        colWidths=[9 * cm, 9 * cm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL_PLATIM),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(header)
    story.append(Spacer(1, 0.4 * cm))

    es_may = tipo_precio == "mayoreo"
    celda = ParagraphStyle("celda", fontSize=9, leading=11)
    grupos = OrderedDict()
    for p in productos:
        grupos.setdefault(p.get("categoria", "General"), []).append(p)

    rows = [["Código", "Producto", "Precio mayoreo" if es_may else "Precio"]]
    filas_cat = []
    for cat, prods in grupos.items():
        rows.append([cat, "", ""])
        filas_cat.append(len(rows) - 1)
        for p in prods:
            precio = p.get("precio_mayoreo" if es_may else "precio_publico", 0)
            rows.append([p.get("codigo", ""),
                         Paragraph(str(p.get("nombre", "")), celda),
                         _moneda(precio)])

    tabla = Table(rows, colWidths=[3 * cm, 9.5 * cm, 3.5 * cm], repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL_PLATIM),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0e0")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    for r in filas_cat:
        estilo += [
            ("SPAN", (0, r), (-1, r)),
            ("BACKGROUND", (0, r), (-1, r), AZUL_CLARO),
            ("FONTNAME", (0, r), (-1, r), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, r), (-1, r), AZUL_PLATIM),
        ]
    tabla.setStyle(TableStyle(estilo))
    story.append(tabla)
    story.append(Spacer(1, 0.6 * cm))

    footer = ParagraphStyle("f", fontSize=8, textColor=colors.grey)
    tipo_txt = "mayoreo" if es_may else "público"
    story.append(Paragraph(
        f"Precios de {tipo_txt} en pesos colombianos (COP). IVA no incluido salvo "
        "indicación. Precios sujetos a cambio. ventas@platim.co | Palmira, Valle "
        "del Cauca, Colombia.", footer))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generar_pdf_cotizacion(cot: dict) -> bytes:
    """Genera el PDF de la cotizacion y devuelve los bytes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"Cotizacion PLATIM {cot.get('codigo', '')}",
    )

    getSampleStyleSheet()  # asegura registro de estilos base
    story = []

    # ── HEADER ──────────────────────────────────────────────
    header_style = ParagraphStyle(
        "header", fontSize=22, textColor=colors.white,
        alignment=TA_LEFT, fontName="Helvetica-Bold",
    )
    sub_style = ParagraphStyle(
        "sub", fontSize=10, textColor=colors.HexColor("#90caf9"),
        alignment=TA_LEFT,
    )

    header_data = [
        [
            Paragraph("PLATIM", header_style),
            Paragraph(f'Cotización N° {cot["codigo"]}', header_style),
        ],
        [
            Paragraph("Dotaciones y Seguridad Industrial", sub_style),
            Paragraph(f'Fecha: {cot.get("ts", "")[:10]}', sub_style),
        ],
    ]
    header_table = Table(header_data, colWidths=[9 * cm, 8 * cm])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), AZUL_PLATIM),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 0.5 * cm))

    # ── DATOS CLIENTE ────────────────────────────────────────
    tipo_str = "Mayoreo" if cot.get("tipo_precio") == "mayoreo" else "Público"
    cliente_data = [
        ["Cliente:", cot.get("nombre", "—"), "Empresa:", cot.get("empresa", "—")],
        ["Email:", cot.get("email", "—"), "Teléfono:", cot.get("telefono", "—")],
        ["Tipo precio:", tipo_str, "Vigencia:", "30 días desde la fecha"],
    ]
    cliente_table = Table(cliente_data, colWidths=[3 * cm, 7 * cm, 3 * cm, 4 * cm])
    cliente_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), GRIS),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.white),
            ]
        )
    )
    story.append(cliente_table)
    story.append(Spacer(1, 0.5 * cm))

    # ── TABLA DE PRODUCTOS ───────────────────────────────────
    celda_style = ParagraphStyle("celda", fontSize=9, leading=11)
    rows = [["Código", "Producto", "Cant.", "Precio Unit.", "Subtotal"]]
    for item in cot.get("items", []):
        rows.append(
            [
                item.get("codigo", ""),
                Paragraph(str(item.get("nombre", "")), celda_style),
                str(item.get("cantidad", 0)),
                _moneda(item.get("precio", 0)),
                _moneda(item.get("subtotal", 0)),
            ]
        )
    rows.append(["", "", "", "TOTAL:", _moneda(cot.get("total", 0))])

    prod_table = Table(
        rows, colWidths=[2.5 * cm, 8 * cm, 1.5 * cm, 3 * cm, 3 * cm]
    )
    prod_style = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), AZUL_PLATIM),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, AZUL_CLARO]),
        ("GRID", (0, 0), (-1, -2), 0.5, colors.HexColor("#e0e0e0")),
        # Fila total
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("FONTNAME", (3, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (3, -1), (-1, -1), 11),
        ("TEXTCOLOR", (4, -1), (4, -1), AZUL_PLATIM),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, AZUL_PLATIM),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    prod_table.setStyle(TableStyle(prod_style))
    story.append(prod_table)
    story.append(Spacer(1, 0.8 * cm))

    # ── FOOTER ───────────────────────────────────────────────
    footer_style = ParagraphStyle(
        "footer", fontSize=8, textColor=colors.grey, alignment=TA_LEFT
    )
    story.append(
        Paragraph(
            "Precios en pesos colombianos (COP). IVA no incluido salvo indicación. "
            "Cotización válida por 30 días. Para confirmar el pedido comuníquese con "
            "PLATIM. ventas@platim.co | Palmira, Valle del Cauca, Colombia.",
            footer_style,
        )
    )

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
