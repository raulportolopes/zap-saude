"""Voz: transcrever o áudio recebido e gerar a resposta falada.

Os dois provedores são trocáveis. Padrão do protótipo, ambos instalados à parte:
  - transcrição: faster-whisper (roda no próprio servidor, sem custo por uso)
  - fala: gTTS (português do Brasil); troque por um serviço pago com voz melhor em produção
A conversão para OGG/OPUS mono usa o ffmpeg, formato exigido para mensagem de voz no WhatsApp.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

_modelo = None


def transcrever(audio: bytes) -> str:
    global _modelo
    from faster_whisper import WhisperModel  # import tardio: só quem usa voz precisa instalar

    if _modelo is None:
        _modelo = WhisperModel(os.getenv("WHISPER_MODELO", "small"), device="cpu", compute_type="int8")
    with tempfile.NamedTemporaryFile(suffix=".ogg") as f:
        f.write(audio)
        f.flush()
        segmentos, _ = _modelo.transcribe(f.name, language="pt")
        return " ".join(s.text.strip() for s in segmentos)


def para_ogg_opus(mp3: bytes) -> bytes:
    r = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-ac", "1", "-b:a", "24k", "-f", "ogg", "pipe:1"],
        input=mp3, capture_output=True, check=True,
    )
    return r.stdout


def sintetizar(texto: str) -> bytes:
    """Devolve OGG/OPUS pronto para enviar com "voice": true (abaixo de 512 KB para aparecer como áudio)."""
    from gtts import gTTS

    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
        gTTS(texto, lang="pt", tld="com.br").write_to_fp(f)
        f.flush()
        f.seek(0)
        return para_ogg_opus(f.read())
