"""Fluxo da conversa, sem dependência do WhatsApp: recebe texto, botão ou localização e devolve respostas.

Cada resposta é um dict:
  {"tipo": "texto", "texto": ...}
  {"tipo": "botoes", "texto": ..., "botoes": [(id, titulo), ...]}         (até 3, título até 20 caracteres)
  {"tipo": "lista", "texto": ..., "botao": ..., "itens": [(id, titulo, descricao), ...]}  (até 10)
  {"tipo": "pedir_local", "texto": ...}                                    (botão "Enviar localização")
"""
from __future__ import annotations

import re
import time
import unicodedata

from . import rotas
from .siss import Coletor, Unidade, chave

EMOJI = {
    "Muito Urgente": "🟠",
    "Urgente": "🟡",
    "Pouco Urgente": "🟢",
    "Não Urgente": "🔵",
    "Sem Classificação": "⚪",
}
REFERENCIA = "Pouco Urgente"  # a classificação mais comum de quem procura o pronto-socorro sem gravidade
DADO_VELHO_S = 20 * 60  # a prefeitura atualiza a cada 5 min; acima disso, avisamos que pode estar defasado
LEGENDA = (
    "🟠 muito urgente · 🟡 urgente · 🟢 pouco urgente · 🔵 não urgente · ⚪ aguardando triagem\n"
    "Casos graves (vermelho) são atendidos na hora."
)
BOTOES_MENU = [("adulto", "Espera adulto"), ("infantil", "Espera infantil")]
BOTOES_TRANSPORTE = [("onibus", "🚌 De ônibus"), ("carro", "🚗 De carro")]


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def fmt_min(m: int) -> str:
    return f"{m} min" if m < 60 else f"{m // 60}h{m % 60:02d}"


class Conversa:
    def __init__(self, config: dict, coletor: Coletor, chave_mapas: str | None = None):
        self.cfg = config
        self.coletor = coletor
        self.chave_mapas = chave_mapas
        self.estados: dict[str, dict] = {}

    # ---------- dados ----------
    def unidades(self) -> list[dict]:
        """Unidades do portal com o que o config acrescenta (nome amigável, busca no mapa, coordenadas)."""
        painel = self.coletor.painel
        if not painel:
            return []
        extras = self.cfg.get("unidades", {})
        return [{"id": u.id, "nome": u.nome_portal.title(), **extras.get(u.id, {}), "portal": u} for u in painel.unidades]

    @staticmethod
    def _ref(u: Unidade, publico: str) -> tuple[int, int]:
        c = u.espera(publico, REFERENCIA)
        return (c.minutos, c.fila) if c else (0, 0)

    def ordenar(self, publico: str, origem: tuple[float, float] | None = None) -> list[dict]:
        itens = []
        for u in self.unidades():
            espera, fila = self._ref(u["portal"], publico)
            fila_total = sum(c.fila for c in u["portal"].paineis.get(publico, []))
            ida = rotas.minutos_carro(origem, (u["lat"], u["lng"])) if origem and u.get("lat") is not None else None
            itens.append({**u, "espera": espera, "fila": fila, "fila_total": fila_total, "ida": ida, "total": espera + (ida or 0)})
        if origem and any(i["ida"] is not None for i in itens):
            return sorted(itens, key=lambda i: (i["ida"] is None, i["total"], i["fila_total"]))
        return sorted(itens, key=lambda i: (i["espera"], i["fila_total"]))

    # ---------- entrada ----------
    async def responder(
        self, usuario: str, texto: str | None = None, botao: str | None = None,
        local: tuple[float, float] | None = None, agora: float | None = None,
    ) -> list[dict]:
        agora = agora or time.time()
        estado = self.estados.setdefault(usuario, {"etapa": "inicio", "publico": "adulto"})
        if local:
            estado["origem"] = local
            if estado.get("etapa") == "aguardando_local" and estado.get("unidade"):
                return await self._onibus(estado)
            return self._espera(estado, agora)
        acao = botao or self._intencao(texto or "")

        if acao == "alarme":
            return [{"tipo": "texto", "texto": (
                f"⚠️ Se for grave, ligue agora para o SAMU: {self.cfg['telefones']['samu']}.\n\n"
                "Dor forte no peito, falta de ar, desmaio, convulsão, fala enrolada ou fraqueza de um lado do corpo "
                "não esperam fila. Se alguém puder levar você, vá para o pronto-socorro mais próximo."
            )}]
        if acao in ("adulto", "infantil"):
            estado["publico"] = acao
            return self._espera(estado, agora)
        if acao and acao.startswith("ir_"):
            u = self._unidade(acao.removeprefix("ir_"))
            if not u:
                return self._espera(estado, agora)
            estado["unidade"] = u["id"]
            return [{"tipo": "botoes", "texto": f"Destino: *{u['nome']}*\nComo você vai?", "botoes": BOTOES_TRANSPORTE}]
        if acao in ("onibus", "carro"):
            if not estado.get("unidade"):
                return [{"tipo": "texto", "texto": "Primeiro escolha a unidade 👇"}] + self._espera(estado, agora)
            return await self._onibus(estado) if acao == "onibus" else self._carro(estado)
        return self._menu()

    def _intencao(self, texto: str) -> str | None:
        t = normalizar(texto)
        if not t:
            return None
        if any(p in t for p in ("192", "samu", "infarto", "nao consigo respirar", "falta de ar", "dor no peito", "desmai", "convuls")):
            return "alarme"
        for u in self.unidades():
            nomes = {normalizar(u["nome"]), normalizar(u["portal"].nome_portal), *map(normalizar, u.get("apelidos", []))}
            curtos = {re.sub(r"^(upa|pa|p\.a|pronto-socorro|pronto socorro)\s+-?\s*", "", n) for n in nomes}
            if any(n and n in t for n in nomes | curtos):
                return f"ir_{u['id']}"
        if re.search(r"\b(onibus|busao|bus|metro|trem|transporte)\b", t):
            return "onibus"
        if re.search(r"\b(carro|waze|maps|uber|moto|dirigindo)\b", t):
            return "carro"
        if re.search(r"\b(infantil|crianca|filh[oa]|bebe|nenem|pediatr)", t):
            return "infantil"
        if re.search(r"\b(upas?|ps|pa|pronto\w*|espera|fila|tempo|demora\w*|adulto|onde|vazi\w*)\b", t):
            return "adulto"
        return None

    def _unidade(self, uid: str) -> dict | None:
        return next((u for u in self.unidades() if u["id"] == chave(uid) or u["id"] == uid), None)

    # ---------- respostas ----------
    def _menu(self) -> list[dict]:
        return [{
            "tipo": "botoes",
            "texto": (
                f"Olá! Este é o Zap-Saúde de {self.cfg['cidade']}. Eu mostro quanto tempo está a espera nas UPAs, "
                "prontos-socorros e prontos-atendimentos da cidade e o caminho até lá.\n\n"
                "Você também pode mandar um áudio ou a sua localização."
            ),
            "botoes": BOTOES_MENU,
        }]

    def _sem_dados(self) -> list[dict]:
        return [{"tipo": "texto", "texto": (
            "Não consegui ler o painel da prefeitura agora. Tente de novo em alguns minutos ou veja direto em "
            f"{self.cfg['fonte']}\n\nSe for grave, ligue {self.cfg['telefones']['samu']}."
        )}]

    @staticmethod
    def _linha_cores(u: Unidade, publico: str) -> str:
        partes = [
            f"{EMOJI.get(c.nome, '•')} {fmt_min(c.minutos)}, {c.fila} na fila"
            for c in u.paineis.get(publico, []) if c.fila > 0
        ]
        return " | ".join(partes) or "✅ sem fila agora"

    def _espera(self, estado: dict, agora: float) -> list[dict]:
        publico = estado["publico"]
        painel = self.coletor.painel
        if not painel:
            return self._sem_dados()
        origem = estado.get("origem")
        itens = self.ordenar(publico, origem)
        perto = origem and any(i["ida"] is not None for i in itens)
        linhas = []
        for i in itens:
            ida = f" · 🚗 ~{fmt_min(i['ida'])}" if i["ida"] is not None else ""
            linhas.append(f"*{i['nome']}*{ida}\n{self._linha_cores(i['portal'], publico)}")
        ordem = "tempo de carro + espera 🟢" if perto else "menor espera para casos 🟢 pouco urgentes"
        hora = painel.atualizado_em[-8:-3] if painel.atualizado_em else time.strftime("%H:%M", time.localtime(painel.coletado_em))
        rodape = f"Fonte: Prefeitura de {self.cfg['cidade']}, atualizado às {hora}."
        if agora - painel.coletado_em > DADO_VELHO_S:
            rodape += " ⚠️ O painel não atualiza há um tempo; os números podem ter mudado."
        texto = (
            f"⏱️ Espera agora ({'infantil' if publico == 'infantil' else 'adulto'}), em ordem de {ordem}:\n\n"
            + "\n\n".join(linhas) + f"\n\n{LEGENDA}\n{rodape}"
        )
        outro = "adulto" if publico == "infantil" else "infantil"
        lista = {
            "tipo": "lista",
            "texto": "Quer o caminho até alguma delas? Escolha abaixo ou digite o nome.",
            "botao": "Ver trajeto",
            "itens": [
                (f"ir_{i['id']}", i["nome"], f"🟢 {fmt_min(i['espera'])}, {i['fila']} na fila" if i["fila"] else "🟢 sem fila")
                for i in itens[:9]
            ] + [(outro, f"Ver espera {outro}", "")],
        }
        return [{"tipo": "texto", "texto": texto}, lista]

    def _carro(self, estado: dict) -> list[dict]:
        u = self._unidade(estado["unidade"])
        origem = estado.get("origem")
        estimativa = ""
        if origem and u.get("lat") is not None:
            estimativa = f" (cerca de {fmt_min(rotas.minutos_carro(origem, (u['lat'], u['lng'])))} sem trânsito)"
        return [{"tipo": "texto", "texto": (
            f"🚗 De carro até *{u['nome']}*{estimativa}:\n\n"
            f"Abrir no Waze: {rotas.link_waze(u)}\n\n"
            f"Abrir no Google Maps: {rotas.link_google_maps(u, 'driving', origem)}"
        )}]

    async def _onibus(self, estado: dict) -> list[dict]:
        u = self._unidade(estado["unidade"])
        origem = estado.get("origem")
        link = rotas.link_google_maps(u, "transit", origem)
        estado["etapa"] = "inicio"
        if origem:
            rota = await rotas.trajeto_onibus(origem, u, self.chave_mapas)
            if rota and rota["passos"]:
                passos = "\n".join(f"{n}. {p}" for n, p in enumerate(rota["passos"], 1))
                return [{"tipo": "texto", "texto": (
                    f"🚌 De ônibus até *{u['nome']}*: cerca de {fmt_min(rota['minutos'])}.\n\n{passos}\n\n"
                    f"Ver no mapa, com horários: {link}"
                )}]
        respostas = [{"tipo": "texto", "texto": (
            f"🚌 De ônibus até *{u['nome']}*: abra o link e o Google Maps mostra as linhas, os pontos e os horários.\n\n{link}"
        )}]
        if not origem and self.chave_mapas and u.get("lat") is not None:
            estado["etapa"] = "aguardando_local"
            respostas.append({"tipo": "pedir_local", "texto": "Se preferir, mande sua localização e eu digo aqui quais ônibus pegar."})
        return respostas


def como_texto(respostas: list[dict]) -> str:
    """Versão em texto corrido, usada para gerar a resposta em áudio. Listas e links não são lidos."""
    partes = []
    for r in respostas:
        if r["tipo"] in ("lista", "pedir_local"):
            continue
        t = re.sub(r"(Abrir no \w+( \w+)?|Ver no mapa[^:]*):?\s*https?://\S+", "", r["texto"])
        t = re.sub(r"https?://\S+", "", t).replace("*", "").replace("_", "")
        for emoji, palavra in (("🟠", "laranja"), ("🟡", "amarelo"), ("🟢", "verde"), ("🔵", "azul"), ("⚪", "sem triagem")):
            t = t.replace(emoji, palavra)
        t = re.sub(r"[⏱️✅⚠️🚗🚌🚶🚇🚆]", "", t)
        t = re.sub(r"(\d+)h(\d\d)", lambda m: f"{m[1]} hora{'s' if m[1] != '1' else ''} e {int(m[2])} minutos", t)
        t = re.sub(r"(\d+) min\b", r"\1 minutos", t)
        partes.append(t.replace(" · ", ", ").replace(" | ", "; "))
    return "\n".join(p.strip() for p in partes if p.strip())
