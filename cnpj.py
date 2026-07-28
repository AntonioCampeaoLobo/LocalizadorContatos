# -*- coding: utf-8 -*-
"""
cnpj.py
=======

Pesquisa inteligente por CNPJ e consulta a dados públicos da Receita Federal.

Esta é a etapa executada **antes** da pesquisa principal, conforme o escopo:
localizar CNPJ e nome fantasia para (a) enriquecer as consultas seguintes e
(b) validar que qualquer contato encontrado pertence de fato à empresa certa.

Estratégia:

1. buscar o CNPJ da empresa em fontes públicas (snippets e páginas de
   diretórios de CNPJ), aceitando apenas números com dígito verificador válido;
2. consultar cada CNPJ candidato em APIs públicas que espelham a base da
   Receita Federal (BrasilAPI, MinhaReceita, CNPJ.ws);
3. confrontar razão social, município e UF retornados com o que consta na
   planilha — só então o CNPJ é adotado;
4. aproveitar telefone e e-mail do cadastro oficial, que recebem confiança
   **Alta** por virem de fonte governamental.

Nada é inferido: se nenhum CNPJ candidato confirmar razão social **e**
município, a etapa devolve ``None`` e a pesquisa segue sem CNPJ.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import config
import utils
from modelos import Confianca, Empresa, Evidencia, Fonte, ResultadoPesquisa

logger = logging.getLogger("localizador.cnpj")


# ===========================================================================
# Modelo dos dados cadastrais
# ===========================================================================

@dataclass
class DadosCNPJ:
    """
    Dados cadastrais públicos de um CNPJ, normalizados entre provedores.

    Attributes:
        cnpj: 14 dígitos.
        razao_social: Razão social registrada.
        nome_fantasia: Nome fantasia, quando informado.
        municipio: Município do estabelecimento.
        uf: Unidade federativa.
        telefones: Telefones do cadastro, já formatados e validados.
        emails: E-mails do cadastro.
        situacao: Situação cadastral ("ATIVA", "BAIXADA", etc.).
        fonte_url: URL exata da consulta que originou os dados.
    """

    cnpj: str
    razao_social: str = ""
    nome_fantasia: str = ""
    municipio: str = ""
    uf: str = ""
    telefones: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    situacao: str = ""
    fonte_url: str = ""

    @property
    def ativa(self) -> bool:
        """Situação cadastral ativa (baixadas costumam ter contato obsoleto)."""
        return utils.normalizar(self.situacao) in ("ativa", "", "2")


# ===========================================================================
# Consultor
# ===========================================================================

class ConsultorCNPJ:
    """
    Consulta CNPJ em APIs públicas com fallback entre provedores.

    Os resultados são memorizados em cache por instância — a mesma empresa pode
    aparecer em múltiplas consultas e não faz sentido repetir a chamada.
    """

    def __init__(self, cliente: utils.ClienteHTTP) -> None:
        self.cliente = cliente
        self._cache: Dict[str, Optional[DadosCNPJ]] = {}
        self.log = logger

    # ------------------------------------------------------------------

    def consultar(self, cnpj: str) -> Optional[DadosCNPJ]:
        """
        Consulta um CNPJ nos endpoints públicos configurados.

        Returns:
            :class:`DadosCNPJ` do primeiro provedor que responder, ou ``None``.
        """
        digitos = utils.so_digitos(cnpj)
        if not utils.cnpj_valido(digitos):
            return None
        if digitos in self._cache:
            return self._cache[digitos]

        dados: Optional[DadosCNPJ] = None
        for template in config.ENDPOINTS_CNPJ:
            url = template.format(cnpj=digitos)
            try:
                payload = self.cliente.obter_json(url, fator_delay=0.7)
            except utils.CaptchaDetectado:
                continue
            except Exception as exc:
                self.log.debug("Falha ao consultar %s: %s", url, exc)
                continue

            if not isinstance(payload, dict):
                continue
            dados = self._normalizar(payload, digitos, url)
            if dados and dados.razao_social:
                self.log.debug("CNPJ %s resolvido via %s.", digitos, url)
                break
            dados = None

        self._cache[digitos] = dados
        return dados

    # ------------------------------------------------------------------

    def _normalizar(self, payload: dict, cnpj: str, url: str) -> Optional[DadosCNPJ]:
        """
        Converte a resposta de qualquer provedor para :class:`DadosCNPJ`.

        Suporta os formatos da BrasilAPI/MinhaReceita (campos planos) e do
        CNPJ.ws (campos aninhados em ``estabelecimento``).
        """
        if payload.get("message") or payload.get("erro") or payload.get("detalhes"):
            if not payload.get("razao_social"):
                return None

        # --- formato CNPJ.ws (aninhado) --------------------------------
        estabelecimento = payload.get("estabelecimento")
        if isinstance(estabelecimento, dict):
            telefones = []
            for ddd_key, tel_key in (("ddd1", "telefone1"), ("ddd2", "telefone2")):
                bruto = f"{estabelecimento.get(ddd_key) or ''}{estabelecimento.get(tel_key) or ''}"
                formatado = self._formatar_telefone_cadastro(bruto)
                if formatado:
                    telefones.append(formatado)

            cidade = estabelecimento.get("cidade") or {}
            estado = estabelecimento.get("estado") or {}
            situacao = estabelecimento.get("situacao_cadastral") or ""

            return DadosCNPJ(
                cnpj=cnpj,
                razao_social=str(payload.get("razao_social") or "").strip(),
                nome_fantasia=str(estabelecimento.get("nome_fantasia") or "").strip(),
                municipio=str(cidade.get("nome") or "").strip(),
                uf=str(estado.get("sigla") or "").strip(),
                telefones=telefones,
                emails=[
                    e for e in [str(estabelecimento.get("email") or "").strip().lower()]
                    if utils.email_valido(e)
                ],
                situacao=str(situacao),
                fonte_url=url,
            )

        # --- formato BrasilAPI / MinhaReceita (plano) ------------------
        if not any(k in payload for k in ("razao_social", "nome_empresarial", "nome")):
            return None

        chaves_telefone = ["ddd_telefone_1", "ddd_telefone_2", "telefone"]
        if config.INCLUIR_FAX_DO_CADASTRO:
            chaves_telefone.append("ddd_fax")

        telefones = []
        for chave in chaves_telefone:
            formatado = self._formatar_telefone_cadastro(payload.get(chave))
            if formatado and formatado not in telefones:
                telefones.append(formatado)

        emails = []
        for chave in ("correio_eletronico", "email"):
            valor = str(payload.get(chave) or "").strip().lower()
            if valor and utils.email_valido(valor):
                emails.append(valor)

        return DadosCNPJ(
            cnpj=cnpj,
            razao_social=str(
                payload.get("razao_social")
                or payload.get("nome_empresarial")
                or payload.get("nome")
                or ""
            ).strip(),
            nome_fantasia=str(
                payload.get("nome_fantasia") or payload.get("fantasia") or ""
            ).strip(),
            municipio=str(payload.get("municipio") or payload.get("cidade") or "").strip(),
            uf=str(payload.get("uf") or "").strip(),
            telefones=telefones,
            emails=emails,
            situacao=str(
                payload.get("descricao_situacao_cadastral")
                or payload.get("situacao_cadastral")
                or payload.get("situacao")
                or ""
            ),
            fonte_url=url,
        )

    @staticmethod
    def _formatar_telefone_cadastro(bruto) -> str:
        """
        Formata um telefone vindo do cadastro, validando-o.

        A Receita entrega os números concatenados com o DDD e sem máscara
        (``"1938249898"``). Números inválidos são simplesmente descartados.
        """
        digitos = utils.so_digitos(bruto)
        if not digitos:
            return ""
        if not utils.telefone_valido(digitos):
            return ""
        return utils.formatar_telefone(digitos)


# ===========================================================================
# Localizador de CNPJ a partir do nome
# ===========================================================================

class LocalizadorCNPJ:
    """
    Descobre o CNPJ de uma empresa a partir da razão social e da cidade.

    O CNPJ só é adotado quando a consulta oficial confirma **razão social** e
    **município**. Isso protege contra homônimos: duas empresas de mesmo nome
    em cidades diferentes nunca serão confundidas.
    """

    def __init__(self, cliente: utils.ClienteHTTP, buscador, consultor: ConsultorCNPJ) -> None:
        self.cliente = cliente
        self.buscador = buscador
        self.consultor = consultor
        self.log = logger

    # ------------------------------------------------------------------

    def localizar(self, empresa: Empresa) -> Optional[DadosCNPJ]:
        """
        Executa a busca e a validação do CNPJ.

        Returns:
            :class:`DadosCNPJ` confirmado, ou ``None`` quando não há certeza.
        """
        candidatos = self._coletar_candidatos(empresa)
        if not candidatos:
            self.log.debug("Nenhum CNPJ candidato para %r.", empresa.razao_social)
            return None

        confirmados: List[tuple] = []

        for cnpj in candidatos[: config.MAX_CANDIDATOS_CNPJ]:
            dados = self.consultor.consultar(cnpj)
            if not dados or not dados.razao_social:
                continue

            aceito, score = utils.matcher.compativel(empresa.razao_social, dados.razao_social)
            # O nome fantasia também vale como confirmação de identidade.
            if not aceito and dados.nome_fantasia:
                aceito_f, score_f = utils.matcher.compativel(
                    empresa.razao_social, dados.nome_fantasia
                )
                aceito, score = aceito or aceito_f, max(score, score_f)

            if score < config.SIMILARIDADE_MINIMA_DUVIDA:
                continue

            municipio_ok = (
                utils.normalizar(dados.municipio) == utils.normalizar(empresa.cidade)
                if empresa.cidade and dados.municipio
                else None
            )
            if municipio_ok is False:
                self.log.debug(
                    "CNPJ %s descartado: município %r != %r.",
                    cnpj, dados.municipio, empresa.cidade,
                )
                continue

            confirmados.append((aceito and bool(municipio_ok), score, dados))

        if not confirmados:
            return None

        confirmados.sort(key=lambda t: (t[0], t[1]), reverse=True)
        pleno, score, dados = confirmados[0]

        # Mais de um CNPJ plenamente confirmado com nomes distintos = ambiguidade.
        plenos = [d for ok, _, d in confirmados if ok]
        if len(plenos) > 1 and len({d.cnpj for d in plenos}) > 1:
            nomes = {utils.normalizar(d.razao_social) for d in plenos}
            if len(nomes) > 1:
                self.log.info(
                    "Múltiplos CNPJs confirmados para %r — ambiguidade.",
                    empresa.razao_social,
                )
                return None

        if not pleno:
            self.log.debug(
                "CNPJ %s aceito parcialmente (score=%.2f, município não confirmado).",
                dados.cnpj, score,
            )
        return dados

    # ------------------------------------------------------------------

    def _coletar_candidatos(self, empresa: Empresa) -> List[str]:
        """
        Reúne CNPJs candidatos a partir de buscas web.

        Percorre as variantes de consulta em ordem e para assim que encontra
        candidatos — só insiste nas consultas mais caras quando as baratas não
        produziram nada. Somente números com dígito verificador válido entram
        na lista.
        """
        candidatos: List[str] = []
        vistos = set()
        resultados = []

        for consulta in empresa.consultas_cnpj():
            try:
                encontrados = self.buscador.buscar(
                    consulta, limite=config.MAX_RESULTADOS_BUSCA
                )
            except utils.CaptchaDetectado:
                raise
            except Exception as exc:
                self.log.debug("Busca de CNPJ falhou (%r): %s", consulta, exc)
                continue

            resultados.extend(encontrados)

            # 1) CNPJs presentes nos próprios snippets (mais barato).
            # 2) CNPJ embutido na URL de diretórios (cnpj.biz/12345678000190).
            for item in encontrados:
                for texto in (item.texto, item.url.replace("/", " ")):
                    for cnpj in utils.extrair_cnpjs(texto):
                        if cnpj not in vistos:
                            vistos.add(cnpj)
                            candidatos.append(cnpj)

            if candidatos:
                self.log.debug(
                    "%d CNPJ(s) candidato(s) via %r.", len(candidatos), consulta
                )
                break

        # 3) Abre até duas páginas de diretórios de CNPJ, se ainda não há candidatos.
        if not candidatos:
            abertas = 0
            for item in resultados:
                if abertas >= 2:
                    break
                if not utils.dominio_em_lista(
                    item.url,
                    config.DOMINIOS_DIRETORIOS_CONFIAVEIS + config.DOMINIOS_OFICIAIS,
                ):
                    continue
                abertas += 1
                try:
                    html = self.cliente.obter_texto(item.url)
                except utils.CaptchaDetectado:
                    raise
                except Exception:
                    continue
                texto = utils.html_para_texto(html)
                # Só aproveita a página se ela realmente fala da empresa buscada.
                _, score = utils.matcher.compativel(empresa.razao_social, texto[:2000])
                if score < config.SIMILARIDADE_MINIMA_DUVIDA:
                    continue
                for cnpj in utils.extrair_cnpjs(texto):
                    if cnpj not in vistos:
                        vistos.add(cnpj)
                        candidatos.append(cnpj)

        return candidatos


# ===========================================================================
# Integração com o resultado da pesquisa
# ===========================================================================

def aplicar_dados_cnpj(
    resultado: ResultadoPesquisa, dados: DadosCNPJ, empresa: Empresa
) -> int:
    """
    Registra no resultado os contatos vindos do cadastro oficial.

    Dados da Receita Federal recebem confiança **Alta** — são a fonte mais
    confiável disponível, atrás apenas do site oficial em atualidade.

    Returns:
        Quantidade de contatos novos registrados.
    """
    evidencia = Evidencia(
        fonte=Fonte.RECEITA_FEDERAL,
        url=dados.fonte_url,
        trecho=utils.truncar(
            f"{dados.razao_social} — {dados.municipio}/{dados.uf} — {dados.situacao}", 220
        ),
        detalhe=f"CNPJ {utils.formatar_cnpj(dados.cnpj)}",
    )

    # Cadastro baixado/inapto tem contato potencialmente obsoleto -> Média.
    confianca = Confianca.ALTA if dados.ativa else Confianca.MEDIA

    registrados = 0
    for telefone in dados.telefones:
        conf = confianca
        # Checagem por cidade, não por UF: um (11) da capital no cadastro de uma
        # empresa de Amparo é sinal de contato desatualizado ou de outra
        # unidade, e não merece a mesma confiança de um DDD local.
        if not utils.ddd_coerente_com_local(
            telefone, dados.municipio or empresa.cidade, dados.uf or empresa.uf
        ):
            conf = Confianca.MEDIA if conf == Confianca.ALTA else Confianca.BAIXA
        if resultado.adicionar_telefone(telefone, evidencia, conf):
            registrados += 1

    for email in dados.emails:
        if resultado.adicionar_email(email, evidencia, confianca):
            registrados += 1

    # Situação cadastral irregular é informação comercial relevante: evita que
    # o vendedor gaste uma ligação com uma empresa baixada ou inapta.
    if not dados.ativa and dados.situacao:
        aviso = f"Situação cadastral: {dados.situacao.upper()}"
        resultado.observacao = (
            f"{resultado.observacao} {aviso}".strip()
            if resultado.observacao
            else aviso
        )

    resultado.registrar_fonte(f"Receita Federal (CNPJ {utils.formatar_cnpj(dados.cnpj)})")
    return registrados
