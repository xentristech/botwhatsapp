"""
Servicio de audio para PLATIM Agent (OpenAI).

    transcribir_audio(audio_bytes, filename) -> str   # nota de voz -> texto (STT)
    texto_a_audio(texto) -> bytes                       # texto -> nota de voz (TTS)

STT usa Whisper; TTS devuelve Ogg/Opus, el formato que WhatsApp reproduce como
nota de voz. Modelos y voz configurables por variables de entorno.
"""

import io
import os
import re

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

STT_MODEL = os.getenv("STT_MODEL", "whisper-1")
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.getenv("TTS_VOICE", "alloy")


async def transcribir_audio(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    """Transcribe una nota de voz a texto usando Whisper."""
    f = io.BytesIO(audio_bytes)
    f.name = filename  # Whisper usa la extension para inferir el formato.
    r = await _client.audio.transcriptions.create(
        model=STT_MODEL,
        file=f,
        language="es",
    )
    return (r.text or "").strip()


def _limpiar_para_voz(texto: str) -> str:
    """Quita marcas de formato de WhatsApp/Markdown para que no se lean en voz."""
    t = re.sub(r"[*_~`#>]", "", texto or "")
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


async def texto_a_audio(texto: str) -> bytes:
    """Convierte texto a una nota de voz (Ogg/Opus) usando TTS."""
    limpio = _limpiar_para_voz(texto)[:4000]  # limite de entrada de TTS
    async with _client.audio.speech.with_streaming_response.create(
        model=TTS_MODEL,
        voice=TTS_VOICE,
        input=limpio,
        response_format="opus",
    ) as resp:
        return await resp.read()
