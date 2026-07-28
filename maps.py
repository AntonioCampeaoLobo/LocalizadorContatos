# -*- coding: utf-8 -*-
"""
maps.py
=======

Coleta de dados do **Google Business Profile** (ficha do Google Maps) via
Playwright.

O Maps é a segunda fonte em prioridade porque a ficha de um estabelecimento
traz telefone, site e endereço declarados pelo próprio dono do negócio, com
cidade explícita — o que resolve boa parte dos casos de homônimos.

Pontos de atenção implementados:

* **Thread-safety** — a API síncrona do Playwright não pode ser compartilhada
  entre threads. :class:`SessaoPlaywright` mantém uma instância *por thread*
  através de ``threading.local``, e :func:`encerrar_todas` limpa tudo no fim.
* **Degradação graciosa** — se o Playwright ou o navegador não estiverem
  instalados, o módulo se declara indisponível e a pesquisa segue sem o Maps,
  sem quebrar a aplicação.
* **Desambiguação** — quando a busca retorna vários estabelecimentos, cada um é
  confrontado com razão social e cidade; havendo dois candidatos plausíveis em
  cidades diferentes, o resultado é marcado como ambíguo e **nada é gravado**.
* **Detecção de bloqueio** — páginas de "unusual traffic"/reCAPTCHA levantam
  :class:`utils.CaptchaDetectado`, o que faz o motor pausar e avisar o usuário.
"""

from __future__ import annotations

import logging
import random
import re
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import quote_plus

import config
import utils
from modelos import (
    Candidato,
    Confianca,
    Empresa,
    Evidencia,
    Fonte,
    ResultadoPesquisa,
)

logger = logging.getLogger("localizador.maps")


# ===========================================================================
# Disponibilidade do Playwright
# ===========================================================================

def playwright_disponivel() -> Tuple[bool, str]:
    """
    Verifica se o Playwright pode ser usado.

    Returns:
        Tupla ``(disponivel, motivo)``. ``motivo`` explica a indisponibilidade.
    """
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False, (
            "Playwright não instalado. Execute: pip install playwright "
            "&& playwright install chromium"
        )
    return True, ""


# ===========================================================================
# Sessão por thread
# ===========================================================================

class SessaoPlaywright:
    """
    Gerencia um navegador Playwright por thread.

    Cada worker do ``ThreadPoolExecutor`` recebe seu próprio navegador na
    primeira utilização. O consumo de memória é o preço por poder pesquisar
    várias empresas em paralelo sem violar a thread-safety da API síncrona.
    """

    _local = threading.local()
    _instancias: List["SessaoPlaywright"] = []
    _lock = threading.Lock()

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._navegador = None
        self._contexto = None
        self.log = logger

    # ------------------------------------------------------------------

    @classmethod
    def da_thread(cls, headless: bool = True) -> "SessaoPlaywright":
        """Devolve (criando se preciso) a sessão da thread atual."""
        sessao = getattr(cls._local, "sessao", None)
        if sessao is None:
            sessao = cls(headless=headless)
            cls._local.sessao = sessao
            with cls._lock:
                cls._instancias.append(sessao)
        return sessao

    @classmethod
    def encerrar_todas(cls) -> None:
        """Fecha todos os navegadores abertos (chamado ao fim do processamento)."""
        with cls._lock:
            instancias = list(cls._instancias)
            cls._instancias.clear()
        for sessao in instancias:
            sessao.encerrar()

    # ------------------------------------------------------------------

    def _garantir(self):
        """Inicializa navegador e contexto sob demanda."""
        if self._contexto is not None:
            return self._contexto

        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._navegador = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
            ],
        )
        self._contexto = self._navegador.new_context(
            user_agent=random.choice(config.USER_AGENTS_FALLBACK),
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1366, "height": 900},
            geolocation={"latitude": -22.9099, "longitude": -47.0626},  # Campinas
            permissions=[],
        )
        self._contexto.set_default_timeout(config.TIMEOUT_PLAYWRIGHT)
        # Remove o marcador `navigator.webdriver`, que denuncia automação.
        self._contexto.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self.log.debug("Navegador Playwright iniciado (thread %s).", threading.get_ident())
        return self._contexto

    def nova_pagina(self):
        """Abre uma nova aba no contexto da thread."""
        return self._garantir().new_page()

    def encerrar(self) -> None:
        """Fecha contexto, navegador e o próprio Playwright."""
        for objeto, metodo in (
            (self._contexto, "close"),
            (self._navegador, "close"),
            (self._playwright, "stop"),
        ):
            if objeto is None:
                continue
            try:
                getattr(objeto, metodo)()
            except Exception:
                pass
        self._contexto = self._navegador = self._playwright = None


# ===========================================================================
# Resultado
# ===========================================================================

@dataclass
class FichaMaps:
    """Dados extraídos de uma ficha do Google Business Profile."""

    nome: str = ""
    telefone: str = ""
    site: str = ""
    endereco: str = ""
    categoria: str = ""
    url: str = ""

    @property
    def cidade(self) -> str:
        """
        Cidade inferida do endereço.

        O padrão brasileiro no Maps é ``... - Bairro, Cidade - SP, CEP``.
        Somente o que está escrito é utilizado; nada é deduzido além disso.
        """
        m = re.search(r",\s*([^,\-]{3,40})\s*-\s*[A-Z]{2}\b", self.endereco or "")
        return utils.limpar_espacos(m.group(1)) if m else ""

    @property
    def uf(self) -> str:
        m = re.search(r"-\s*([A-Z]{2})\b", self.endereco or "")
        return m.group(1) if m else ""


@dataclass
class ResultadoMaps:
    """Sumário da consulta ao Maps."""

    fichas: List[FichaMaps] = field(default_factory=list)
    ambiguo: bool = False
    contatos_registrados: int = 0
    motivo: str = ""


# ===========================================================================
# Rastreador
# ===========================================================================

class RastreadorMaps:
    """
    Consulta o Google Maps e registra os contatos da ficha correspondente.

    Example:
        >>> maps = RastreadorMaps(cfg)
        >>> if maps.disponivel:
        ...     info = maps.pesquisar(empresa, resultado)
    """

    # Seletores da ficha (Maps usa `data-item-id`, mais estável que classes).
    SEL_TELEFONE = 'button[data-item-id^="phone:tel:"], a[data-item-id^="phone:tel:"]'
    SEL_SITE = 'a[data-item-id="authority"]'
    SEL_ENDERECO = 'button[data-item-id="address"]'
    SEL_TITULO = "h1.DUwDvf, h1"
    SEL_CATEGORIA = "button.DkEaL, button[jsaction*='category']"
    SEL_FEED = 'div[role="feed"]'
    SEL_ITEM_FEED = 'div[role="feed"] a[href*="/maps/place/"]'

    def __init__(self, cfg: Optional[config.Configuracao] = None) -> None:
        self.cfg = cfg or config.Configuracao()
        self.disponivel, self.motivo_indisponivel = playwright_disponivel()
        if self.cfg.usar_playwright is False or self.cfg.usar_google_maps is False:
            self.disponivel = False
            self.motivo_indisponivel = "Google Maps desabilitado na configuração."
        self.log = logger

    # ==================================================================
    # API pública
    # ==================================================================

    def pesquisar(self, empresa: Empresa, resultado: ResultadoPesquisa) -> ResultadoMaps:
        """
        Pesquisa a empresa no Maps e registra os dados da ficha correspondente.

        Returns:
            :class:`ResultadoMaps`. Quando ``ambiguo`` é ``True``, nenhum dado
            foi gravado — a empresa deve ir para revisão manual.
        """
        info = ResultadoMaps()
        if not self.disponivel:
            info.motivo = self.motivo_indisponivel
            return info

        consulta = f"{empresa.razao_social} {empresa.cidade} SP".strip()
        pagina = None
        try:
            sessao = SessaoPlaywright.da_thread(headless=self.cfg.headless)
            pagina = sessao.nova_pagina()
            fichas = self._coletar_fichas(pagina, consulta)
        except utils.CaptchaDetectado:
            raise
        except Exception as exc:
            self.log.debug("Falha no Maps para %r: %s", empresa.razao_social, exc)
            info.motivo = f"Falha ao consultar o Maps: {exc}"
            return info
        finally:
            if pagina is not None:
                try:
                    pagina.close()
                except Exception:
                    pass

        info.fichas = fichas
        resultado.registrar_fonte("Google Business Profile")

        if not fichas:
            info.motivo = "Nenhum estabelecimento encontrado no Maps."
            return info

        aceitos, duvidosos = self._classificar(empresa, fichas)

        # Ambiguidade: dois estabelecimentos plausíveis em cidades diferentes.
        if len(aceitos) > 1:
            cidades = {utils.normalizar(c.cidade) for c in aceitos if c.cidade}
            if len(cidades) > 1:
                info.ambiguo = True
                info.motivo = (
                    "Mais de um estabelecimento com nome compatível em cidades "
                    "diferentes: " + "; ".join(str(c) for c in aceitos[:3])
                )
                self.log.info("Maps ambíguo para %r.", empresa.razao_social)
                return info

        if not aceitos:
            if duvidosos:
                info.motivo = (
                    "Estabelecimento apenas parecido, sem confirmação: "
                    + str(duvidosos[0])
                )
            else:
                info.motivo = "Nenhum estabelecimento correspondeu à empresa."
            return info

        melhor = max(aceitos, key=lambda c: c.score_nome)
        ficha = next(f for f in fichas if f.url == melhor.url)
        info.contatos_registrados = self._registrar(empresa, resultado, ficha, melhor)
        return info

    # ==================================================================
    # Navegação
    # ==================================================================

    def _coletar_fichas(self, pagina, consulta: str) -> List[FichaMaps]:
        """
        Abre a busca no Maps e devolve as fichas dos primeiros resultados.

        O Maps redireciona direto para a ficha quando há um único resultado
        evidente; ambos os caminhos são tratados.
        """
        url = config.URL_GOOGLE_MAPS_BUSCA.format(query=quote_plus(consulta))
        pagina.goto(url, wait_until="domcontentloaded", timeout=config.TIMEOUT_PLAYWRIGHT)
        self._tratar_consentimento(pagina)
        self._verificar_bloqueio(pagina)

        pagina.wait_for_timeout(random.randint(1800, 3200))

        # Caso 1: redirecionou direto para a ficha do estabelecimento.
        if "/maps/place/" in pagina.url:
            ficha = self._ler_ficha(pagina)
            return [ficha] if ficha.nome else []

        # Caso 2: lista de resultados.
        try:
            pagina.wait_for_selector(self.SEL_FEED, timeout=8000)
        except Exception:
            return []

        links: List[str] = []
        for elemento in pagina.query_selector_all(self.SEL_ITEM_FEED)[:4]:
            href = elemento.get_attribute("href") or ""
            if href and href not in links:
                links.append(href)

        fichas: List[FichaMaps] = []
        for href in links[:3]:
            try:
                pagina.goto(
                    href, wait_until="domcontentloaded", timeout=config.TIMEOUT_PLAYWRIGHT
                )
                pagina.wait_for_timeout(random.randint(1200, 2200))
                self._verificar_bloqueio(pagina)
                ficha = self._ler_ficha(pagina)
                if ficha.nome:
                    fichas.append(ficha)
            except utils.CaptchaDetectado:
                raise
            except Exception as exc:
                self.log.debug("Falha ao abrir ficha %s: %s", href, exc)

        return fichas

    def _ler_ficha(self, pagina) -> FichaMaps:
        """Extrai os campos da ficha aberta."""
        ficha = FichaMaps(url=pagina.url)

        ficha.nome = self._texto(pagina, self.SEL_TITULO)
        ficha.categoria = self._texto(pagina, self.SEL_CATEGORIA)

        # Telefone: o `data-item-id` já traz o número ("phone:tel:+551938249898").
        elemento = pagina.query_selector(self.SEL_TELEFONE)
        if elemento:
            item_id = elemento.get_attribute("data-item-id") or ""
            bruto = item_id.split("tel:")[-1] if "tel:" in item_id else ""
            if not bruto:
                bruto = elemento.get_attribute("aria-label") or elemento.inner_text()
            digitos = utils.so_digitos(bruto)
            if digitos.startswith("55") and len(digitos) > 11:
                digitos = digitos[2:]
            if utils.telefone_valido(digitos):
                ficha.telefone = utils.formatar_telefone(digitos)

        elemento = pagina.query_selector(self.SEL_SITE)
        if elemento:
            href = elemento.get_attribute("href") or ""
            if href.startswith("http") and utils.pode_ser_site_oficial(href):
                ficha.site = href

        elemento = pagina.query_selector(self.SEL_ENDERECO)
        if elemento:
            rotulo = elemento.get_attribute("aria-label") or elemento.inner_text()
            ficha.endereco = utils.limpar_espacos(
                re.sub(r"^\s*Endere[cç]o:\s*", "", rotulo or "", flags=re.IGNORECASE)
            )

        return ficha

    @staticmethod
    def _texto(pagina, seletor: str) -> str:
        """Lê o texto de um seletor, tolerando ausência."""
        try:
            elemento = pagina.query_selector(seletor)
            return utils.limpar_espacos(elemento.inner_text()) if elemento else ""
        except Exception:
            return ""

    def _tratar_consentimento(self, pagina) -> None:
        """
        Lida com a tela de consentimento do Google.

        Sempre escolhe a opção **mais restritiva** disponível ("Rejeitar tudo"),
        em respeito à privacidade; se ela não existir, a página é deixada como
        está e a consulta simplesmente não prossegue.
        """
        if "consent." not in (pagina.url or ""):
            return
        for rotulo in ("Rejeitar tudo", "Reject all", "Recusar tudo"):
            try:
                botao = pagina.get_by_role("button", name=rotulo)
                if botao.count() > 0:
                    botao.first.click(timeout=4000)
                    pagina.wait_for_timeout(1200)
                    return
            except Exception:
                continue
        self.log.warning("Tela de consentimento do Google sem opção de recusa.")

    def _verificar_bloqueio(self, pagina) -> None:
        """Levanta :class:`utils.CaptchaDetectado` se a página for um desafio."""
        url = pagina.url or ""
        if "/sorry/" in url or "consent.google" in url and "maps" not in url:
            raise utils.CaptchaDetectado(url, "Página de bloqueio do Google.")
        try:
            conteudo = pagina.content()[:6000]
        except Exception:
            return
        marcador = utils.detectar_captcha(conteudo)
        if marcador:
            raise utils.CaptchaDetectado(url, f"Marcador: {marcador!r}")

    # ==================================================================
    # Validação e registro
    # ==================================================================

    def _classificar(
        self, empresa: Empresa, fichas: List[FichaMaps]
    ) -> Tuple[List[Candidato], List[Candidato]]:
        """Separa as fichas em aceitas e duvidosas conforme nome e cidade."""
        aceitos: List[Candidato] = []
        duvidosos: List[Candidato] = []

        for ficha in fichas:
            _, score = utils.matcher.compativel(empresa.razao_social, ficha.nome)
            cidade_ok: Optional[bool] = None
            if empresa.cidade and ficha.cidade:
                cidade_ok = utils.normalizar(ficha.cidade) == utils.normalizar(empresa.cidade)

            candidato = Candidato(
                nome=ficha.nome,
                url=ficha.url,
                fonte=Fonte.GOOGLE_BUSINESS,
                cidade=ficha.cidade,
                uf=ficha.uf,
                score_nome=score,
                cidade_confere=cidade_ok,
                texto=ficha.endereco,
            )
            if candidato.aceito:
                aceitos.append(candidato)
            elif candidato.duvidoso:
                duvidosos.append(candidato)

        return aceitos, duvidosos

    def _registrar(
        self,
        empresa: Empresa,
        resultado: ResultadoPesquisa,
        ficha: FichaMaps,
        candidato: Candidato,
    ) -> int:
        """Grava os dados da ficha aprovada no resultado da pesquisa."""
        # Cidade não confirmada rebaixa a confiança: a ficha pode ser de outra
        # unidade da mesma rede.
        confianca = (
            Confianca.ALTA if candidato.cidade_confere else Confianca.MEDIA
        )

        evidencia = Evidencia(
            fonte=Fonte.GOOGLE_BUSINESS,
            url=ficha.url,
            trecho=utils.truncar(f"{ficha.nome} — {ficha.endereco}", 220),
            detalhe=f"score_nome={candidato.score_nome:.2f}; categoria={ficha.categoria}",
        )

        registrados = 0
        if ficha.telefone:
            conf = confianca
            if not utils.ddd_coerente_com_uf(ficha.telefone, ficha.uf or empresa.uf):
                conf = Confianca.MEDIA if conf == Confianca.ALTA else Confianca.BAIXA
            if resultado.adicionar_telefone(ficha.telefone, evidencia, conf):
                registrados += 1

        if ficha.site:
            resultado.definir_site(ficha.site, evidencia, confianca)

        if ficha.endereco and not resultado.endereco:
            resultado.endereco = ficha.endereco

        return registrados


def encerrar_navegadores() -> None:
    """Fecha todos os navegadores Playwright abertos pelas threads."""
    SessaoPlaywright.encerrar_todas()
