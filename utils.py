# -*- coding: utf-8 -*-
"""
utils.py
========

Infraestrutura compartilhada: normalização de texto, extração e validação de
telefones/e-mails/CNPJ, comparação de nomes de empresas, cliente HTTP com
rotação de User-Agent e detecção de captcha, controle de ritmo (rate limit) e
configuração de logging.

Regra de ouro deste módulo: **extrair e validar, nunca inferir**. Todas as
funções recebem texto real obtido de uma fonte pública e devolvem apenas o que
estiver literalmente presente. Não existe geração, completamento ou "chute" de
dados em nenhum ponto.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests

import config

logger = logging.getLogger("localizador.utils")


# ===========================================================================
# Exceções
# ===========================================================================

class LocalizadorError(Exception):
    """Exceção base da aplicação."""


class CaptchaDetectado(LocalizadorError):
    """
    Levantada quando uma fonte responde com desafio anti-robô.

    O motor de pesquisa trata esta exceção pausando o processamento e avisando
    o usuário, conforme exigido no escopo.
    """

    def __init__(self, url: str, detalhe: str = "") -> None:
        self.url = url
        self.detalhe = detalhe
        super().__init__(f"Captcha/bloqueio detectado em {url}. {detalhe}".strip())


class BloqueioTemporario(LocalizadorError):
    """Levantada em respostas 429/503 — indica necessidade de recuar o ritmo."""


class OperacaoCancelada(LocalizadorError):
    """Levantada quando o usuário cancela a execução."""


# ===========================================================================
# Logging
# ===========================================================================

def configurar_logging(
    pasta_saida: Path, nivel: int = logging.INFO, fila_ui=None
) -> logging.Logger:
    """
    Configura o logger raiz da aplicação.

    Args:
        pasta_saida: Pasta onde ``log_tecnico.txt`` será gravado.
        nivel: Nível mínimo registrado.
        fila_ui: ``queue.Queue`` opcional que recebe as mensagens formatadas
            para exibição em tempo real na interface gráfica.

    Returns:
        O logger raiz da aplicação, já configurado.
    """
    pasta_saida.mkdir(parents=True, exist_ok=True)
    raiz = logging.getLogger("localizador")
    raiz.setLevel(nivel)
    raiz.handlers.clear()
    raiz.propagate = False

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    arquivo = logging.FileHandler(
        pasta_saida / config.ARQ_LOG_TECNICO, encoding="utf-8"
    )
    arquivo.setFormatter(formato)
    raiz.addHandler(arquivo)

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(levelname)-8s | %(message)s")
    )
    raiz.addHandler(console)

    if fila_ui is not None:
        raiz.addHandler(_HandlerFila(fila_ui))

    # Silencia bibliotecas verbosas de terceiros.
    for ruidoso in ("urllib3", "requests", "asyncio", "PIL"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)

    return raiz


class _HandlerFila(logging.Handler):
    """Handler que empurra registros formatados para uma fila consumida pela UI."""

    def __init__(self, fila) -> None:
        super().__init__()
        self.fila = fila
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.fila.put_nowait((record.levelno, self.format(record)))
        except Exception:  # pragma: no cover - fila cheia não pode quebrar o app
            pass


# ===========================================================================
# Normalização de texto
# ===========================================================================

_RE_ESPACOS = re.compile(r"\s+")
_RE_NAO_ALFANUM = re.compile(r"[^0-9a-z ]+")


def remover_acentos(texto: str) -> str:
    """Remove acentuação preservando as letras base ('Ação' -> 'Acao')."""
    if not texto:
        return ""
    normalizado = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in normalizado if not unicodedata.combining(c))


def normalizar(texto: str) -> str:
    """
    Normaliza texto para comparação: sem acento, minúsculo, sem pontuação e
    com espaços colapsados.
    """
    if not texto:
        return ""
    base = remover_acentos(str(texto)).lower()
    base = base.replace("&", " e ").replace("/", " ")
    base = _RE_NAO_ALFANUM.sub(" ", base)
    return _RE_ESPACOS.sub(" ", base).strip()


def limpar_espacos(texto: str) -> str:
    """Colapsa espaços/brancos preservando acentuação e caixa."""
    return _RE_ESPACOS.sub(" ", str(texto or "")).strip()


def so_digitos(texto: str) -> str:
    """Retorna apenas os dígitos presentes no texto."""
    return "".join(c for c in str(texto or "") if c.isdigit())


def truncar(texto: str, limite: int = 160) -> str:
    """Corta o texto no limite informado, adicionando reticências."""
    limpo = limpar_espacos(texto)
    return limpo if len(limpo) <= limite else limpo[: limite - 1] + "…"


# ===========================================================================
# Comparação de nomes de empresas
# ===========================================================================

class NomeMatcher:
    """
    Comparador de razões sociais.

    A comparação combina três sinais:

    1. Similaridade de sequência (``difflib``) entre os nomes normalizados;
    2. Sobreposição de tokens (Jaccard) ignorando palavras genéricas do ramo;
    3. Presença dos *tokens distintivos* (as palavras que realmente identificam
       a empresa, como sobrenomes e marcas).

    O objetivo é ser conservador: nomes parecidos mas com tokens distintivos
    diferentes ("ANCONA BUFFET" x "ANCONA TRANSPORTES") não devem ser tratados
    como a mesma empresa sem confirmação adicional.
    """

    def __init__(self, palavras_genericas: Optional[Set[str]] = None) -> None:
        self.genericas = palavras_genericas or config.PALAVRAS_GENERICAS

    # -- normalização ---------------------------------------------------

    def normalizar_razao(self, nome: str) -> str:
        """Normaliza a razão social removendo sufixos societários."""
        base = normalizar(nome)
        if not base:
            return ""
        # Remove sufixos societários (podem aparecer no meio: "LTDA - ME").
        for sufixo in sorted(config.SUFIXOS_SOCIETARIOS, key=len, reverse=True):
            alvo = normalizar(sufixo)
            if not alvo:
                continue
            base = re.sub(rf"(?:^|\s){re.escape(alvo)}(?=\s|$)", " ", base)
        return _RE_ESPACOS.sub(" ", base).strip()

    def tokens(self, nome: str) -> List[str]:
        """Tokens da razão social normalizada, sem tokens de 1 caractere."""
        return [t for t in self.normalizar_razao(nome).split() if len(t) > 1]

    def tokens_distintivos(self, nome: str) -> Set[str]:
        """Tokens que não são palavras genéricas do ramo."""
        return {t for t in self.tokens(nome) if t not in self.genericas}

    # -- similaridade ---------------------------------------------------

    def similaridade(self, a: str, b: str) -> float:
        """
        Score de 0 a 1 entre duas razões sociais.

        Combina similaridade de sequência (40%) com sobreposição de tokens
        distintivos (60%), pois os tokens distintivos são o que de fato
        identifica a empresa.
        """
        na, nb = self.normalizar_razao(a), self.normalizar_razao(b)
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0

        seq = SequenceMatcher(None, na, nb).ratio()

        da, db = self.tokens_distintivos(a), self.tokens_distintivos(b)
        if da and db:
            # Cobertura do menor conjunto: nomes truncados na planilha
            # (ex.: "ALAMEDAS OURO VERDE EMPREENDIMENTOS IMOB") não podem ser
            # penalizados por lhes faltarem palavras finais.
            intersec = len(da & db)
            cobertura = intersec / min(len(da), len(db))
            jaccard = intersec / len(da | db)
            tokens_score = 0.7 * cobertura + 0.3 * jaccard
        else:
            ta, tb = set(self.tokens(a)), set(self.tokens(b))
            tokens_score = (
                len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
            )

        # Similaridade parcial: nome da planilha contido no nome da fonte.
        if na in nb or nb in na:
            seq = max(seq, 0.92)

        return round(0.4 * seq + 0.6 * tokens_score, 4)

    def compativel(self, buscado: str, encontrado: str) -> Tuple[bool, float]:
        """
        Diz se dois nomes podem ser considerados a mesma empresa.

        Returns:
            Tupla ``(aceito, score)``. ``aceito`` exige o limiar de aceite mais
            um token distintivo em comum — **ou** nomes normalizados idênticos.

            A exceção para nomes idênticos é necessária: razões sociais
            compostas só por palavras genéricas ("A & A EXECUTIVA TRANSPORTES")
            não têm token distintivo algum, mas uma coincidência exata com o
            registro da Receita Federal é confirmação legítima.
        """
        score = self.similaridade(buscado, encontrado)
        distintivos_comuns = self.tokens_distintivos(buscado) & self.tokens_distintivos(
            encontrado
        )
        identicos = (
            self.normalizar_razao(buscado) == self.normalizar_razao(encontrado)
            and bool(self.normalizar_razao(buscado))
        )
        aceito = score >= config.SIMILARIDADE_MINIMA_ACEITE and (
            bool(distintivos_comuns) or identicos
        )
        return aceito, score


# Instância padrão reutilizável (o matcher não guarda estado mutável).
matcher = NomeMatcher()


def cidade_confere(cidade_planilha: str, texto_fonte: str) -> Optional[bool]:
    """
    Verifica se a cidade da planilha aparece no texto da fonte.

    Returns:
        ``True`` se a cidade foi encontrada, ``None`` se o texto não permite
        concluir nada (nenhuma cidade reconhecível) e ``False`` se o texto cita
        explicitamente outra cidade da mesma carteira sem citar a esperada.
    """
    alvo = normalizar(cidade_planilha)
    if not alvo:
        return None
    texto = normalizar(texto_fonte)
    if not texto:
        return None
    if alvo in texto:
        return True
    # Aceita variações comuns ("santa barbara d oeste" x "santa barbara doeste").
    if alvo.replace(" ", "") in texto.replace(" ", ""):
        return True
    return None


# ===========================================================================
# Telefones
# ===========================================================================

# Captura números brasileiros exigindo DDD explícito (entre parênteses ou
# seguido de separador) ou prefixo +55. Lookarounds impedem casar pedaços de
# sequências numéricas maiores (CNPJ, CEP, códigos).
#
# O ``0?`` antes do DDD cobre o prefixo interurbano usado em cadastros antigos
# ("019 3842-9898"), formato muito comum nas carteiras de clientes.
_RE_TELEFONE = re.compile(
    r"(?<![\d\-/.])"
    r"(?:\+?\s?55[\s.\-]{0,2})?"
    r"0?"
    r"(?:\(\s*0?(?P<ddd_par>\d{2})\s*\)|(?P<ddd_nu>\d{2})(?=[\s.\-]))"
    r"[\s.\-]{0,3}"
    r"(?P<p1>\d{4,5})[\s.\-]?(?P<p2>\d{4})"
    r"(?![\d\-/])"
)

# Número gravado sem qualquer separador ("1938249898", "5519998443483"),
# formato típico de exportações de sistemas e bancos de dados.
_RE_TELEFONE_SOLIDO = re.compile(
    r"(?<![\d\-/.])(?:\+?55)?0?(?P<num>\d{10,11})(?![\d\-/])"
)

# Números locais sem DDD — só aproveitados via herança de DDD do vizinho.
_RE_TELEFONE_LOCAL = re.compile(
    r"(?<![\d()\-/.])(?P<p1>\d{4,5})[\s.\-](?P<p2>\d{4})(?![\d\-/])"
)

# 0800 / 0300 / 4004
_RE_TELEFONE_ESPECIAL = re.compile(
    r"(?<!\d)(0800|0300|4004|4003)[\s.\-]?(\d{3})[\s.\-]?(\d{3,4})(?!\d)"
)

# Contextos que indicam que o número NÃO é telefone.
_RE_CONTEXTO_PROIBIDO = re.compile(
    r"(cnpj|cpf|cep|inscri[cç][aã]o|ie\b|im\b|processo|protocolo|nire|"
    r"c[oó]digo|pedido|nota fiscal|nf-?e|chave|r\$|cnae)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TelefoneExtraido:
    """Telefone validado, com a posição e o contexto de onde foi lido."""

    formatado: str
    digitos: str          # 10 ou 11 dígitos (DDD + número), sem +55
    posicao: int
    contexto: str
    ddd_herdado: bool = False

    @property
    def celular(self) -> bool:
        return len(self.digitos) == 11 and self.digitos[2] == "9"


def formatar_telefone(digitos: str) -> str:
    """
    Formata um número nacional (10 ou 11 dígitos) no padrão brasileiro.

    ``"1938249898"`` -> ``"(19) 3824-9898"``
    ``"19998443483"`` -> ``"(19) 99844-3483"``
    """
    d = so_digitos(digitos)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    if len(d) == 11 and d.startswith("0800"):
        return f"{d[:4]} {d[4:7]} {d[7:]}"
    if d.startswith(("0800", "0300", "4004", "4003")):
        return f"{d[:4]} {d[4:7]} {d[7:]}" if len(d) >= 10 else d
    return d


def telefone_valido(digitos: str) -> bool:
    """
    Valida um número nacional brasileiro.

    Regras aplicadas:
      * 10 dígitos (fixo) ou 11 dígitos (celular);
      * DDD presente na lista oficial da Anatel;
      * celular (11 dígitos) precisa começar com 9 após o DDD;
      * fixo (10 dígitos) precisa começar com 2..5 após o DDD;
      * rejeita sequências repetidas/placeholder conhecidas.
    """
    d = so_digitos(digitos)
    if d.startswith(("0800", "0300", "4004", "4003")):
        return 10 <= len(d) <= 11
    if len(d) not in (10, 11):
        return False
    if d in config.TELEFONES_BLOQUEADOS:
        return False
    if int(d[:2]) not in config.DDDS_VALIDOS:
        return False
    assinatura = d[2:]
    if len(set(assinatura)) <= 2:      # 999999999, 111122222...
        return False
    if len(d) == 11:
        return assinatura[0] == "9"
    return assinatura[0] in "2345"


def extrair_telefones(texto: str, herdar_ddd: Optional[bool] = None) -> List[TelefoneExtraido]:
    """
    Extrai telefones brasileiros válidos de um texto livre.

    Args:
        texto: Texto bruto (HTML já convertido em texto, snippet, etc.).
        herdar_ddd: Se ``True``, números sem DDD que apareçam logo após um
            número com DDD herdam esse DDD (convenção tipográfica usual em
            sites brasileiros: ``(19) 3824-9898 / 99844-3483``). O padrão vem
            de :data:`config.HERDAR_DDD`.

    Returns:
        Lista de :class:`TelefoneExtraido` na ordem de aparição, sem duplicatas.
    """
    if not texto:
        return []
    if herdar_ddd is None:
        herdar_ddd = config.HERDAR_DDD

    encontrados: Dict[str, TelefoneExtraido] = {}
    ocupados: List[Tuple[int, int]] = []

    def _contexto(ini: int, fim: int) -> str:
        return limpar_espacos(texto[max(0, ini - 60): fim + 40])

    def _contexto_proibido(ini: int) -> bool:
        janela = texto[max(0, ini - 45): ini]
        return bool(_RE_CONTEXTO_PROIBIDO.search(janela))

    # --- 1) números com DDD explícito ---------------------------------
    ultimo_ddd: Optional[str] = None
    ultimo_fim = -10_000

    for m in _RE_TELEFONE.finditer(texto):
        ddd = m.group("ddd_par") or m.group("ddd_nu")
        numero = m.group("p1") + m.group("p2")
        digitos = f"{ddd}{numero}"

        if not telefone_valido(digitos):
            continue
        if _contexto_proibido(m.start()):
            continue

        ocupados.append((m.start(), m.end()))
        ultimo_ddd, ultimo_fim = ddd, m.end()
        if digitos not in encontrados:
            encontrados[digitos] = TelefoneExtraido(
                formatado=formatar_telefone(digitos),
                digitos=digitos,
                posicao=m.start(),
                contexto=_contexto(m.start(), m.end()),
            )

    # --- 2) números sem separador -------------------------------------
    for m in _RE_TELEFONE_SOLIDO.finditer(texto):
        if any(ini <= m.start() < fim for ini, fim in ocupados):
            continue
        digitos = m.group("num")
        if not telefone_valido(digitos) or digitos in encontrados:
            continue
        if _contexto_proibido(m.start()):
            continue
        ocupados.append((m.start(), m.end()))
        encontrados[digitos] = TelefoneExtraido(
            formatado=formatar_telefone(digitos),
            digitos=digitos,
            posicao=m.start(),
            contexto=_contexto(m.start(), m.end()),
        )

    # --- 3) números especiais (0800 e afins) --------------------------
    for m in _RE_TELEFONE_ESPECIAL.finditer(texto):
        digitos = so_digitos(m.group(0))
        if not telefone_valido(digitos) or digitos in encontrados:
            continue
        ocupados.append((m.start(), m.end()))
        encontrados[digitos] = TelefoneExtraido(
            formatado=formatar_telefone(digitos),
            digitos=digitos,
            posicao=m.start(),
            contexto=_contexto(m.start(), m.end()),
        )

    # --- 4) herança de DDD para números locais ------------------------
    if herdar_ddd:
        for m in _RE_TELEFONE_LOCAL.finditer(texto):
            # Ignora trechos já consumidos pelos padrões anteriores.
            if any(ini <= m.start() < fim for ini, fim in ocupados):
                continue
            if _contexto_proibido(m.start()):
                continue

            # Procura o DDD explícito mais próximo à esquerda.
            anterior_ddd, anterior_fim = None, -10_000
            for m2 in _RE_TELEFONE.finditer(texto[: m.start()]):
                d = m2.group("ddd_par") or m2.group("ddd_nu")
                if int(d) in config.DDDS_VALIDOS:
                    anterior_ddd, anterior_fim = d, m2.end()

            if anterior_ddd is None:
                continue
            if m.start() - anterior_fim > config.HERDAR_DDD_DISTANCIA_MAX:
                continue
            # Entre os dois números só pode haver separadores/rótulos curtos.
            ponte = texto[anterior_fim: m.start()]
            if re.search(r"[A-Za-zÀ-ÿ]{6,}", ponte):
                continue

            digitos = f"{anterior_ddd}{m.group('p1')}{m.group('p2')}"
            if not telefone_valido(digitos) or digitos in encontrados:
                continue
            encontrados[digitos] = TelefoneExtraido(
                formatado=formatar_telefone(digitos),
                digitos=digitos,
                posicao=m.start(),
                contexto=_contexto(m.start(), m.end()),
                ddd_herdado=True,
            )

    return sorted(encontrados.values(), key=lambda t: t.posicao)


def ddd_coerente_com_uf(digitos: str, uf: str) -> bool:
    """
    Diz se o DDD do número pertence à UF informada.

    Usado apenas como sinal de coerência: um DDD de outro estado não invalida
    o telefone (empresas têm filiais e 0800), mas reduz a confiança.
    """
    d = so_digitos(digitos)
    if len(d) < 10 or d.startswith(("0800", "0300", "4004", "4003")):
        return True
    esperados = config.DDDS_POR_UF.get((uf or "").upper())
    return True if not esperados else int(d[:2]) in esperados


# ===========================================================================
# E-mails
# ===========================================================================

_RE_EMAIL = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63})@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)",
)

# Ofuscações comuns: "contato (arroba) empresa.com.br", "contato [at] ..."
_RE_EMAIL_OFUSCADO = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*(?:\(|\[)?\s*(?:arroba|at)\s*(?:\)|\])?\s*"
    r"([A-Za-z0-9-]+(?:\s*(?:\(|\[)?\s*(?:ponto|dot)\s*(?:\)|\])?\s*[A-Za-z0-9-]+)+)",
    re.IGNORECASE,
)


def email_valido(email: str) -> bool:
    """
    Valida um e-mail extraído, descartando falsos positivos comuns.

    Rejeita: domínios de bibliotecas/plataformas, arquivos capturados como
    e-mail (``logo@2x.png``), domínios de exemplo e prefixos de terceiros.
    """
    if not email or email.count("@") != 1:
        return False
    local, dominio = email.lower().split("@")
    if not local or not dominio or "." not in dominio:
        return False
    if len(email) > 100:
        return False

    tld = dominio.rsplit(".", 1)[-1]
    if tld in config.EXTENSOES_INVALIDAS_EMAIL or len(tld) < 2:
        return False
    if not tld.isalpha():
        return False
    if dominio in config.DOMINIOS_EMAIL_BLOQUEADOS:
        return False
    if any(dominio.endswith("." + d) or dominio == d
           for d in config.DOMINIOS_EMAIL_BLOQUEADOS):
        return False
    if any(local.startswith(p) for p in config.PREFIXOS_EMAIL_SUSPEITOS):
        return False
    # "logo@2x", "img@3x" e hashes longos sem vogais.
    if re.fullmatch(r"\d+x", dominio.split(".")[0]):
        return False
    if len(local) > 30 and not re.search(r"[aeiou]", local):
        return False
    return True


def extrair_emails(texto: str) -> List[str]:
    """Extrai e-mails válidos (inclusive ofuscados) preservando a ordem."""
    if not texto:
        return []
    achados: List[str] = []
    vistos: Set[str] = set()

    for m in _RE_EMAIL.finditer(texto):
        email = f"{m.group(1)}@{m.group(2)}".lower().rstrip(".")
        if email_valido(email) and email not in vistos:
            vistos.add(email)
            achados.append(email)

    for m in _RE_EMAIL_OFUSCADO.finditer(texto):
        dominio = re.sub(
            r"\s*(?:\(|\[)?\s*(?:ponto|dot)\s*(?:\)|\])?\s*", ".", m.group(2),
            flags=re.IGNORECASE,
        )
        email = f"{m.group(1)}@{dominio}".lower().strip(". ")
        if email_valido(email) and email not in vistos:
            vistos.add(email)
            achados.append(email)

    return achados


# ===========================================================================
# WhatsApp
# ===========================================================================

_RE_WA_LINK = re.compile(
    r"(?:https?://)?(?:api\.whatsapp\.com/send\?phone=|wa\.me/|whatsapp://send\?phone=)"
    r"(\+?\d{10,15})",
    re.IGNORECASE,
)


def extrair_whatsapps(texto: str) -> List[str]:
    """
    Extrai números de WhatsApp a partir de links ``wa.me`` / ``api.whatsapp``.

    Somente links explícitos são considerados — números "supostamente" de
    WhatsApp não são inferidos.
    """
    if not texto:
        return []
    achados, vistos = [], set()
    for m in _RE_WA_LINK.finditer(texto):
        d = so_digitos(m.group(1))
        if d.startswith("55"):
            d = d[2:]
        if telefone_valido(d) and d not in vistos:
            vistos.add(d)
            achados.append(formatar_telefone(d))
    return achados


# ===========================================================================
# CNPJ
# ===========================================================================

_RE_CNPJ = re.compile(r"(?<!\d)(\d{2})[.\s]?(\d{3})[.\s]?(\d{3})[/\s]?(\d{4})[-\s]?(\d{2})(?!\d)")


def cnpj_valido(cnpj: str) -> bool:
    """Valida um CNPJ pelos dois dígitos verificadores (módulo 11)."""
    d = so_digitos(cnpj)
    if len(d) != 14 or len(set(d)) == 1:
        return False

    def _dv(base: str, pesos: Sequence[int]) -> str:
        soma = sum(int(c) * p for c, p in zip(base, pesos))
        resto = soma % 11
        return "0" if resto < 2 else str(11 - resto)

    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    return d[12] == _dv(d[:12], p1) and d[13] == _dv(d[:13], p2)


def formatar_cnpj(cnpj: str) -> str:
    """Formata um CNPJ como ``00.000.000/0000-00``."""
    d = so_digitos(cnpj)
    if len(d) != 14:
        return d
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"


def extrair_cnpjs(texto: str) -> List[str]:
    """Extrai CNPJs válidos (14 dígitos, DV conferido) preservando a ordem."""
    if not texto:
        return []
    achados, vistos = [], set()
    for m in _RE_CNPJ.finditer(texto):
        d = "".join(m.groups())
        if cnpj_valido(d) and d not in vistos:
            vistos.add(d)
            achados.append(d)
    return achados


# ===========================================================================
# URLs e domínios
# ===========================================================================

def dominio_de(url: str) -> str:
    """Extrai o host de uma URL, sem ``www.`` e em minúsculas."""
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def dominio_base(url: str) -> str:
    """
    Retorna o domínio registrável aproximado (``sub.empresa.com.br`` ->
    ``empresa.com.br``), suficiente para comparação entre páginas do mesmo site.
    """
    host = dominio_de(url)
    if not host:
        return ""
    partes = host.split(".")
    if len(partes) <= 2:
        return host
    # Domínios brasileiros de segundo nível (com.br, ind.br, etc.).
    if partes[-1] == "br" and len(partes) >= 3:
        return ".".join(partes[-3:])
    return ".".join(partes[-2:])


def dominio_em_lista(url: str, lista: Iterable[str]) -> bool:
    """Diz se o domínio da URL pertence (ou é subdomínio de) algum item da lista."""
    host = dominio_de(url)
    if not host:
        return False
    for item in lista:
        alvo = item.lower().split("/")[0]
        if host == alvo or host.endswith("." + alvo):
            return True
    return False


def pode_ser_site_oficial(url: str) -> bool:
    """Descarta redes sociais, diretórios, catálogos e agregadores."""
    host = dominio_de(url)
    if not host or "." not in host:
        return False
    return not dominio_em_lista(url, config.DOMINIOS_NAO_OFICIAIS)


def absolutizar(base: str, href: str) -> str:
    """Converte um href relativo em URL absoluta."""
    try:
        return urljoin(base, href)
    except ValueError:
        return ""


def url_limpa(url: str) -> str:
    """Remove fragmento e barra final para deduplicação de páginas."""
    if not url:
        return ""
    sem_frag = url.split("#", 1)[0]
    return sem_frag.rstrip("/") or sem_frag


# ===========================================================================
# Rotação de User-Agent
# ===========================================================================

class RotacionadorUserAgent:
    """
    Fornece User-Agents variados a cada requisição.

    Usa ``fake-useragent`` quando disponível; caso contrário, recorre à lista
    estática de :mod:`config`. Thread-safe.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gerador = None
        try:
            from fake_useragent import UserAgent  # import tardio: é opcional

            self._gerador = UserAgent(fallback=config.USER_AGENTS_FALLBACK[0])
            logger.debug("fake-useragent carregado.")
        except Exception as exc:  # pragma: no cover - depende do ambiente
            logger.debug("fake-useragent indisponível (%s); usando lista fixa.", exc)

    def proximo(self) -> str:
        """Retorna um User-Agent para a próxima requisição."""
        with self._lock:
            if self._gerador is not None:
                try:
                    return self._gerador.random
                except Exception:
                    self._gerador = None
            return random.choice(config.USER_AGENTS_FALLBACK)


# ===========================================================================
# Controle de ritmo
# ===========================================================================

class Limitador:
    """
    Aplica pausas aleatórias entre requisições para evitar bloqueios.

    O intervalo é sorteado por chamada, o que descaracteriza o padrão robótico
    de requisições em intervalos fixos. Thread-safe: cada thread aguarda seu
    próprio intervalo, mas o mínimo global entre chamadas é respeitado.
    """

    def __init__(self, minimo: float, maximo: float) -> None:
        self.minimo = max(0.0, float(minimo))
        self.maximo = max(self.minimo, float(maximo))
        self._lock = threading.Lock()
        self._proximo_livre = 0.0

    def aguardar(self, fator: float = 1.0) -> float:
        """
        Bloqueia pelo intervalo sorteado. Retorna quantos segundos esperou.

        Args:
            fator: Multiplicador do intervalo (>1 para fontes mais sensíveis).
        """
        espera = random.uniform(self.minimo, self.maximo) * max(0.1, fator)
        with self._lock:
            agora = time.monotonic()
            alvo = max(agora, self._proximo_livre) + espera
            self._proximo_livre = alvo
            dormir = alvo - agora
        if dormir > 0:
            time.sleep(dormir)
        return max(0.0, dormir)

    def penalizar(self, segundos: float) -> None:
        """Empurra o próximo slot livre para o futuro (após 429/503)."""
        with self._lock:
            self._proximo_livre = max(self._proximo_livre, time.monotonic()) + segundos


# ===========================================================================
# Cliente HTTP
# ===========================================================================

def detectar_captcha(texto: str, status: int = 200) -> Optional[str]:
    """
    Procura marcadores de desafio anti-robô no corpo da resposta.

    Returns:
        O marcador encontrado, ou ``None`` se a resposta parece legítima.
    """
    if status in (403, 429, 503) and not texto:
        return f"HTTP {status} sem corpo"
    if not texto:
        return None
    amostra = remover_acentos(texto[:8000]).lower()
    for marcador in config.MARCADORES_CAPTCHA:
        if remover_acentos(marcador).lower() in amostra:
            return marcador
    return None


class ClienteHTTP:
    """
    Cliente HTTP com sessão persistente, retentativas, rotação de User-Agent,
    limitação de ritmo e detecção de captcha.

    Uma instância por thread é o uso recomendado (``requests.Session`` não é
    formalmente thread-safe); o motor de pesquisa cuida disso via ``threading.local``.
    """

    def __init__(
        self,
        limitador: Optional[Limitador] = None,
        rotacionador: Optional[RotacionadorUserAgent] = None,
        timeout: int = config.TIMEOUT_HTTP,
    ) -> None:
        self.sessao = requests.Session()
        self.limitador = limitador or Limitador(config.DELAY_MIN, config.DELAY_MAX)
        self.rotacionador = rotacionador or RotacionadorUserAgent()
        self.timeout = timeout
        self.log = logging.getLogger("localizador.http")

    # ------------------------------------------------------------------

    def _cabecalhos(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Monta cabeçalhos plausíveis de navegador."""
        cabecalhos = {
            "User-Agent": self.rotacionador.proximo(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            # Apenas gzip/deflate: o `requests` não descompacta Brotli sem a
            # biblioteca `brotli`. Anunciar "br" faria o servidor devolver um
            # corpo binário ilegível — falha silenciosa difícil de diagnosticar.
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "DNT": "1",
        }
        if extra:
            cabecalhos.update(extra)
        return cabecalhos

    def obter(self, url: str, **kwargs) -> Optional[requests.Response]:
        """Executa um GET com retentativas. Ver :meth:`requisitar`."""
        return self.requisitar("GET", url, **kwargs)

    def postar(self, url: str, dados: Optional[Dict[str, str]] = None, **kwargs):
        """
        Executa um POST com retentativas.

        Alguns endpoints de busca (notadamente o HTML do DuckDuckGo) são bem
        mais tolerantes a POST do que a GET, que é limitado com agressividade.
        """
        return self.requisitar("POST", url, dados=dados, **kwargs)

    def requisitar(
        self,
        metodo: str,
        url: str,
        params: Optional[Dict[str, str]] = None,
        dados: Optional[Dict[str, str]] = None,
        fator_delay: float = 1.0,
        verificar_captcha: bool = True,
        tentativas: int = config.MAX_TENTATIVAS_HTTP,
        cabecalhos: Optional[Dict[str, str]] = None,
        permitir_json: bool = False,
        avisar_bloqueio: bool = False,
    ) -> Optional[requests.Response]:
        """
        Executa uma requisição HTTP com retentativas e devolve a resposta.

        Args:
            metodo: ``"GET"`` ou ``"POST"``.
            params: Parâmetros de query string.
            dados: Corpo do formulário (apenas POST).
            fator_delay: Multiplicador do intervalo entre requisições.
            verificar_captcha: Ativa a detecção de desafio anti-robô.
            avisar_bloqueio: Registra em WARNING quando a resposta for 4xx.
                Usado pelos buscadores, onde um bloqueio silencioso seria
                confundido com "nenhum resultado".

        Returns:
            A resposta, ou ``None`` se todas as tentativas falharem.

        Raises:
            CaptchaDetectado: quando a resposta contém desafio anti-robô e
                ``verificar_captcha`` está ativo.
        """
        ultimo_erro: Optional[Exception] = None

        for tentativa in range(1, tentativas + 1):
            self.limitador.aguardar(fator_delay)
            try:
                resposta = self.sessao.request(
                    metodo.upper(),
                    url,
                    params=params,
                    data=dados,
                    headers=self._cabecalhos(cabecalhos),
                    timeout=self.timeout,
                    allow_redirects=True,
                    stream=True,
                )
            except requests.RequestException as exc:
                ultimo_erro = exc
                self.log.debug("Falha %s/%s em %s: %s", tentativa, tentativas, url, exc)
                time.sleep(config.BACKOFF_BASE ** tentativa)
                continue

            # Limita o corpo lido para não baixar arquivos enormes.
            try:
                conteudo = resposta.raw.read(config.MAX_BYTES_RESPOSTA, decode_content=True)
                resposta._content = conteudo  # type: ignore[attr-defined]
            except Exception as exc:
                ultimo_erro = exc
                resposta.close()
                continue
            finally:
                resposta.close()

            if resposta.status_code in (429, 503):
                self.limitador.penalizar(20 * tentativa)
                self.log.warning(
                    "HTTP %s em %s — recuando o ritmo.", resposta.status_code, url
                )
                ultimo_erro = BloqueioTemporario(f"HTTP {resposta.status_code}")
                continue

            if resposta.status_code >= 400:
                if avisar_bloqueio:
                    self.log.warning(
                        "HTTP %s em %s — a fonte recusou a requisição "
                        "(bloqueio ou limite de taxa).",
                        resposta.status_code, url,
                    )
                else:
                    self.log.debug("HTTP %s em %s", resposta.status_code, url)
                return None

            tipo = resposta.headers.get("Content-Type", "")
            if not permitir_json and "text/html" not in tipo and "text/plain" not in tipo:
                if "json" not in tipo and "xml" not in tipo:
                    return None

            if verificar_captcha:
                marcador = detectar_captcha(resposta.text, resposta.status_code)
                if marcador:
                    raise CaptchaDetectado(url, f"Marcador: {marcador!r}")

            return resposta

        if ultimo_erro:
            self.log.debug("Desistindo de %s: %s", url, ultimo_erro)
        return None

    def obter_texto(self, url: str, **kwargs) -> str:
        """GET que devolve o corpo em texto (string vazia em caso de falha)."""
        return self._texto_de(self.obter(url, **kwargs))

    def postar_texto(self, url: str, dados: Optional[Dict[str, str]] = None, **kwargs) -> str:
        """POST que devolve o corpo em texto (string vazia em caso de falha)."""
        return self._texto_de(self.postar(url, dados=dados, **kwargs))

    @staticmethod
    def _texto_de(resposta: Optional[requests.Response]) -> str:
        """Decodifica o corpo da resposta detectando o charset quando preciso."""
        if resposta is None:
            return ""
        if not resposta.encoding or resposta.encoding.lower() == "iso-8859-1":
            resposta.encoding = resposta.apparent_encoding or "utf-8"
        return resposta.text

    def obter_json(self, url: str, **kwargs) -> Optional[dict]:
        """GET que devolve JSON decodificado, ou ``None``."""
        kwargs.setdefault("permitir_json", True)
        kwargs.setdefault("verificar_captcha", False)
        kwargs.setdefault(
            "cabecalhos", {"Accept": "application/json"}
        )
        resposta = self.obter(url, **kwargs)
        if resposta is None:
            return None
        try:
            return resposta.json()
        except ValueError:
            return None

    def fechar(self) -> None:
        """Encerra a sessão HTTP."""
        try:
            self.sessao.close()
        except Exception:
            pass


# ===========================================================================
# HTML -> texto
# ===========================================================================

def html_para_texto(html: str) -> str:
    """
    Converte HTML em texto legível preservando separação entre blocos.

    Usa ``lxml`` quando disponível (mais rápido) e cai para ``html.parser``.
    Scripts, estilos e comentários são removidos para não poluir a extração.
    """
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        return re.sub(r"<[^>]+>", " ", html)

    for parser in ("lxml", "html.parser"):
        try:
            sopa = BeautifulSoup(html, parser)
            break
        except Exception:
            continue
    else:  # pragma: no cover
        return re.sub(r"<[^>]+>", " ", html)

    for tag in sopa(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    return limpar_espacos(sopa.get_text(separator=" \n "))


# ===========================================================================
# Cronômetro
# ===========================================================================

class Cronometro:
    """Mede a duração de um bloco de código (uso como context manager)."""

    def __init__(self) -> None:
        self.inicio = 0.0
        self.duracao = 0.0

    def __enter__(self) -> "Cronometro":
        self.inicio = time.monotonic()
        return self

    def __exit__(self, *_exc) -> None:
        self.duracao = time.monotonic() - self.inicio

    @property
    def decorrido(self) -> float:
        return time.monotonic() - self.inicio if self.inicio else self.duracao


def formatar_duracao(segundos: float) -> str:
    """Formata segundos como ``1h 05min 30s`` / ``2min 10s`` / ``9s``."""
    segundos = max(0, int(round(segundos)))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}min {seg:02d}s"
    if minutos:
        return f"{minutos}min {seg:02d}s"
    return f"{seg}s"
