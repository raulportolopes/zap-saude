"""Filas das unidades: guarda o último dado de cada UPA e gera dados simulados.

Nível 1 (manual): a recepção chama POST /admin/fila e grava a fila.
Sem dado recente, o protótipo usa a simulação abaixo, marcada como "simulado".
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

CORES = ("amarelo", "verde", "azul")
VALIDADE_S = 30 * 60  # dado com mais de 30 min não é mostrado como atual


@dataclass
class Fila:
    unidade: str
    espera: dict[str, int]  # minutos por cor de risco
    atualizado_em: float
    origem: str  # "manual", "coletor" ou "simulado"


def carregar_config(caminho: str | Path) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def espera_simulada(unidade: dict, agora: float) -> dict[str, int]:
    """Curva do dia: pico às 10h e às 20h, vale de madrugada. Muda a cada 5 min."""
    hora = time.localtime(agora).tm_hour + time.localtime(agora).tm_min / 60
    pico = 0.55 + 0.45 * max(math.exp(-((hora - 10) ** 2) / 8), math.exp(-((hora - 20) ** 2) / 6))
    rnd = random.Random(f"{unidade['id']}-{int(agora // 300)}")
    verde = 120 * pico * unidade.get("fator", 1.0) * rnd.uniform(0.85, 1.15)
    return {
        "amarelo": max(5, round(verde * 0.35)),
        "verde": max(10, round(verde)),
        "azul": max(15, round(verde * 1.45)),
    }


class Repositorio:
    def __init__(self, config: dict, caminho_db: str = ":memory:"):
        self.config = config
        self.db = sqlite3.connect(caminho_db, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS fila (unidade TEXT PRIMARY KEY, espera TEXT, atualizado_em REAL, origem TEXT)"
        )

    def gravar(self, unidade: str, espera: dict[str, int], origem: str = "manual", quando: float | None = None):
        if unidade not in {u["id"] for u in self.config["unidades"]}:
            raise ValueError(f"unidade desconhecida: {unidade}")
        faltando = set(CORES) - set(espera)
        if faltando:
            raise ValueError(f"faltam cores: {sorted(faltando)}")
        self.db.execute(
            "INSERT OR REPLACE INTO fila VALUES (?, ?, ?, ?)",
            (unidade, json.dumps({c: int(espera[c]) for c in CORES}), quando or time.time(), origem),
        )
        self.db.commit()

    def atual(self, agora: float | None = None) -> list[Fila]:
        agora = agora or time.time()
        gravadas = {
            r[0]: Fila(r[0], json.loads(r[1]), r[2], r[3])
            for r in self.db.execute("SELECT unidade, espera, atualizado_em, origem FROM fila")
        }
        filas = []
        for u in self.config["unidades"]:
            f = gravadas.get(u["id"])
            if f is None or agora - f.atualizado_em > VALIDADE_S:
                f = Fila(u["id"], espera_simulada(u, agora), agora - (agora % 300), "simulado")
            filas.append(f)
        return filas
