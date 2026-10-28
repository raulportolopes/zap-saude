"""Fluxo da conversa, sem dependência do WhatsApp: recebe texto ou botão, devolve respostas.

Cada resposta é um dict:
  {"tipo": "texto", "texto": ...}
  {"tipo": "botoes", "texto": ..., "botoes": [(id, titulo), ...]}         (até 3, título até 20 caracteres)
  {"tipo": "lista", "texto": ..., "botao": ..., "itens": [(id, titulo, descricao), ...]}  (até 10)
"""
from __future__ import annotations

import re
import time
import unicodedata

from .fila import Repositorio

SINAIS_ALARME = (
    "• dor forte ou aperto no peito\n"
    "• falta de ar\n"
    "• desmaio, confusão ou convulsão\n"
    "• fala enrolada, boca torta ou fraqueza de um lado do corpo\n"
    "• sangramento que não para"
)

BOTOES_MENU = [("espera", "Ver espera agora"), ("onde_ir", "Para onde eu vou?")]


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def fmt_min(m: int) -> str:
    return f"{m} min" if m < 60 else f"{m // 60}h{m % 60:02d}"


class Conversa:
    def __init__(self, repo: Repositorio):
        self.repo = repo
        self.cfg = repo.config
        self.estados: dict[str, dict] = {}
        self.alertas: list[dict] = []  # {"usuario", "unidade", "limite"}

    # ---------- entrada ----------
    def responder(self, usuario: str, texto: str | None = None, botao: str | None = None, agora: float | None = None) -> list[dict]:
        agora = agora or time.time()
        estado = self.estados.setdefault(usuario, {"etapa": "inicio"})
        acao = botao or self._intencao(texto or "", estado)

        if acao == "espera":
            return self._espera(agora)
        if acao == "onde_ir":
            estado["etapa"] = "triagem"
            return [{
                "tipo": "botoes",
                "texto": f"Antes, uma pergunta rápida. Você tem algum destes sinais agora?\n\n{SINAIS_ALARME}",
                "botoes": [("alarme_sim", "Sim, tenho"), ("alarme_nao", "Não")],
            }]
        if acao == "alarme_sim":
            estado["etapa"] = "inicio"
            return [{"tipo": "texto", "texto": (
                f"⚠️ Ligue agora para o SAMU: {self.cfg['telefones']['samu']}.\n\n"
                "Não espere fila nem compare unidades. Se alguém puder levar você, vá para a UPA mais próxima."
            )}]
        if acao == "alarme_nao":
            estado["etapa"] = "regiao"
            return [self._pergunta_regiao()]
        if acao and acao.startswith("regiao_"):
            estado["regiao"] = acao.removeprefix("regiao_")
            estado["etapa"] = "inicio"
            return self._recomendar(estado["regiao"], agora, estado)
        if acao == "avisar":
            return self._criar_alerta(usuario, estado)
        return self._menu()

    def _intencao(self, texto: str, estado: dict) -> str | None:
        t = normalizar(texto)
        if not t:
            return None
        if estado.get("etapa") == "triagem":
            if t in {"sim", "s", "tenho", "sim tenho"}:
                return "alarme_sim"
            if t in {"nao", "n", "nao tenho"}:
                return "alarme_nao"
        for r in self.cfg["regioes"]:
            nome = normalizar(r["nome"]).replace(" e regiao", "")
            if nome in t:
                return f"regiao_{r['id']}"
        if any(p in t for p in ("192", "samu", "infarto", "nao consigo respirar", "falta de ar", "dor no peito", "desmai")):
            return "alarme_sim"
        if any(p in t for p in ("onde", "qual upa", "pra onde", "para onde", "ir ")):
            return "onde_ir"
        if any(p in t for p in ("upa", "espera", "fila", "demora", "tempo")):
            return "espera"
        if "avis" in t:
            return "avisar"
        return None

    # ---------- respostas ----------
    def _menu(self) -> list[dict]:
        return [{
            "tipo": "botoes",
            "texto": (
                f"Olá! Este é o Zap-Saúde de {self.cfg['cidade']}. Eu mostro a espera nas UPAs e indico a melhor "
                "unidade para você agora.\n\nVocê também pode mandar um áudio."
            ),
            "botoes": BOTOES_MENU,
        }]

    def _espera(self, agora: float) -> list[dict]:
        nomes = {u["id"]: u["nome"] for u in self.cfg["unidades"]}
        filas = sorted(self.repo.atual(agora), key=lambda f: f.espera["verde"])
        linhas = []
        for f in filas:
            e = f.espera
            linhas.append(
                f"*{nomes[f.unidade]}*\n🟡 {fmt_min(e['amarelo'])} · 🟢 {fmt_min(e['verde'])} · 🔵 {fmt_min(e['azul'])}"
            )
        hora = time.strftime("%H:%M", time.localtime(max(f.atualizado_em for f in filas)))
        simulado = any(f.origem == "simulado" for f in filas)
        rodape = f"Atualizado às {hora}." + (" Dados simulados (protótipo)." if simulado else "")
        return [{
            "tipo": "botoes",
            "texto": (
                "⏱️ Espera estimada nas UPAs de agora, por cor da triagem:\n\n" + "\n\n".join(linhas)
                + "\n\n🟡 urgente · 🟢 pouco urgente · 🔵 não urgente\nCasos vermelhos são atendidos na hora.\n"
                + rodape
            ),
            "botoes": [("onde_ir", "Para onde eu vou?")],
        }]

    def _pergunta_regiao(self) -> dict:
        return {
            "tipo": "lista",
            "texto": "Certo. Em que região de Mauá você está agora?",
            "botao": "Escolher região",
            "itens": [(f"regiao_{r['id']}", r["nome"][:24], "") for r in self.cfg["regioes"]],
        }

    def ranking(self, regiao: str, agora: float) -> list[dict]:
        nomes = {u["id"]: u["nome"] for u in self.cfg["unidades"]}
        desloc = self.cfg["deslocamento_min"][regiao]
        itens = []
        for f in self.repo.atual(agora):
            ida = desloc[f.unidade]
            itens.append({
                "unidade": f.unidade, "nome": nomes[f.unidade], "espera": f.espera["verde"],
                "ida": ida, "total": f.espera["verde"] + ida, "origem": f.origem,
            })
        return sorted(itens, key=lambda i: i["total"])

    def _ubs_aberta(self, agora: float) -> bool:
        lt = time.localtime(agora)
        u = self.cfg["ubs"]
        return lt.tm_wday in u["dias_uteis"] and u["abre"] <= lt.tm_hour < u["fecha"]

    def _recomendar(self, regiao: str, agora: float, estado: dict) -> list[dict]:
        rank = self.ranking(regiao, agora)
        melhor = rank[0]
        estado["ultima_sugestao"] = melhor["unidade"]
        linhas = [
            f"{i + 1}. *{r['nome']}*: cerca de {fmt_min(r['total'])} no total "
            f"({fmt_min(r['ida'])} de deslocamento + {fmt_min(r['espera'])} de espera)"
            for i, r in enumerate(rank[:3])
        ]
        texto = (
            f"✅ A melhor opção agora é a *{melhor['nome']}*.\n\n" + "\n".join(linhas)
            + "\n\nA espera considera a classificação verde, a mais comum para quem não tem sinais de alarme. "
            "Na UPA, a enfermagem faz a triagem e pode mudar a ordem."
        )
        if self._ubs_aberta(agora):
            texto += (
                "\n\n💡 Se for algo leve (resfriado, receita, exame, dor antiga), a UBS do seu bairro "
                f"atende {self.cfg['ubs']['horario']} e costuma ser mais rápida."
            )
        if melhor["origem"] == "simulado":
            texto += "\n\n_Dados simulados (protótipo)._"
        return [{
            "tipo": "botoes", "texto": texto,
            "botoes": [("avisar", "Avisar se baixar"), ("espera", "Ver todas as UPAs")],
        }]

    def _criar_alerta(self, usuario: str, estado: dict) -> list[dict]:
        unidade = estado.get("ultima_sugestao")
        if not unidade:
            return [{"tipo": "texto", "texto": "Primeiro me diga para onde você vai: toque em \"Para onde eu vou?\"."}] + self._menu()
        espera = next(f.espera["verde"] for f in self.repo.atual() if f.unidade == unidade)
        limite = max(15, round(espera * 0.7))
        self.alertas = [a for a in self.alertas if a["usuario"] != usuario]
        self.alertas.append({"usuario": usuario, "unidade": unidade, "limite": limite})
        nome = next(u["nome"] for u in self.cfg["unidades"] if u["id"] == unidade)
        return [{"tipo": "texto", "texto": f"🔔 Combinado. Aviso você quando a espera na {nome} ficar abaixo de {fmt_min(limite)}."}]

    def verificar_alertas(self, agora: float | None = None) -> list[tuple[str, dict]]:
        """Chamado a cada poucos minutos. Devolve (usuario, resposta) dos alertas disparados e os remove."""
        filas = {f.unidade: f for f in self.repo.atual(agora)}
        nomes = {u["id"]: u["nome"] for u in self.cfg["unidades"]}
        disparados, restantes = [], []
        for a in self.alertas:
            espera = filas[a["unidade"]].espera["verde"]
            if espera <= a["limite"]:
                disparados.append((a["usuario"], {
                    "tipo": "texto",
                    "texto": f"🔔 A espera na {nomes[a['unidade']]} baixou para {fmt_min(espera)}. Se ainda precisar, é uma boa hora.",
                }))
            else:
                restantes.append(a)
        self.alertas = restantes
        return disparados


def como_texto(respostas: list[dict]) -> str:
    """Versão em texto corrido, usada para gerar a resposta em áudio."""
    partes = []
    for r in respostas:
        t = r["texto"].replace("*", "").replace("_", "")
        for emoji, palavra in (("🟡", "amarelo"), ("🟢", "verde"), ("🔵", "azul"), ("⏱️", ""), ("✅", ""), ("💡", ""), ("⚠️", ""), ("🔔", "")):
            t = t.replace(emoji, palavra)
        t = re.sub(r"(\d+)h(\d\d)", lambda m: f"{m[1]} hora{'s' if m[1] != '1' else ''} e {int(m[2])} minutos", t)
        partes.append(t.replace(" · ", ", ").replace("min", "minutos").replace("minutosutos", "minutos"))
    return "\n".join(partes)
