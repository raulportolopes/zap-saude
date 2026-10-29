"""Trajeto até a unidade: links para abrir no celular e, se houver chave, o passo a passo de ônibus.

Sem chave nenhuma o bot já funciona: manda o link do Google Maps no modo transporte público
(o app calcula as linhas de ônibus) e, para quem vai de carro, os links do Waze e do Google Maps.
Com GOOGLE_MAPS_API_KEY, a resposta de ônibus traz também as linhas, os pontos e o tempo
dentro do próprio WhatsApp (Routes API do Google, modo TRANSIT).
"""
from __future__ import annotations

import math
import os
import re
from urllib.parse import quote, urlencode

import httpx

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
CAMPOS = ",".join((
    "routes.duration",
    "routes.legs.steps.travelMode",
    "routes.legs.steps.staticDuration",
    "routes.legs.steps.distanceMeters",
    "routes.legs.steps.transitDetails",
))
VELOCIDADE_CARRO_KMH = 25  # média urbana para a estimativa sem API
FATOR_RUA = 1.35  # distância em linha reta -> distância pelas ruas


def distancia_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def minutos_carro(origem: tuple[float, float], destino: tuple[float, float]) -> int:
    """Estimativa simples, usada para ordenar as unidades quando a pessoa manda a localização."""
    return max(3, round(distancia_km(origem, destino) * FATOR_RUA / VELOCIDADE_CARRO_KMH * 60))


def _destino(unidade: dict) -> str:
    if unidade.get("lat") is not None and unidade.get("lng") is not None:
        return f"{unidade['lat']},{unidade['lng']}"
    if unidade.get("endereco"):
        return f"{unidade['endereco']}, Guarulhos - SP"
    return unidade.get("busca") or f"{unidade['nome']}, Guarulhos - SP"


def link_google_maps(unidade: dict, modo: str, origem: tuple[float, float] | None = None) -> str:
    """modo: "transit" (ônibus) ou "driving" (carro). Sem origem, o app usa a localização do celular."""
    params = {"api": "1", "destination": _destino(unidade), "travelmode": modo}
    if origem:
        params["origin"] = f"{origem[0]},{origem[1]}"
    return "https://www.google.com/maps/dir/?" + urlencode(params, quote_via=quote, safe=",")


def link_waze(unidade: dict) -> str:
    if unidade.get("lat") is not None and unidade.get("lng") is not None:
        return f"https://waze.com/ul?ll={unidade['lat']},{unidade['lng']}&navigate=yes"
    return "https://waze.com/ul?" + urlencode({"q": _destino(unidade), "navigate": "yes"}, quote_via=quote)


def _segundos(valor: str | None) -> int:
    m = re.fullmatch(r"(\d+)s", valor or "")
    return int(m[1]) if m else 0


def resumir_transit(rota: dict) -> dict:
    """Converte uma rota da Routes API em {"minutos", "passos": [texto, ...]}."""
    passos: list[str] = []
    caminhada = 0
    for leg in rota.get("legs", []):
        for s in leg.get("steps", []):
            if s.get("travelMode") == "WALK":
                caminhada += s.get("distanceMeters", 0)
                continue
            if caminhada:
                passos.append(f"🚶 Ande {caminhada} m")
                caminhada = 0
            t = s.get("transitDetails") or {}
            linha = t.get("transitLine", {})
            nome = linha.get("nameShort") or linha.get("name") or "ônibus"
            veiculo = linha.get("vehicle", {}).get("type", "BUS")
            icone = {"SUBWAY": "🚇", "HEAVY_RAIL": "🚆", "COMMUTER_TRAIN": "🚆", "RAIL": "🚆"}.get(veiculo, "🚌")
            sentido = f" (sentido {t['headsign']})" if t.get("headsign") else ""
            paradas = t.get("stopCount")
            sobe = t.get("stopDetails", {}).get("departureStop", {}).get("name", "")
            desce = t.get("stopDetails", {}).get("arrivalStop", {}).get("name", "")
            passos.append(
                f"{icone} Pegue a linha *{nome}*{sentido} no ponto {sobe}"
                + (f" e desça em {desce}" if desce else "")
                + (f" ({paradas} paradas)" if paradas else "")
            )
    if caminhada:
        passos.append(f"🚶 Ande {caminhada} m até a unidade")
    return {"minutos": max(1, round(_segundos(rota.get("duration")) / 60)), "passos": passos}


async def trajeto_onibus(origem: tuple[float, float], unidade: dict, chave: str | None = None) -> dict | None:
    """Passo a passo de transporte público. Devolve None sem chave, sem coordenadas ou se a API falhar."""
    chave = chave or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not chave or unidade.get("lat") is None:
        return None
    corpo = {
        "origin": {"location": {"latLng": {"latitude": origem[0], "longitude": origem[1]}}},
        "destination": {"location": {"latLng": {"latitude": unidade["lat"], "longitude": unidade["lng"]}}},
        "travelMode": "TRANSIT",
        "languageCode": "pt-BR",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(ROUTES_URL, json=corpo, headers={"X-Goog-Api-Key": chave, "X-Goog-FieldMask": CAMPOS})
            r.raise_for_status()
            rotas = r.json().get("routes") or []
    except (httpx.HTTPError, ValueError):
        return None
    return resumir_transit(rotas[0]) if rotas else None
