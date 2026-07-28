# -*- coding: utf-8 -*-
"""
site.py
=======

Rastreamento do site oficial da empresa — a fonte de maior prioridade.

Fluxo do :class:`RastreadorSite`:

1. baixa a página inicial e **valida** que o site pertence à empresa buscada
   (tokens distintivos da razão social, cidade e/ou CNPJ presentes na página);
2. localiza links internos de contato ("Contato", "Fale Conosco", "Atendimento",
   "Quem Somos", "Nossa Empresa", etc.);
3. visita essas páginas dentro do mesmo domínio;
4. extrai telefones, celulares, WhatsApp (``wa.me``), e-mails, endereço e a
   existência de formulário de contato.

Links ``tel:``, ``mailto:`` e ``wa.me`` têm prioridade sobre texto solto: são
declarações explícitas do próprio site e praticamente eliminam falso positivo.

Se a validação de identidade falhar, **nada é aproveitado** — é preferível
deixar a empresa sem contato a copiar o telefone do site errado.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import config
import utils
from modelos import Confianca, Empresa, Evidencia, Fonte, ResultadoPesquisa

logger = logging.getLogger("localizador.site")


# ===========================================================================
# Resultado do rastreamento
# ===========================================================================

@dataclass
class ResultadoSite:
    """Sumário do que o rastreamento do site produziu."""

    confirmado: bool = False
    dominio: str = ""
    paginas_visitadas: List[str] = field(default_factory=list)
    contatos_registrados: int = 0
    endereco: str = ""
    tem_formulario: bool = False
    motivo: str = ""

    @property
    def paginas(self) -> int:
        return len(self.paginas_visitadas)


# ===========================================================================
# Rastreador
# ===========================================================================

class RastreadorSite:
    """
    Visita o site oficial e extrai contatos com validação de identidade.

    Example:
        >>> rastreador = RastreadorSite(cliente, cfg)
        >>> info = rastreador.rastrear(empresa, "https://empresa.com.br", resultado)
        >>> info.confirmado
        True
    """

    # Padrões de href que interessam.
    _RE_TEL = re.compile(r"^tel:(.+)$", re.IGNORECASE)
    _RE_MAILTO = re.compile(r"^mailto:([^?]+)", re.IGNORECASE)

    # Rótulos que indicam endereço no texto da página.
    _RE_ENDERECO = re.compile(
        r"((?:rua|avenida|av\.|alameda|rodovia|rod\.|estrada|travessa|praça|praca|"
        r"largo|via)\s+[^\n|]{6,110}?\d{1,6}[^\n|]{0,60})",
        re.IGNORECASE,
    )

    def __init__(
        self,
        cliente: utils.ClienteHTTP,
        cfg: Optional[config.Configuracao] = None,
    ) -> None:
        self.cliente = cliente
        self.cfg = cfg or config.Configuracao()
        self.log = logger

    # ==================================================================
    # API pública
    # ==================================================================

    def rastrear(
        self,
        empresa: Empresa,
        url_inicial: str,
        resultado: ResultadoPesquisa,
        confianca_maxima: Confianca = Confianca.ALTA,
    ) -> ResultadoSite:
        """
        Rastreia o site oficial e registra os contatos encontrados.

        Args:
            empresa: Empresa buscada (fonte da verdade para validação).
            url_inicial: URL do site candidato.
            resultado: Objeto onde os contatos são acumulados.
            confianca_maxima: Teto de confiança aplicado aos dados deste site.
                É rebaixado quando a identidade do site é apenas provável.

        Returns:
            :class:`ResultadoSite` com o sumário do rastreamento.
        """
        info = ResultadoSite(dominio=utils.dominio_base(url_inicial))

        html_inicial = self._baixar(url_inicial)
        if not html_inicial:
            info.motivo = "Site inacessível."
            return info

        texto_inicial = utils.html_para_texto(html_inicial)
        confirmado, confianca, motivo = self._validar_identidade(
            empresa, url_inicial, texto_inicial, confianca_maxima
        )
        info.confirmado = confirmado
        info.motivo = motivo

        if not confirmado:
            self.log.info(
                "Site %s descartado para %r: %s", url_inicial, empresa.razao_social, motivo
            )
            return info

        resultado.registrar_fonte(f"Site Oficial ({info.dominio})")
        resultado.definir_site(
            self._url_raiz(url_inicial),
            Evidencia(
                fonte=Fonte.SITE_OFICIAL,
                url=url_inicial,
                trecho=utils.truncar(texto_inicial, 200),
                detalhe=motivo,
            ),
            confianca,
        )

        # --- percorre página inicial + páginas de contato ---------------
        for url, html, texto in self._percorrer(url_inicial, html_inicial, texto_inicial):
            info.paginas_visitadas.append(url)
            evidencia = Evidencia(
                fonte=Fonte.SITE_OFICIAL,
                url=url,
                trecho=utils.truncar(texto, 200),
                detalhe=motivo,
            )
            info.contatos_registrados += self._extrair_pagina(
                empresa, resultado, html, texto, evidencia, confianca,
                identidade_por_cnpj=motivo.startswith("CNPJ"),
            )

            if not info.endereco:
                info.endereco = self._extrair_endereco(texto)
            if not info.tem_formulario:
                info.tem_formulario = self._detectar_formulario(html)

        if info.endereco and not resultado.endereco:
            resultado.endereco = info.endereco

        self.log.debug(
            "Site %s: %d página(s), %d contato(s).",
            info.dominio, info.paginas, info.contatos_registrados,
        )
        return info

    # ==================================================================
    # Validação de identidade
    # ==================================================================

    def _validar_identidade(
        self, empresa: Empresa, url: str, texto: str, teto: Confianca
    ) -> Tuple[bool, Confianca, str]:
        """
        Confirma que o site pertence à empresa da planilha.

        Sinais avaliados, em ordem de força:

        1. **CNPJ** da empresa presente na página — prova definitiva, pois a
           maioria dos sites brasileiros o exibe no rodapé;
        2. **tokens distintivos** da razão social presentes no texto ou no
           domínio (palavras genéricas do ramo não contam);
        3. **cidade** da planilha citada na página.

        A regra central é: *um único token distintivo nunca basta*. Razões
        sociais como "A & A EXECUTIVA TRANSPORTES" reduzem-se ao token
        "executiva", que casa com qualquer site que use essa palavra comum no
        domínio. Sem um segundo token ou o CNPJ, o site é rejeitado.

        Returns:
            Tupla ``(confirmado, confianca_efetiva, motivo)``.
        """
        texto_norm = utils.normalizar(texto[:20000])
        dominio = utils.dominio_base(url)
        nucleo_dominio = utils.normalizar(dominio.replace(".", " "))

        # --- 1) CNPJ: prova definitiva ---------------------------------
        if empresa.cnpj:
            if empresa.cnpj in {utils.so_digitos(c) for c in utils.extrair_cnpjs(texto)}:
                return True, teto, "CNPJ confirmado na página."

        distintivos = utils.matcher.tokens_distintivos(empresa.razao_social)
        if not distintivos:
            return False, Confianca.BAIXA, "Razão social sem tokens distintivos."

        no_texto = {t for t in distintivos if len(t) >= 3 and t in texto_norm}
        no_dominio = {t for t in distintivos if len(t) >= 3 and t in nucleo_dominio}
        cobertos = no_texto | no_dominio
        cobertura = len(cobertos) / len(distintivos)
        cidade_ok = utils.cidade_confere(empresa.cidade, texto[:20000])

        # --- 2) Token único ------------------------------------------
        if len(distintivos) == 1:
            token = next(iter(distintivos))

            if config.EXIGIR_CNPJ_PARA_TOKEN_UNICO:
                return (
                    False,
                    Confianca.BAIXA,
                    f"Único token distintivo ({token!r}) e modo paranoico ativo: "
                    "identidade exige confirmação por CNPJ.",
                )

            # Com um só token, o texto da página não basta: a palavra pode
            # aparecer por acaso. Exige-se que ela esteja no próprio domínio.
            if config.TOKEN_UNICO_EXIGE_DOMINIO and not no_dominio:
                return (
                    False,
                    Confianca.BAIXA,
                    f"Único token distintivo ({token!r}) ausente do domínio "
                    f"{dominio!r}: identidade não confirmada.",
                )
            if not cobertos:
                return (
                    False,
                    Confianca.BAIXA,
                    f"Token distintivo ({token!r}) não encontrado na página.",
                )

            if cidade_ok:
                return True, teto, f"Token {token!r} no domínio e cidade confirmada."
            return (
                True,
                _rebaixar(teto),
                f"Token {token!r} no domínio, cidade não localizada na página.",
            )

        # --- 3) Confirmação insuficiente de tokens ---------------------
        if len(cobertos) < config.MIN_TOKENS_CONFIRMACAO_SITE or cobertura < 0.5:
            return (
                False,
                Confianca.BAIXA,
                f"Identidade não confirmada: {len(cobertos)}/{len(distintivos)} "
                f"token(s) distintivo(s) encontrado(s).",
            )

        # --- 4) Dois ou mais tokens confirmados ------------------------
        if cidade_ok:
            return (
                True,
                teto,
                f"Nome ({len(cobertos)}/{len(distintivos)} tokens) e cidade "
                "confirmados na página.",
            )

        # Sem a cidade, a identidade é provável mas não certa -> rebaixa.
        return (
            True,
            _rebaixar(teto),
            f"Nome confirmado ({len(cobertos)}/{len(distintivos)} tokens), "
            "cidade não localizada na página.",
        )

    # ==================================================================
    # Navegação
    # ==================================================================

    def _percorrer(self, url_inicial: str, html_inicial: str, texto_inicial: str):
        """
        Gera ``(url, html, texto)`` para a página inicial e as de contato.

        Restringe-se ao domínio do site e respeita o limite configurado de
        páginas, evitando rastreamento indefinido.
        """
        yield url_inicial, html_inicial, texto_inicial

        base = utils.dominio_base(url_inicial)
        visitadas: Set[str] = {utils.url_limpa(url_inicial).lower()}
        fila: deque = deque(self._links_de_contato(url_inicial, html_inicial))

        limite = max(1, self.cfg.max_paginas_site)
        while fila and len(visitadas) < limite:
            url = fila.popleft()
            chave = utils.url_limpa(url).lower()
            if chave in visitadas:
                continue
            if utils.dominio_base(url) != base:
                continue

            visitadas.add(chave)
            html = self._baixar(url)
            if not html:
                continue
            yield url, html, utils.html_para_texto(html)

    def _links_de_contato(self, base: str, html: str) -> List[str]:
        """
        Extrai links internos que provavelmente levam a páginas de contato.

        Ordena colocando "contato"/"fale conosco" na frente, pois são as
        páginas com maior densidade de telefones.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # pragma: no cover
            return []

        try:
            sopa = BeautifulSoup(html, "lxml")
        except Exception:
            sopa = BeautifulSoup(html, "html.parser")

        pontuados: List[Tuple[int, str]] = []
        vistos: Set[str] = set()

        for tag in sopa.find_all("a", href=True):
            href = tag["href"].strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:", "whatsapp:")):
                continue

            url = utils.absolutizar(base, href)
            if not url.startswith("http"):
                continue
            chave = utils.url_limpa(url).lower()
            if chave in vistos:
                continue

            alvo = utils.normalizar(f"{url} {tag.get_text()}")
            pontos = 0
            for i, palavra in enumerate(config.PALAVRAS_PAGINA_CONTATO):
                if utils.normalizar(palavra.replace("-", " ")) in alvo:
                    # Palavras do início da lista são as mais relevantes.
                    pontos = max(pontos, 100 - i)
            if pontos:
                vistos.add(chave)
                pontuados.append((pontos, url))

        pontuados.sort(key=lambda p: p[0], reverse=True)
        return [url for _, url in pontuados]

    def _baixar(self, url: str) -> str:
        """Baixa uma página tratando erros de rede sem interromper o fluxo."""
        try:
            return self.cliente.obter_texto(url)
        except utils.CaptchaDetectado:
            raise
        except Exception as exc:
            self.log.debug("Falha ao baixar %s: %s", url, exc)
            return ""

    # ==================================================================
    # Extração
    # ==================================================================

    def _extrair_pagina(
        self,
        empresa: Empresa,
        resultado: ResultadoPesquisa,
        html: str,
        texto: str,
        evidencia: Evidencia,
        confianca: Confianca,
        identidade_por_cnpj: bool = False,
    ) -> int:
        """
        Extrai todos os contatos de uma página.

        Links declarativos (``tel:``, ``mailto:``, ``wa.me``) mantêm a confiança
        integral; números soltos no texto podem ser rebaixados quando o DDD não
        é coerente com a UF ou quando o DDD foi herdado do número anterior.

        Args:
            identidade_por_cnpj: Quando ``False``, um DDD de outro estado
                derruba o telefone direto para confiança Baixa — sem o CNPJ
                confirmando a empresa, um número de outra região é forte
                indício de que o site é de um homônimo.
        """
        registrados = 0
        hrefs = self._coletar_hrefs(html)

        def _ajustar_por_ddd(numero: str, base: Confianca) -> Confianca:
            """Aplica a penalidade de DDD incompatível com a UF da empresa."""
            if utils.ddd_coerente_com_uf(numero, empresa.uf):
                return base
            return _rebaixar(base) if identidade_por_cnpj else Confianca.BAIXA

        # --- 1) links tel: (declaração explícita do site) ---------------
        for href in hrefs:
            m = self._RE_TEL.match(href)
            if not m:
                continue
            digitos = utils.so_digitos(m.group(1))
            if digitos.startswith("55") and len(digitos) > 11:
                digitos = digitos[2:]
            if not utils.telefone_valido(digitos):
                continue
            ev = Evidencia(
                fonte=evidencia.fonte, url=evidencia.url,
                trecho=evidencia.trecho, detalhe="link tel:",
            )
            if resultado.adicionar_telefone(
                utils.formatar_telefone(digitos), ev, _ajustar_por_ddd(digitos, confianca)
            ):
                registrados += 1

        # --- 2) links mailto: ------------------------------------------
        for href in hrefs:
            m = self._RE_MAILTO.match(href)
            if not m:
                continue
            email = utils.limpar_espacos(m.group(1)).lower()
            if not utils.email_valido(email):
                continue
            if not self._email_pertence_ao_site(email, evidencia.url):
                continue
            ev = Evidencia(
                fonte=evidencia.fonte, url=evidencia.url,
                trecho=evidencia.trecho, detalhe="link mailto:",
            )
            if resultado.adicionar_email(email, ev, confianca):
                registrados += 1

        # --- 3) WhatsApp (links wa.me / api.whatsapp.com) ---------------
        for numero in utils.extrair_whatsapps(" ".join(hrefs) + " " + html[:200000]):
            ev = Evidencia(
                fonte=evidencia.fonte, url=evidencia.url,
                trecho=evidencia.trecho, detalhe="link WhatsApp",
            )
            conf = _ajustar_por_ddd(numero, confianca)
            if resultado.adicionar_whatsapp(numero, ev, conf):
                registrados += 1
            # Um WhatsApp também é um telefone de contato válido.
            if resultado.adicionar_telefone(numero, ev, conf):
                registrados += 1

        # --- 4) texto visível ------------------------------------------
        for telefone in utils.extrair_telefones(texto):
            conf = _ajustar_por_ddd(telefone.digitos, confianca)
            if telefone.ddd_herdado:
                conf = _rebaixar(conf)
            ev = Evidencia(
                fonte=evidencia.fonte, url=evidencia.url,
                trecho=utils.truncar(telefone.contexto, 200),
                detalhe="texto da página",
            )
            if resultado.adicionar_telefone(telefone.formatado, ev, conf):
                registrados += 1

        for email in utils.extrair_emails(texto):
            if not self._email_pertence_ao_site(email, evidencia.url):
                continue
            if resultado.adicionar_email(email, evidencia, confianca):
                registrados += 1

        return registrados

    @staticmethod
    def _email_pertence_ao_site(email: str, url_pagina: str) -> bool:
        """
        Diz se o e-mail pode pertencer à empresa dona do site.

        Aceita apenas duas situações:

        * o domínio do e-mail é o mesmo do site (``@empresa.com.br`` em
          ``empresa.com.br``);
        * o e-mail está em um provedor gratuito (muitas empresas pequenas usam
          Gmail como contato oficial).

        Qualquer outro domínio pertence a um terceiro citado na página —
        prefeitura, parceiro, agência que fez o site — e é descartado.
        """
        dominio_email = utils.dominio_base(email.split("@")[-1])
        if not dominio_email:
            return False
        if dominio_email in config.PROVEDORES_EMAIL_GRATUITOS:
            return True
        return dominio_email == utils.dominio_base(url_pagina)

    def _coletar_hrefs(self, html: str) -> List[str]:
        """Lista todos os ``href`` do documento (usa regex por ser mais rápido)."""
        return re.findall(r'href=["\']([^"\']+)["\']', html or "", flags=re.IGNORECASE)

    def _extrair_endereco(self, texto: str) -> str:
        """Captura a primeira ocorrência de um endereço reconhecível."""
        m = self._RE_ENDERECO.search(texto or "")
        return utils.truncar(m.group(1), 140) if m else ""

    @staticmethod
    def _detectar_formulario(html: str) -> bool:
        """Detecta a presença de formulário de contato na página."""
        if not html:
            return False
        baixo = html.lower()
        if "<form" not in baixo:
            return False
        return any(
            marcador in baixo
            for marcador in ("contato", "contact", "mensagem", "message", "assunto", "e-mail")
        )

    @staticmethod
    def _url_raiz(url: str) -> str:
        """Reduz a URL ao endereço raiz do site (``https://dominio.com.br``)."""
        from urllib.parse import urlparse

        try:
            partes = urlparse(url if "://" in url else "http://" + url)
            return f"{partes.scheme}://{partes.netloc}"
        except ValueError:
            return url


def _rebaixar(confianca: Confianca) -> Confianca:
    """Reduz um nível de confiança (Alta -> Média -> Baixa -> Baixa)."""
    return {
        Confianca.ALTA: Confianca.MEDIA,
        Confianca.MEDIA: Confianca.BAIXA,
        Confianca.BAIXA: Confianca.BAIXA,
    }[confianca]
