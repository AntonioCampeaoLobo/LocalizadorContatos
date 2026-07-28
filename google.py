# -*- coding: utf-8 -*-
"""
google.py
=========

Camada de buscadores web.

Implementa um pequeno framework de motores de busca (Strategy) com uma
interface comum :class:`MotorBusca`, e um agregador :class:`Buscador` que os
tenta em ordem até obter resultados.

Motores implementados:

* :class:`DuckDuckGoHTML` — endpoint HTML sem JavaScript, o mais estável;
* :class:`Bing` — alternativa com boa cobertura de empresas brasileiras;
* :class:`GoogleSearch` — exigido pelo escopo; é o mais suscetível a bloqueio,
  por isso fica por último e sua falha nunca interrompe o processamento.

Além da busca, o módulo oferece:

* :meth:`Buscador.descobrir_site_oficial` — identifica o domínio oficial da
  empresa entre os resultados, descartando redes sociais, catálogos e
  agregadores;
* :meth:`Buscador.coletar_de_resultados` — extrai contatos dos *snippets* e das
  páginas de diretórios, sempre com validação de correspondência da empresa.

Nenhuma informação é inventada: todo contato retornado carrega a URL exata de
onde foi lido.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlparse

import config
import utils
from modelos import Candidato, Confianca, Empresa, Evidencia, Fonte, ResultadoPesquisa

logger = logging.getLogger("localizador.google")


# ===========================================================================
# Estrutura de resultado
# ===========================================================================

@dataclass
class ResultadoBusca:
    """Um item da página de resultados de um buscador."""

    titulo: str
    url: str
    snippet: str = ""
    motor: str = ""

    @property
    def dominio(self) -> str:
        return utils.dominio_de(self.url)

    @property
    def texto(self) -> str:
        """Título + snippet, usado para validação de correspondência."""
        return f"{self.titulo} {self.snippet}".strip()

    def fonte_provavel(self) -> Fonte:
        """Classifica o resultado conforme a confiabilidade do domínio."""
        if utils.dominio_em_lista(self.url, ["linkedin.com"]):
            return Fonte.LINKEDIN
        if utils.dominio_em_lista(self.url, config.DOMINIOS_OFICIAIS):
            return Fonte.RECEITA_FEDERAL
        if utils.dominio_em_lista(self.url, config.DOMINIOS_DIRETORIOS_CONFIAVEIS):
            return Fonte.DIRETORIO
        if utils.dominio_em_lista(self.url, config.DOMINIOS_CATALOGOS):
            return Fonte.CATALOGO
        if utils.pode_ser_site_oficial(self.url):
            return Fonte.SITE_OFICIAL
        return Fonte.GOOGLE_SEARCH


# ===========================================================================
# Motores
# ===========================================================================

class MotorBusca:
    """
    Interface comum dos motores de busca.

    Subclasses implementam :meth:`buscar`. O agregador cuida de fallback,
    ritmo e tratamento de captcha.
    """

    nome = "base"
    fator_delay = 1.6   # buscadores exigem intervalo maior

    def __init__(self, cliente: utils.ClienteHTTP) -> None:
        self.cliente = cliente
        self.log = logging.getLogger(f"localizador.busca.{self.nome}")

    def buscar(self, consulta: str, limite: int) -> List[ResultadoBusca]:
        """Executa a consulta e devolve até ``limite`` resultados."""
        raise NotImplementedError

    # -- disjuntor ------------------------------------------------------

    @property
    def disjuntor(self) -> utils.Disjuntor:
        """Disjuntor compartilhado desta fonte."""
        return utils.Disjuntor.para(self.nome)

    @property
    def disponivel(self) -> bool:
        """``False`` enquanto o disjuntor estiver aberto."""
        return not self.disjuntor.aberto

    def _corpo(self, metodo: str, url: str, **kwargs) -> str:
        """
        Busca o corpo de uma resposta contabilizando o resultado no disjuntor.

        Um corpo vazio significa que a camada HTTP não conseguiu nada — host
        inalcançável, bloqueio ou erro — e conta como falha. Um corpo não vazio
        conta como sucesso, mesmo que a página não tenha resultados: "nenhum
        resultado" é uma resposta legítima, não uma falha da fonte.
        """
        if not self.disponivel:
            return ""

        kwargs.setdefault("fator_delay", self.fator_delay)
        kwargs.setdefault("avisar_bloqueio", True)

        if metodo.upper() == "POST":
            corpo = self.cliente.postar_texto(url, **kwargs)
        else:
            corpo = self.cliente.obter_texto(url, **kwargs)

        if corpo:
            self.disjuntor.registrar_sucesso()
        else:
            self.disjuntor.registrar_falha()
        return corpo

    # -- utilidades compartilhadas -------------------------------------

    def _sopa(self, html: str):
        """Cria o parser HTML preferindo lxml."""
        from bs4 import BeautifulSoup

        for parser in ("lxml", "html.parser"):
            try:
                return BeautifulSoup(html, parser)
            except Exception:
                continue
        return BeautifulSoup(html, "html.parser")

    @staticmethod
    def _limpar_url(href: str) -> str:
        """
        Desembrulha URLs de redirecionamento usadas pelos buscadores.

        DuckDuckGo: ``//duckduckgo.com/l/?uddg=<url-encoded>``
        Google:     ``/url?q=<url-encoded>``
        Bing:       ``https://www.bing.com/ck/a?...&u=a1<base64url>``
        """
        if not href:
            return ""
        if href.startswith("//"):
            href = "https:" + href

        try:
            partes = urlparse(href)
        except ValueError:
            return ""

        query = parse_qs(partes.query)
        for chave in ("uddg", "q", "url", "u"):
            if chave not in query or not query[chave]:
                continue
            bruto = query[chave][0]

            alvo = unquote(bruto)
            if alvo.startswith("http"):
                return alvo

            # Bing codifica o destino em base64url com o prefixo "a1".
            if chave == "u" and bruto.startswith("a1"):
                alvo = MotorBusca._decodificar_base64url(bruto[2:])
                if alvo.startswith("http"):
                    return alvo

        return href if href.startswith("http") else ""

    @staticmethod
    def _decodificar_base64url(texto: str) -> str:
        """Decodifica base64url tolerando ausência de padding."""
        import base64
        import binascii

        try:
            preenchido = texto + "=" * (-len(texto) % 4)
            return base64.urlsafe_b64decode(preenchido).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError):
            return ""


class DuckDuckGoHTML(MotorBusca):
    """
    Endpoints sem JavaScript do DuckDuckGo.

    É a primeira escolha para consultas em volume: raramente aplica captcha e
    devolve resultados de boa qualidade para empresas brasileiras.

    Usa **POST**, que é bem menos limitado que GET nesses endpoints, e tenta o
    endpoint ``lite`` quando o ``html`` recusa a requisição — em uso sustentado
    o ``html`` começa a responder 4xx antes do ``lite``.
    """

    nome = "duckduckgo"

    def buscar(self, consulta: str, limite: int) -> List[ResultadoBusca]:
        dados = {"q": consulta, "kl": "br-pt"}
        cabecalhos = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://duckduckgo.com",
            "Referer": "https://duckduckgo.com/",
        }

        for url, extrator in (
            (config.URL_DUCKDUCKGO, self._extrair_html),
            (config.URL_DUCKDUCKGO_LITE, self._extrair_lite),
        ):
            corpo = self._corpo("POST", url, dados=dados, cabecalhos=cabecalhos)
            if not corpo:
                continue
            resultados = extrator(corpo, limite)
            if resultados:
                return resultados

        return []

    def _extrair_html(self, corpo: str, limite: int) -> List[ResultadoBusca]:
        """Extrai resultados do endpoint ``html.duckduckgo.com``."""
        sopa = self._sopa(corpo)
        resultados: List[ResultadoBusca] = []

        for bloco in sopa.select("div.result, div.web-result")[: limite * 2]:
            link = bloco.select_one("a.result__a")
            if not link:
                continue
            url = self._limpar_url(link.get("href", ""))
            if not url:
                continue
            snippet_tag = bloco.select_one(".result__snippet")
            resultados.append(
                ResultadoBusca(
                    titulo=utils.limpar_espacos(link.get_text()),
                    url=url,
                    snippet=utils.limpar_espacos(
                        snippet_tag.get_text() if snippet_tag else ""
                    ),
                    motor=self.nome,
                )
            )
            if len(resultados) >= limite:
                break

        return resultados

    def _extrair_lite(self, corpo: str, limite: int) -> List[ResultadoBusca]:
        """
        Extrai resultados do endpoint ``lite.duckduckgo.com``.

        A marcação é uma tabela: o link do resultado vem em uma linha e o
        respectivo trecho na linha seguinte.
        """
        sopa = self._sopa(corpo)
        resultados: List[ResultadoBusca] = []
        pendente: Optional[ResultadoBusca] = None

        for linha in sopa.select("tr"):
            link = linha.select_one("a.result-link") or linha.select_one("a[href]")
            trecho = linha.select_one("td.result-snippet")

            if trecho is not None and pendente is not None:
                pendente.snippet = utils.limpar_espacos(trecho.get_text())
                resultados.append(pendente)
                pendente = None
                if len(resultados) >= limite:
                    break
                continue

            if link is not None:
                url = self._limpar_url(link.get("href", ""))
                titulo = utils.limpar_espacos(link.get_text())
                if url and titulo and not utils.dominio_em_lista(url, ["duckduckgo.com"]):
                    if pendente is not None:
                        resultados.append(pendente)
                        if len(resultados) >= limite:
                            break
                    pendente = ResultadoBusca(titulo=titulo, url=url, motor=self.nome)

        if pendente is not None and len(resultados) < limite:
            resultados.append(pendente)

        return resultados


class Bing(MotorBusca):
    """Busca no Bing — bom fallback quando o DuckDuckGo não retorna nada."""

    nome = "bing"

    def buscar(self, consulta: str, limite: int) -> List[ResultadoBusca]:
        # O Bing degrada muito com busca por frase exata entre aspas — devolve
        # resultados genéricos do primeiro termo. Sem aspas, a relevância volta.
        consulta = consulta.replace('"', " ").strip()

        html = self._corpo(
            "GET",
            config.URL_BING,
            params={"q": consulta, "setlang": "pt-BR", "cc": "BR", "count": str(limite)},
        )
        if not html:
            return []

        sopa = self._sopa(html)
        resultados: List[ResultadoBusca] = []

        for bloco in sopa.select("li.b_algo")[: limite * 2]:
            link = bloco.select_one("h2 a") or bloco.select_one("a")
            if not link:
                continue
            url = self._limpar_url(link.get("href", ""))
            if not url or utils.dominio_em_lista(url, ["bing.com", "microsoft.com"]):
                continue
            legenda = bloco.select_one(".b_caption p") or bloco.select_one("p")
            resultados.append(
                ResultadoBusca(
                    titulo=utils.limpar_espacos(link.get_text()),
                    url=url,
                    snippet=utils.limpar_espacos(legenda.get_text() if legenda else ""),
                    motor=self.nome,
                )
            )
            if len(resultados) >= limite:
                break

        return resultados


class GoogleSearch(MotorBusca):
    """
    Busca direta no Google (HTML).

    Incluído por exigência do escopo. O Google bloqueia scraping com
    frequência; ao detectar captcha, :class:`utils.CaptchaDetectado` é
    propagada para que o motor pause e avise o usuário.
    """

    nome = "google"
    fator_delay = 2.4

    def buscar(self, consulta: str, limite: int) -> List[ResultadoBusca]:
        html = self._corpo(
            "GET",
            config.URL_GOOGLE,
            params={"q": consulta, "hl": "pt-BR", "gl": "br", "num": str(limite + 4)},
        )
        if not html:
            return []

        sopa = self._sopa(html)
        resultados: List[ResultadoBusca] = []
        vistos = set()

        # O Google muda de marcação com frequência; a estratégia é varrer todos
        # os links externos e associar o texto do bloco pai como snippet.
        for link in sopa.select("a[href]"):
            url = self._limpar_url(link.get("href", ""))
            if not url or not url.startswith("http"):
                continue
            if utils.dominio_em_lista(
                url, ["google.com", "googleusercontent.com", "gstatic.com", "youtube.com"]
            ):
                continue
            chave = utils.url_limpa(url)
            if chave in vistos:
                continue

            titulo = utils.limpar_espacos(link.get_text())
            if len(titulo) < 3:
                cabecalho = link.find("h3")
                titulo = utils.limpar_espacos(cabecalho.get_text()) if cabecalho else ""
            if not titulo:
                continue

            bloco = link.find_parent(["div", "li"])
            snippet = utils.limpar_espacos(bloco.get_text())[:400] if bloco else ""

            vistos.add(chave)
            resultados.append(
                ResultadoBusca(titulo=titulo, url=url, snippet=snippet, motor=self.nome)
            )
            if len(resultados) >= limite:
                break

        return resultados


# Registro de motores disponíveis por nome.
MOTORES = {
    DuckDuckGoHTML.nome: DuckDuckGoHTML,
    Bing.nome: Bing,
    GoogleSearch.nome: GoogleSearch,
}


# ===========================================================================
# Agregador
# ===========================================================================

class Buscador:
    """
    Agrega os motores de busca e implementa a lógica de alto nível de pesquisa.

    Além de consultar, valida a correspondência entre o resultado e a empresa
    procurada — nenhum contato é aproveitado sem que o nome e, quando possível,
    a cidade confiram.
    """

    def __init__(
        self,
        cliente: utils.ClienteHTTP,
        cfg: Optional[config.Configuracao] = None,
    ) -> None:
        self.cliente = cliente
        self.cfg = cfg or config.Configuracao()
        self.motores: List[MotorBusca] = [
            MOTORES[nome](cliente)
            for nome in config.ORDEM_BUSCADORES
            if nome in MOTORES and nome not in self.cfg.fontes_desabilitadas
        ]
        if not self.cfg.usar_google_search:
            self.motores = [m for m in self.motores if m.nome != "google"]
        self.log = logger

    # ------------------------------------------------------------------
    # Busca bruta
    # ------------------------------------------------------------------

    def buscar(
        self, consulta: str, limite: int = config.MAX_RESULTADOS_BUSCA
    ) -> List[ResultadoBusca]:
        """
        Executa a consulta no primeiro motor que responder com resultados.

        Falhas de rede em um motor apenas fazem passar para o próximo; apenas
        :class:`utils.CaptchaDetectado` é propagada, pois exige intervenção.
        """
        disponiveis = [m for m in self.motores if m.disponivel]
        if not disponiveis:
            self.log.debug("Todos os buscadores estão desligados no momento.")
            return []

        for motor in disponiveis:
            try:
                resultados = motor.buscar(consulta, limite)
            except utils.CaptchaDetectado:
                # Google bloqueado não deve derrubar a pesquisa inteira quando
                # existe outro motor disponível; só propaga se for o último.
                motor.disjuntor.registrar_falha()
                if motor is disponiveis[-1]:
                    raise
                self.log.warning("Captcha em %s; tentando o próximo motor.", motor.nome)
                continue
            except Exception as exc:
                self.log.debug("Motor %s falhou: %s", motor.nome, exc)
                continue

            if resultados:
                self.log.debug(
                    "%s retornou %d resultados para %r.", motor.nome, len(resultados), consulta
                )
                return resultados

        self.log.debug("Nenhum motor retornou resultados para %r.", consulta)
        return []

    def buscar_varias(
        self, consultas: Sequence[str], limite: int = config.MAX_RESULTADOS_BUSCA
    ) -> List[ResultadoBusca]:
        """Executa várias consultas, agregando e deduplicando por URL."""
        agregado: List[ResultadoBusca] = []
        vistos = set()
        for consulta in consultas:
            for item in self.buscar(consulta, limite):
                chave = utils.url_limpa(item.url).lower()
                if chave and chave not in vistos:
                    vistos.add(chave)
                    agregado.append(item)
            if len(agregado) >= limite * 2:
                break
        return agregado

    # ------------------------------------------------------------------
    # Site oficial
    # ------------------------------------------------------------------

    def descobrir_site_oficial(
        self, empresa: Empresa, resultados: Optional[List[ResultadoBusca]] = None
    ) -> Optional[Candidato]:
        """
        Identifica o site oficial da empresa entre os resultados de busca.

        Critérios de aceitação (todos conservadores):

        1. o domínio não pode ser rede social, catálogo, diretório ou agregador;
        2. o domínio ou o título da página precisa conter tokens distintivos da
           razão social;
        3. quanto maior a coincidência entre domínio e nome, maior o score.

        Returns:
            O melhor :class:`Candidato` ou ``None`` se nada for confiável.
        """
        if resultados is None:
            resultados = self.buscar_varias(empresa.consultas()[:2])

        distintivos = utils.matcher.tokens_distintivos(empresa.razao_social)
        if not distintivos:
            return None

        candidatos: List[Candidato] = []

        for item in resultados:
            if not utils.pode_ser_site_oficial(item.url):
                continue

            dominio = utils.dominio_base(item.url)
            nucleo = utils.normalizar(dominio.split(".")[0])

            # Score pelo domínio: tokens distintivos presentes no host.
            presentes = [t for t in distintivos if len(t) >= 3 and t in nucleo]
            score_dominio = len(presentes) / len(distintivos) if distintivos else 0.0
            # Domínio idêntico ao nome concatenado vale nota máxima.
            if nucleo and nucleo == "".join(sorted(distintivos)) or nucleo == "".join(
                utils.matcher.tokens(empresa.razao_social)
            ):
                score_dominio = 1.0

            # Score pelo título da página.
            _, score_titulo = utils.matcher.compativel(empresa.razao_social, item.titulo)

            score = max(score_dominio, score_titulo * 0.95)
            if score < 0.34:
                continue

            candidatos.append(
                Candidato(
                    nome=item.titulo or dominio,
                    url=item.url,
                    fonte=Fonte.SITE_OFICIAL,
                    cidade=empresa.cidade,
                    score_nome=round(score, 4),
                    cidade_confere=utils.cidade_confere(empresa.cidade, item.texto),
                    texto=item.texto,
                )
            )

        if not candidatos:
            return None

        candidatos.sort(key=lambda c: c.score_nome, reverse=True)
        melhor = candidatos[0]
        self.log.debug("Site oficial candidato para %s: %s", empresa.razao_social, melhor)
        return melhor

    # ------------------------------------------------------------------
    # Coleta a partir dos resultados
    # ------------------------------------------------------------------

    def coletar_de_resultados(
        self,
        empresa: Empresa,
        resultado: ResultadoPesquisa,
        resultados: List[ResultadoBusca],
        abrir_paginas: bool = True,
        max_paginas: int = 3,
    ) -> int:
        """
        Extrai contatos dos snippets e, opcionalmente, das páginas de diretório.

        Cada item passa por validação de nome e cidade antes de qualquer dado
        ser aproveitado. Itens duvidosos são registrados como observação, não
        como contato.

        Returns:
            Quantidade de dados novos efetivamente registrados.
        """
        registrados = 0
        paginas_abertas = 0

        for item in resultados:
            fonte = item.fonte_provavel()
            if fonte == Fonte.SITE_OFICIAL:
                # Sites oficiais são tratados pelo módulo `site`, com varredura
                # completa de páginas internas.
                continue

            aceito, score = utils.matcher.compativel(empresa.razao_social, item.titulo)
            confere_cidade = utils.cidade_confere(empresa.cidade, item.texto)

            if not aceito:
                # Sem nome compatível no título, tenta o corpo do snippet.
                aceito_snip, score_snip = utils.matcher.compativel(
                    empresa.razao_social, item.snippet[:200]
                )
                aceito, score = aceito_snip, max(score, score_snip)

            if score < config.SIMILARIDADE_MINIMA_DUVIDA:
                continue

            # Cidade divergente é motivo suficiente para descartar.
            if confere_cidade is False:
                continue

            # Confiança: começa pela fonte e é rebaixada quando faltam
            # confirmações (cidade desconhecida ou nome apenas plausível).
            confianca = fonte.confianca
            if not aceito or confere_cidade is None:
                confianca = _rebaixar(confianca)

            evidencia = Evidencia(
                fonte=fonte,
                url=item.url,
                trecho=utils.truncar(item.texto, 220),
                detalhe=f"motor={item.motor}; score_nome={score:.2f}",
            )
            resultado.registrar_fonte(f"{fonte.value} ({item.dominio})")

            registrados += self._extrair_para(
                resultado, item.texto, evidencia, confianca, empresa
            )

            # Abre a página do diretório para obter dados que não cabem no snippet.
            if (
                abrir_paginas
                and paginas_abertas < max_paginas
                and fonte in (Fonte.DIRETORIO, Fonte.LINKEDIN, Fonte.CATALOGO)
            ):
                paginas_abertas += 1
                registrados += self._coletar_pagina(
                    empresa, resultado, item, fonte, confianca
                )

        return registrados

    def _coletar_pagina(
        self,
        empresa: Empresa,
        resultado: ResultadoPesquisa,
        item: ResultadoBusca,
        fonte: Fonte,
        confianca: Confianca,
    ) -> int:
        """Abre a página do diretório e extrai contatos com nova validação."""
        try:
            html = self.cliente.obter_texto(item.url)
        except utils.CaptchaDetectado:
            raise
        except Exception as exc:
            self.log.debug("Falha ao abrir %s: %s", item.url, exc)
            return 0

        if not html:
            return 0

        texto = utils.html_para_texto(html)
        if not texto:
            return 0

        # Revalida com o conteúdo completo: o nome da empresa precisa aparecer.
        _, score = utils.matcher.compativel(empresa.razao_social, texto[:3000])
        distintivos = utils.matcher.tokens_distintivos(empresa.razao_social)
        texto_norm = utils.normalizar(texto[:6000])
        presentes = sum(1 for t in distintivos if t in texto_norm)
        if distintivos and presentes < max(1, len(distintivos) // 2):
            self.log.debug(
                "Página %s não confirma a empresa %r; descartada.",
                item.url, empresa.razao_social,
            )
            return 0

        if utils.cidade_confere(empresa.cidade, texto[:6000]) is False:
            return 0

        evidencia = Evidencia(
            fonte=fonte,
            url=item.url,
            trecho=utils.truncar(texto, 220),
            detalhe=f"página completa; tokens_confirmados={presentes}/{len(distintivos)}",
        )
        return self._extrair_para(resultado, texto, evidencia, confianca, empresa)

    def _extrair_para(
        self,
        resultado: ResultadoPesquisa,
        texto: str,
        evidencia: Evidencia,
        confianca: Confianca,
        empresa: Empresa,
    ) -> int:
        """Extrai telefones, e-mails e WhatsApps de um texto e registra no resultado."""
        registrados = 0

        for telefone in utils.extrair_telefones(texto):
            conf = confianca
            if not utils.ddd_coerente_com_local(
                telefone.digitos, empresa.cidade, empresa.uf
            ):
                conf = _rebaixar(conf)
            if telefone.ddd_herdado:
                conf = _rebaixar(conf)
            if resultado.adicionar_telefone(telefone.formatado, evidencia, conf):
                registrados += 1

        for email in utils.extrair_emails(texto):
            if resultado.adicionar_email(email, evidencia, confianca):
                registrados += 1

        for whats in utils.extrair_whatsapps(texto):
            if resultado.adicionar_whatsapp(whats, evidencia, confianca):
                registrados += 1

        return registrados


# ===========================================================================
# Auxiliares
# ===========================================================================

def _rebaixar(confianca: Confianca) -> Confianca:
    """Reduz um nível de confiança (Alta -> Média -> Baixa -> Baixa)."""
    return {
        Confianca.ALTA: Confianca.MEDIA,
        Confianca.MEDIA: Confianca.BAIXA,
        Confianca.BAIXA: Confianca.BAIXA,
    }[confianca]
