"""
Utilidades de imagen para las fotos de producto.

    a_jpeg(contenido) -> bytes | None   # convierte cualquier imagen a JPEG

WhatsApp NO entrega imágenes WEBP por link (solo JPG/PNG), por eso las fotos se
guardan SIEMPRE como JPEG. Aplana transparencia sobre blanco y limita el tamaño.
"""

import io


def a_jpeg(contenido: bytes) -> bytes | None:
    """Convierte cualquier imagen (PNG/WEBP/JPG…) a JPEG. Devuelve los bytes o
    None si no se pudo abrir como imagen."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        img = Image.open(io.BytesIO(contenido))
        # Aplanar transparencia sobre fondo blanco.
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            fondo = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(fondo, img).convert("RGB")
        else:
            img = img.convert("RGB")
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
