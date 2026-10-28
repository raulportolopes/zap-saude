import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.conversa import Conversa
from app.fila import Repositorio, carregar_config
from app.whatsapp import montar_payload

CFG = carregar_config(Path(__file__).resolve().parent.parent / "config" / "maua.json")
# terça-feira, 28/10/2026, 10h (horário local do processo)
TERCA_10H = time.mktime((2026, 10, 28, 10, 0, 0, 0, 0, -1))


@pytest.fixture
def conversa():
    return Conversa(Repositorio(CFG))


def test_saudacao_mostra_menu(conversa):
    r = conversa.responder("u1", texto="oi")
    assert r[0]["tipo"] == "botoes"
    assert [b[0] for b in r[0]["botoes"]] == ["espera", "onde_ir"]


def test_palavra_upa_mostra_as_quatro_upas(conversa):
    r = conversa.responder("u1", texto="UPA", agora=TERCA_10H)
    for nome in ("Barão", "Zaíra", "Magini", "Vila Assis"):
        assert nome in r[0]["texto"]
    assert "simulados" in r[0]["texto"]


def test_sinal_de_alarme_manda_ligar_192(conversa):
    conversa.responder("u1", botao="onde_ir")
    r = conversa.responder("u1", texto="sim")
    assert "192" in r[0]["texto"]


def test_texto_com_dor_no_peito_vai_direto_ao_192(conversa):
    r = conversa.responder("u1", texto="estou com dor no peito")
    assert "192" in r[0]["texto"]


def test_recomenda_menor_tempo_total(conversa):
    conversa.responder("u1", botao="onde_ir")
    conversa.responder("u1", botao="alarme_nao")
    r = conversa.responder("u1", botao="regiao_zaira", agora=TERCA_10H)
    rank = conversa.ranking("zaira", TERCA_10H)
    assert rank == sorted(rank, key=lambda i: i["total"])
    assert rank[0]["nome"] in r[0]["texto"]
    assert "UBS" in r[0]["texto"]  # dia útil, 10h


def test_regiao_digitada_e_reconhecida(conversa):
    conversa.responder("u1", botao="onde_ir")
    conversa.responder("u1", texto="não")
    r = conversa.responder("u1", texto="moro no zaira", agora=TERCA_10H)
    assert "melhor opção" in r[0]["texto"]


def test_fila_manual_substitui_simulacao(conversa):
    conversa.repo.gravar("magini", {"amarelo": 5, "verde": 7, "azul": 9})
    filas = {f.unidade: f for f in conversa.repo.atual()}
    assert filas["magini"].origem == "manual" and filas["magini"].espera["verde"] == 7


def test_alerta_dispara_quando_espera_baixa(conversa):
    conversa.repo.gravar("magini", {"amarelo": 30, "verde": 100, "azul": 140})
    conversa.estados["u1"] = {"etapa": "inicio", "ultima_sugestao": "magini"}
    conversa.responder("u1", botao="avisar")
    assert conversa.verificar_alertas() == []
    conversa.repo.gravar("magini", {"amarelo": 10, "verde": 20, "azul": 30})
    disparados = conversa.verificar_alertas()
    assert disparados[0][0] == "u1" and "20 min" in disparados[0][1]["texto"]


def test_payload_respeita_limites_do_whatsapp(conversa):
    conversa.responder("u1", botao="onde_ir")
    lista = conversa.responder("u1", botao="alarme_nao")[0]
    p = montar_payload("5511999999999", lista)
    assert all(len(row["title"]) <= 24 for row in p["interactive"]["action"]["sections"][0]["rows"])
    botoes = montar_payload("5511999999999", conversa.responder("u1", texto="oi")[0])
    assert all(len(b["reply"]["title"]) <= 20 for b in botoes["interactive"]["action"]["buttons"])


def test_webhook(monkeypatch):
    from app import main

    enviados = []

    async def falso_enviar(para, resposta):
        enviados.append((para, resposta))

    monkeypatch.setattr(main.whatsapp, "enviar", falso_enviar)
    c = TestClient(main.app)
    assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": main.VERIFY_TOKEN, "hub.challenge": "42"}).text == "42"
    corpo = {"entry": [{"changes": [{"value": {"messages": [{"from": "5511", "type": "text", "text": {"body": "upa"}}]}}]}]}
    assert c.post("/webhook", json=corpo).status_code == 200
    assert enviados and "UPA Magini" in enviados[0][1]["texto"]
