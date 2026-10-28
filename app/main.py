"""Servidor do Zap-Saúde: webhook do WhatsApp, painel de fila (nível 1) e alertas."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from . import whatsapp
from .conversa import Conversa, como_texto
from .fila import Repositorio, carregar_config

log = logging.getLogger("zap-saude")

os.environ.setdefault("TZ", "America/Sao_Paulo")  # horários das respostas e da UBS
time.tzset()

RAIZ = Path(__file__).resolve().parent.parent
config = carregar_config(os.getenv("CIDADE_CONFIG", RAIZ / "config" / "maua.json"))
repo = Repositorio(config, os.getenv("DB_PATH", str(RAIZ / "fila.db")))
conversa = Conversa(repo)

VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "troque-este-token")
APP_SECRET = os.getenv("WA_APP_SECRET", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


async def _loop_alertas():
    while True:
        await asyncio.sleep(120)
        for usuario, resposta in conversa.verificar_alertas():
            try:
                await whatsapp.enviar(usuario, resposta)
            except Exception:
                log.exception("falha ao enviar alerta")


@asynccontextmanager
async def lifespan(_app):
    tarefa = asyncio.create_task(_loop_alertas())
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
    texto = botao = None
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
        texto = ""  # versão 2: usar latitude e longitude no lugar da região

    respostas = conversa.responder(usuario, texto=texto, botao=botao)
    for r in respostas:
        await whatsapp.enviar(usuario, r)
    if veio_de_audio:  # quem fala recebe a resposta também em áudio
        from . import voz

        ogg = await asyncio.to_thread(voz.sintetizar, como_texto(respostas))
        await whatsapp.enviar(usuario, {"tipo": "audio", "media_id": await whatsapp.subir_audio(ogg)})


class AtualizacaoFila(BaseModel):
    unidade: str
    amarelo: int
    verde: int
    azul: int


@app.post("/admin/fila")
def atualizar_fila(dados: AtualizacaoFila, authorization: str = Header(default="")):
    """Nível 1: a recepção da UPA (ou um coletor) informa a espera em minutos por cor."""
    if not ADMIN_TOKEN or authorization != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(401)
    try:
        repo.gravar(dados.unidade, {"amarelo": dados.amarelo, "verde": dados.verde, "azul": dados.azul})
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.get("/fila")
def fila_atual():
    return [f.__dict__ for f in repo.atual()]
