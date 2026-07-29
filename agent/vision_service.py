"""
Visión y documentos para PLATIM Agent.

Convierte imágenes y PDF recibidos por WhatsApp en items de entrada multimodal
para el agente (Responses API de OpenAI). El modelo (gpt-4o-mini) los "ve"/lee
directamente; aquí solo empaquetamos los bytes como data URL en base64.

    item_imagen(data, mime)              -> dict  (type input_image)
    item_documento(data, filename, mime) -> dict  (type input_file, p.ej. PDF)
"""

import base64

# Límite defensivo: imágenes/PDF más grandes se rechazan (costo/límites del modelo).
MAX_BYTES = 15 * 1024 * 1024  # ~15 MB


def _data_url(mime: str, data: bytes) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def item_imagen(data: bytes, mime: str = "image/jpeg") -> dict:
    """Item de imagen para la entrada del agente (visión)."""
    return {
        "type": "input_image",
        "image_url": _data_url(mime or "image/jpeg", data),
        "detail": "auto",
    }


def item_documento(
    data: bytes, filename: str = "documento.pdf", mime: str = "application/pdf"
) -> dict:
    """Item de archivo para la entrada del agente (lectura de PDF)."""
    return {
        "type": "input_file",
        "filename": filename or "documento.pdf",
        "file_data": _data_url(mime or "application/pdf", data),
    }
