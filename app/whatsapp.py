"""Envio pela WhatsApp Cloud API (oficial da Meta)."""
from __future__ import annotations

import os

import httpx

VERSAO = os.getenv("WA_API_VERSION", "v21.0")
TOKEN = os.getenv("WA_TOKEN", "")
PHONE_ID = os.getenv("WA_PHONE_NUMBER_ID", "")
BASE = f"https://graph.facebook.com/{VERSAO}"


def montar_payload(para: str, resposta: dict) -> dict:
    """Converte uma resposta do fluxo (conversa.py) no JSON da API."""
    base = {"messaging_product": "whatsapp", "to": para}
    if resposta["tipo"] == "texto":
        return {**base, "type": "text", "text": {"body": resposta["texto"][:4096], "preview_url": False}}
    if resposta["tipo"] == "botoes":
        return {**base, "type": "interactive", "interactive": {
            "type": "button",
            "body": {"text": resposta["texto"][:1024]},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in resposta["botoes"][:3]
            ]},
        }}
    if resposta["tipo"] == "lista":
        return {**base, "type": "interactive", "interactive": {
            "type": "list",
            "body": {"text": resposta["texto"][:1024]},
            "action": {"button": resposta["botao"][:20], "sections": [{
                "title": "Opções",
                "rows": [{"id": i, "title": t[:24], **({"description": d[:72]} if d else {})}
                         for i, t, d in resposta["itens"][:10]],
            }]},
        }}
    if resposta["tipo"] == "pedir_local":
        return {**base, "type": "interactive", "interactive": {
            "type": "location_request_message",
            "body": {"text": resposta["texto"][:1024]},
            "action": {"name": "send_location"},
        }}
    if resposta["tipo"] == "audio":
        return {**base, "type": "audio", "audio": {"id": resposta["media_id"], "voice": True}}
    raise ValueError(f"tipo desconhecido: {resposta['tipo']}")


def _headers() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


async def enviar(para: str, resposta: dict) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{BASE}/{PHONE_ID}/messages", headers=_headers(), json=montar_payload(para, resposta))
        r.raise_for_status()


async def baixar_midia(media_id: str) -> bytes:
    """Áudio recebido: primeiro pega a URL temporária, depois o arquivo."""
    async with httpx.AsyncClient(timeout=30) as c:
        meta = (await c.get(f"{BASE}/{media_id}", headers=_headers())).raise_for_status().json()
        return (await c.get(meta["url"], headers=_headers())).raise_for_status().content


async def subir_audio(ogg: bytes) -> str:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            f"{BASE}/{PHONE_ID}/media", headers=_headers(),
            data={"messaging_product": "whatsapp", "type": "audio/ogg"},
            files={"file": ("resposta.ogg", ogg, "audio/ogg")},
        )
        return r.raise_for_status().json()["id"]
