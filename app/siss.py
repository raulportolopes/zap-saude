"""Coletor do tempo de espera no portal SISS Online da prefeitura (o mesmo sistema do VAR de Santo André).

O portal de Guarulhos não tem API JSON: a página é montada no servidor (ASP.NET MVC) e já traz
os dados no HTML. Cada unidade é um card com o nome em <h5 class="mb-0">, dois painéis
(ATENDIMENTO ADULTO e ATENDIMENTO INFANTIL) e, em cada painel, cinco caixas de classificação
com o tempo ("⏱ 12 MIN." ou "⏱ 2 H 3 MIN.") e a fila ("👥 Fila: 27").
A prefeitura atualiza a página a cada 5 minutos; o coletor busca no mesmo ritmo.
"""
from __future__ import annotations

import html
import re
import time
import unicodedata
from dataclasses import dataclass, field

import httpx

URL_GUARULHOS = "https://guarulhos-portal.sissonline.com.br/servicos/tempo-espera-ps/estabelecimentos"

# ordem e nome usados no portal
CLASSIFICACOES = ("Muito Urgente", "Urgente", "Pouco Urgente", "Não Urgente", "Sem Classificação")

_UNIDADE = re.compile(r'<h5 class="mb-0">\s*(.*?)\s*</h5>', re.S)
_PAINEL = re.compile(r"<h6[^>]*>\s*ATENDIMENTO\s+(ADULTO|INFANTIL)(.*?)</h6>", re.S | re.I)
_CAIXA = re.compile(
    r'<div class="titulo"[^>]*>\s*([^<]+?)\s*</div>\s*<div class="info[^"]*">\s*⏱\s*([^<]*?)\s*<br\s*/?>\s*👥\s*Fila:\s*(\d+)',
    re.S,
)
_DATA = re.compile(r"(\d{2}/\d{2}/\d{4} \d{2}:\d{2}(?::\d{2})?)")
_ULTIMA = re.compile(r"Última atualização:\s*(?:<[^>]+>\s*)*(\d{2}/\d{2}/\d{4} \d{2}:\d{2}(?::\d{2})?)", re.S)


def minutos(texto: str) -> int:
    """"12 MIN." -> 12, "2 H 3 MIN." -> 123, "1 H" -> 60."""
    t = texto.upper()
    h = re.search(r"(\d+)\s*H", t)
    m = re.search(r"(\d+)\s*MIN", t)
    return (int(h[1]) * 60 if h else 0) + (int(m[1]) if m else 0)


def chave(nome: str) -> str:
    """Nome do portal -> identificador estável: "UPA TABOÃO" -> "upa-taboao"."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento).strip("-")


@dataclass
class Classificacao:
    nome: str
    minutos: int
    fila: int


@dataclass
class Unidade:
    nome_portal: str
    paineis: dict[str, list[Classificacao]] = field(default_factory=dict)  # "adulto"/"infantil"
    atualizado_em: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return chave(self.nome_portal)

    def espera(self, publico: str, classificacao: str) -> Classificacao | None:
        return next((c for c in self.paineis.get(publico, []) if c.nome == classificacao), None)


@dataclass
class Painel:
    unidades: list[Unidade]
    atualizado_em: str  # "29/10/2026 09:34:59", horário de Brasília, como o portal mostra
    coletado_em: float  # time.time() de quando o servidor buscou


def interpretar(pagina: str, coletado_em: float | None = None) -> Painel:
    pedacos = _UNIDADE.split(pagina)
    # split com grupo: [antes, nome1, corpo1, nome2, corpo2, ...]
    unidades = []
    for nome, corpo in zip(pedacos[1::2], pedacos[2::2]):
        u = Unidade(html.unescape(re.sub(r"<[^>]+>", "", nome)).strip())
        blocos = _PAINEL.split(corpo)
        # [antes, publico1, cabecalho1, corpo1, publico2, ...]
        for publico, cabecalho, conteudo in zip(blocos[1::3], blocos[2::3], blocos[3::3]):
            p = publico.lower()
            u.paineis[p] = [
                Classificacao(html.unescape(t).strip(), minutos(tempo), int(fila))
                for t, tempo, fila in _CAIXA.findall(conteudo)
            ]
            if d := _DATA.search(cabecalho):
                u.atualizado_em[p] = d[1]
        if u.paineis:
            unidades.append(u)
    if not unidades:
        raise ValueError("nenhuma unidade encontrada: o layout do portal pode ter mudado")
    ultima = _ULTIMA.search(pagina)
    return Painel(unidades, ultima[1] if ultima else "", coletado_em or time.time())


class Coletor:
    """Guarda o último painel lido. Se o portal falhar, mantém o anterior e registra o erro."""

    def __init__(self, url: str = URL_GUARULHOS):
        self.url = url
        self.painel: Painel | None = None
        self.erro: str | None = None

    async def atualizar(self) -> Painel | None:
        try:
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0 (Zap-Saude)"}) as c:
                r = await c.get(self.url)
                r.raise_for_status()
            self.painel = interpretar(r.text)
            self.erro = None
        except (httpx.HTTPError, ValueError) as e:
            self.erro = str(e) or e.__class__.__name__
        return self.painel


if __name__ == "__main__":  # python -m app.siss  -> mostra o painel atual, para conferir o coletor
    import asyncio

    c = Coletor()
    p = asyncio.run(c.atualizar())
    if not p:
        raise SystemExit(f"falha: {c.erro}")
    print(f"Última atualização: {p.atualizado_em}\n")
    for u in p.unidades:
        print(u.nome_portal)
        for publico, caixas in u.paineis.items():
            print(f"  {publico:9}", " | ".join(f"{c.nome}: {c.minutos} min, fila {c.fila}" for c in caixas))
