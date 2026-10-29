"""Servidor do Zap-Saúde: webhook do WhatsApp e coleta do painel da prefeitura a cada 5 minutos."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response

from . import whatsapp
from .conversa import Conversa, como_texto
from .siss import Coletor

log = logging.getLogger("zap-saude")

os.environ.setdefault("TZ", "America/Sao_Paulo")  # horários das respostas
time.tzset()

RAIZ = Path(__file__).resolve().parent.parent
with open(os.getenv("CIDADE_CONFIG", RAIZ / "config" / "guarulhos.json"), encoding="utf-8") as f:
    config = json.load(f)
coletor = Coletor(config["fonte"])
conversa = Conversa(config, coletor, os.getenv("GOOGLE_MAPS_API_KEY") or None)

VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "troque-este-token")
APP_SECRET = os.getenv("WA_APP_SECRET", "")
INTERVALO_S = 5 * 60  # o portal da prefeitura atualiza a cada 5 minutos


async def _loop_coleta():
    while True:
        await coletor.atualizar()
        if coletor.erro:
            log.warning("falha ao ler o portal: %s", coletor.erro)
        await asyncio.sleep(INTERVALO_S)


@asynccontextmanager
async def lifespan(_app):
    tarefa = asyncio.create_task(_loop_coleta())
    yield
    tarefa.cancel()


app = FastAPI(title="Zap-Saúde", lifespan=lifespan)


@app.get("/webhook")
def verificar(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return Response(p.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403)


@app.post("/webhook")
async def receber(request: Request, tarefas: BackgroundTasks, x_hub_signature_256: str = Header(default="")):
    corpo = await request.body()
    if APP_SECRET:
        esperado = "sha256=" + hmac.new(APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(esperado, x_hub_signature_256):
            raise HTTPException(401)
    dados = await request.json()
    for entrada in dados.get("entry", []):
        for mudanca in entrada.get("changes", []):
            for msg in mudanca.get("value", {}).get("messages", []):
                tarefas.add_task(processar, msg)
    return {"ok": True}  # responder rápido; o trabalho segue em segundo plano


async def processar(msg: dict) -> None:
    usuario, tipo = msg["from"], msg["type"]
    texto = botao = local = None
    veio_de_audio = False
    if tipo == "text":
        texto = msg["text"]["body"]
    elif tipo == "interactive":
        i = msg["interactive"]
        botao = (i.get("button_reply") or i.get("list_reply") or {}).get("id")
    elif tipo == "audio":
        from . import voz

        audio = await whatsapp.baixar_midia(msg["audio"]["id"])
        texto = await asyncio.to_thread(voz.transcrever, audio)
        veio_de_audio = True
        log.info("áudio de %s transcrito: %s", usuario, texto)
    elif tipo == "location":
        local = (msg["location"]["latitude"], msg["location"]["longitude"])

    respostas = await conversa.responder(usuario, texto=texto, botao=botao, local=local)
    for r in respostas:
        await whatsapp.enviar(usuario, r)
    if veio_de_audio:  # quem fala recebe a resposta também em áudio
        from . import voz

        ogg = await asyncio.to_thread(voz.sintetizar, como_texto(respostas))
        await whatsapp.enviar(usuario, {"tipo": "audio", "media_id": await whatsapp.subir_audio(ogg)})


@app.get("/fila")
def fila_atual():
    """O que o bot está usando agora: o último painel lido do portal."""
    p = coletor.painel
    return {
        "atualizado_em": p.atualizado_em if p else None,
        "coletado_em": p.coletado_em if p else None,
        "erro": coletor.erro,
        "unidades": [asdict(u) for u in p.unidades] if p else [],
    }
