import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import rotas
from app.conversa import Conversa, como_texto
from app.siss import Coletor, interpretar, minutos
from app.whatsapp import montar_payload

RAIZ = Path(__file__).resolve().parent.parent
CFG = json.loads((RAIZ / "config" / "guarulhos.json").read_text(encoding="utf-8"))
HTML = (RAIZ / "tests" / "fixtures" / "guarulhos.html").read_text(encoding="utf-8")
COLETA = 1_800_000_000.0


def responder(conversa, *args, **kw):
    kw.setdefault("agora", COLETA + 60)
    return asyncio.run(conversa.responder(*args, **kw))


@pytest.fixture
def conversa():
    coletor = Coletor()
    coletor.painel = interpretar(HTML, coletado_em=COLETA)
    return Conversa(CFG, coletor)


# ---------- coletor ----------
def test_tempo_com_hora_e_minuto():
    assert minutos("12 MIN.") == 12
    assert minutos("2 H 3 MIN.") == 123
    assert minutos("1 H") == 60


def test_le_as_11_unidades_com_adulto_e_infantil():
    p = interpretar(HTML)
    assert p.atualizado_em == "29/10/2026 09:34:59"
    assert len(p.unidades) == 11
    assert all(set(u.paineis) == {"adulto", "infantil"} and len(u.paineis["adulto"]) == 5 for u in p.unidades)
    taboao = p.unidades[0]
    assert taboao.nome_portal == "UPA TABOÃO"  # entidade HTML desfeita
    hmpb = next(u for u in p.unidades if u.id == "pronto-socorro-hmpb")
    assert hmpb.espera("adulto", "Pouco Urgente").minutos == 123
    assert hmpb.espera("adulto", "Pouco Urgente").fila == 27


def test_todas_as_unidades_do_portal_estao_no_config():
    assert {u.id for u in interpretar(HTML).unidades} == set(CFG["unidades"])


def test_layout_desconhecido_gera_erro():
    with pytest.raises(ValueError):
        interpretar("<html><body>manutenção</body></html>")


# ---------- conversa ----------
def test_saudacao_mostra_menu(conversa):
    r = responder(conversa, "u1", texto="oi")
    assert [b[0] for b in r[0]["botoes"]] == ["adulto", "infantil"]


def test_espera_adulto_em_ordem_de_menor_espera_verde(conversa):
    r = responder(conversa, "u1", texto="quanto tá a fila da UPA?")
    texto = r[0]["texto"]
    assert texto.index("PA Paraventi") < texto.index("UPA Cumbica") < texto.index("PA Alvorada")
    assert "🟢 2h03, 27 na fila" in texto  # HMPB
    assert "atualizado às 09:34" in texto
    lista = r[1]
    assert lista["tipo"] == "lista" and len(lista["itens"]) <= 10
    assert lista["itens"][-1][0] == "infantil"


def test_crianca_mostra_painel_infantil(conversa):
    r = responder(conversa, "u1", texto="meu filho está com febre")
    assert "(infantil)" in r[0]["texto"]
    assert "🟢 21 min, 3 na fila" in r[0]["texto"]  # HMPB infantil


def test_painel_velho_avisa(conversa):
    r = responder(conversa, "u1", botao="adulto", agora=COLETA + 3600)
    assert "não atualiza" in r[0]["texto"]


def test_sem_dados_manda_link_do_portal():
    r = asyncio.run(Conversa(CFG, Coletor()).responder("u1", botao="adulto"))
    assert CFG["fonte"] in r[0]["texto"]


def test_sinal_de_alarme_manda_ligar_192(conversa):
    r = responder(conversa, "u1", texto="estou com dor no peito")
    assert "192" in r[0]["texto"]


def test_trajeto_de_carro_tem_waze_e_google_maps(conversa):
    r = responder(conversa, "u1", botao="ir_upa-paulista")
    assert [b[0] for b in r[0]["botoes"]] == ["onibus", "carro"]
    r = responder(conversa, "u1", botao="carro")
    assert "waze.com/ul" in r[0]["texto"] and "travelmode=driving" in r[0]["texto"]


def test_nome_digitado_escolhe_a_unidade_e_onibus_abre_transporte_publico(conversa):
    r = responder(conversa, "u1", texto="quero ir na taboão")
    assert "UPA Taboão" in r[0]["texto"]
    r = responder(conversa, "u1", texto="vou de ônibus")
    assert "travelmode=transit" in r[0]["texto"]


def test_localizacao_ordena_por_distancia_quando_ha_coordenadas(conversa):
    conversa.cfg = json.loads(json.dumps(CFG))
    conversa.cfg["unidades"]["upa-cumbica"].update(lat=-23.4400, lng=-46.4600)
    conversa.cfg["unidades"]["p-a-paraventi"].update(lat=-23.4700, lng=-46.5600)
    r = responder(conversa, "u1", local=(-23.4401, -46.4601))
    assert r[0]["texto"].index("UPA Cumbica") < r[0]["texto"].index("PA Paraventi")
    assert "🚗 ~" in r[0]["texto"]


def test_onibus_com_chave_monta_passo_a_passo(conversa, monkeypatch):
    conversa.chave_mapas = "x"
    conversa.cfg = json.loads(json.dumps(CFG))
    conversa.cfg["unidades"]["upa-paulista"].update(lat=-23.46, lng=-46.53)

    async def falso(origem, unidade, chave):
        return {"minutos": 25, "passos": ["🚌 Pegue a linha *123*", "🚶 Ande 200 m até a unidade"]}

    monkeypatch.setattr(rotas, "trajeto_onibus", falso)
    responder(conversa, "u1", botao="ir_upa-paulista")
    r = responder(conversa, "u1", botao="onibus")
    assert r[-1]["tipo"] == "pedir_local"
    r = responder(conversa, "u1", local=(-23.45, -46.52))
    assert "linha *123*" in r[0]["texto"] and "25 min" in r[0]["texto"]


def test_resumo_da_routes_api():
    rota = {"duration": "1500s", "legs": [{"steps": [
        {"travelMode": "WALK", "distanceMeters": 150},
        {"travelMode": "TRANSIT", "transitDetails": {
            "headsign": "Pimentas", "stopCount": 8,
            "stopDetails": {"departureStop": {"name": "Av. Tiradentes"}, "arrivalStop": {"name": "Hospital"}},
            "transitLine": {"nameShort": "301", "vehicle": {"type": "BUS"}}}},
        {"travelMode": "WALK", "distanceMeters": 90},
    ]}]}
    r = rotas.resumir_transit(rota)
    assert r["minutos"] == 25
    assert r["passos"][1].startswith("🚌 Pegue a linha *301* (sentido Pimentas) no ponto Av. Tiradentes")


def test_audio_nao_le_links(conversa):
    responder(conversa, "u1", botao="ir_upa-paulista")
    fala = como_texto(responder(conversa, "u1", botao="carro"))
    assert "http" not in fala and "UPA Paulista" in fala


def test_payload_respeita_limites_do_whatsapp(conversa):
    for resposta in responder(conversa, "u1", botao="adulto"):
        p = montar_payload("5511999999999", resposta)
        if resposta["tipo"] == "lista":
            rows = p["interactive"]["action"]["sections"][0]["rows"]
            assert len(rows) <= 10 and all(len(row["title"]) <= 24 for row in rows)
        else:
            assert len(p["text"]["body"]) <= 4096
    botoes = montar_payload("5511999999999", responder(conversa, "u1", texto="oi")[0])
    assert all(len(b["reply"]["title"]) <= 20 for b in botoes["interactive"]["action"]["buttons"])
    pedir = montar_payload("5511", {"tipo": "pedir_local", "texto": "manda"})
    assert pedir["interactive"]["type"] == "location_request_message"


def test_webhook(monkeypatch):
    from app import main

    enviados = []

    async def falso_enviar(para, resposta):
        enviados.append((para, resposta))

    async def falsa_coleta():
        main.coletor.painel = interpretar(HTML)
        return main.coletor.painel

    monkeypatch.setattr(main.whatsapp, "enviar", falso_enviar)
    monkeypatch.setattr(main.coletor, "atualizar", falsa_coleta)
    with TestClient(main.app) as c:
        assert c.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": main.VERIFY_TOKEN, "hub.challenge": "42"}).text == "42"
        corpo = {"entry": [{"changes": [{"value": {"messages": [{"from": "5511", "type": "text", "text": {"body": "upa"}}]}}]}]}
        assert c.post("/webhook", json=corpo).status_code == 200
        assert enviados and "UPA Paulista" in enviados[0][1]["texto"]
        assert len(c.get("/fila").json()["unidades"]) == 11
