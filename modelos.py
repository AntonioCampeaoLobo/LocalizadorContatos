# -*- coding: utf-8 -*-
"""
modelos.py
==========

Modelos de domínio compartilhados por todas as camadas.

Este módulo é o coração da **regra de confiabilidade** do sistema: nenhum dado
de contato pode existir sem uma :class:`Evidencia` associada, contendo a fonte
e a URL real de onde ele foi extraído. A API é desenhada para que seja
*impossível* registrar um telefone ou e-mail sem informar a procedência — o
próprio construtor rejeita a tentativa.

Isso torna estrutural (e não apenas documental) a proibição de inventar dados.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional

import config


def _sem_duplicatas(itens: List[str]) -> List[str]:
    """Remove duplicatas e entradas vazias preservando a ordem original."""
    vistos, unicos = set(), []
    for item in itens:
        chave = " ".join(item.split())
        if chave and chave not in vistos:
            vistos.add(chave)
            unicos.append(chave)
    return unicos


# ===========================================================================
# Enumerações
# ===========================================================================

class Fonte(Enum):
    """
    Fontes de dados suportadas, na ordem de prioridade definida no escopo.

    O valor de cada membro é o rótulo exibido no log e na planilha.
    """

    SITE_OFICIAL = "Site Oficial"
    GOOGLE_BUSINESS = "Google Business Profile"
    GOOGLE_SEARCH = "Google Search"
    RECEITA_FEDERAL = "Receita Federal"
    LINKEDIN = "LinkedIn"
    DIRETORIO = "Diretório Empresarial"
    CATALOGO = "Catálogo Comercial"

    @property
    def prioridade(self) -> int:
        """Menor número = maior prioridade (1 a 7, conforme o escopo)."""
        return _PRIORIDADE_FONTE[self]

    @property
    def confianca(self) -> "Confianca":
        """Nível de confiança padrão atribuído à fonte."""
        return _CONFIANCA_FONTE[self]


_PRIORIDADE_FONTE: Dict[Fonte, int] = {
    Fonte.SITE_OFICIAL: 1,
    Fonte.GOOGLE_BUSINESS: 2,
    Fonte.GOOGLE_SEARCH: 3,
    Fonte.RECEITA_FEDERAL: 4,
    Fonte.LINKEDIN: 5,
    Fonte.DIRETORIO: 6,
    Fonte.CATALOGO: 7,
}


class Confianca(Enum):
    """Nível de confiança de uma informação coletada."""

    ALTA = "Alta"
    MEDIA = "Média"
    BAIXA = "Baixa"

    @property
    def peso(self) -> int:
        """Peso numérico para comparação (maior = melhor)."""
        return {"Alta": 3, "Média": 2, "Baixa": 1}[self.value]

    @property
    def preenchivel(self) -> bool:
        """Indica se este nível autoriza preenchimento automático da planilha."""
        return self.value in config.CONFIANCAS_PREENCHIVEIS

    def __lt__(self, outra: "Confianca") -> bool:
        return self.peso < outra.peso


# Mapa fonte -> confiança, conforme a seção "Nível de Confiança" do escopo.
_CONFIANCA_FONTE: Dict[Fonte, Confianca] = {
    Fonte.SITE_OFICIAL: Confianca.ALTA,
    Fonte.GOOGLE_BUSINESS: Confianca.ALTA,
    Fonte.RECEITA_FEDERAL: Confianca.ALTA,
    Fonte.LINKEDIN: Confianca.MEDIA,
    Fonte.DIRETORIO: Confianca.MEDIA,
    Fonte.GOOGLE_SEARCH: Confianca.BAIXA,   # snippet solto vale pouco
    Fonte.CATALOGO: Confianca.BAIXA,
}


class StatusPesquisa(Enum):
    """Desfecho da pesquisa de uma empresa."""

    ENCONTRADO = "Encontrado"
    APENAS_EMAIL = "Apenas e-mail"
    NAO_ENCONTRADO = "Não encontrado"
    REVISAO_MANUAL = "Revisão Manual Necessária"
    CONFERENCIA_MANUAL = "Necessita conferência manual"
    IGNORADO = "Ignorado (já possuía contato)"
    ERRO = "Erro"
    CANCELADO = "Cancelado"

    @property
    def cor_linha(self) -> Optional[str]:
        """Cor ARGB aplicada à linha da planilha para este status."""
        return _COR_STATUS.get(self)


_COR_STATUS: Dict[StatusPesquisa, Optional[str]] = {
    StatusPesquisa.ENCONTRADO: config.COR_VERDE_CLARO,
    StatusPesquisa.APENAS_EMAIL: config.COR_AMARELO,
    StatusPesquisa.NAO_ENCONTRADO: config.COR_VERMELHO,
    StatusPesquisa.REVISAO_MANUAL: config.COR_LARANJA,
    StatusPesquisa.CONFERENCIA_MANUAL: config.COR_LARANJA,
    StatusPesquisa.IGNORADO: None,          # não repinta linhas já preenchidas
    StatusPesquisa.ERRO: config.COR_VERMELHO,
    StatusPesquisa.CANCELADO: None,
}


class TipoContato(Enum):
    """Categoria do dado de contato."""

    TELEFONE_FIXO = "Telefone fixo"
    CELULAR = "Celular"
    WHATSAPP = "WhatsApp"
    EMAIL = "E-mail"
    SITE = "Site oficial"


# ===========================================================================
# Evidência — a âncora de rastreabilidade
# ===========================================================================

@dataclass(frozen=True)
class Evidencia:
    """
    Prova de origem de uma informação.

    Toda informação gravada precisa apontar para uma URL pública real. O
    construtor recusa evidências sem URL, o que impede a criação de dados
    "órfãos" (isto é, inventados) em qualquer ponto do sistema.

    Attributes:
        fonte: Categoria da origem (site oficial, Receita Federal, etc.).
        url: Endereço público exato de onde o dado foi lido.
        trecho: Recorte textual do contexto, útil para auditoria manual.
        detalhe: Observação adicional (ex.: "extraído de link tel:").
    """

    fonte: Fonte
    url: str
    trecho: str = ""
    detalhe: str = ""

    def __post_init__(self) -> None:
        if not self.url or not str(self.url).strip():
            raise ValueError(
                "Evidência inválida: toda informação precisa de uma URL de origem. "
                "Dados sem procedência verificável não podem ser registrados."
            )

    @property
    def confianca(self) -> Confianca:
        return self.fonte.confianca

    def __str__(self) -> str:
        return f"{self.fonte.value} <{self.url}>"


# ===========================================================================
# Dado de contato
# ===========================================================================

@dataclass
class DadoContato:
    """
    Um único dado de contato acompanhado de sua procedência.

    Attributes:
        valor: Valor já normalizado (ex.: "(19) 3824-9898").
        tipo: Categoria do dado.
        evidencia: Origem verificável.
        confianca: Confiança efetiva — pode ser rebaixada em relação à da fonte
            quando a validação da empresa não foi totalmente conclusiva.
    """

    valor: str
    tipo: TipoContato
    evidencia: Evidencia
    confianca: Confianca = Confianca.BAIXA

    def __post_init__(self) -> None:
        if not self.valor or not str(self.valor).strip():
            raise ValueError("DadoContato exige um valor não vazio.")
        if not isinstance(self.evidencia, Evidencia):
            raise TypeError(
                "DadoContato exige uma Evidencia — dados sem origem são proibidos."
            )
        self.valor = str(self.valor).strip()

    @property
    def chave(self) -> str:
        """Chave de deduplicação (apenas dígitos para telefones, minúsculas p/ resto)."""
        if self.tipo in (TipoContato.TELEFONE_FIXO, TipoContato.CELULAR, TipoContato.WHATSAPP):
            return "".join(c for c in self.valor if c.isdigit())
        return self.valor.lower().rstrip("/")

    def __str__(self) -> str:
        return f"{self.valor} ({self.evidencia.fonte.value})"


# ===========================================================================
# Empresa lida da planilha
# ===========================================================================

@dataclass
class Empresa:
    """
    Uma linha de empresa da planilha de entrada.

    Attributes:
        linha: Número da linha na planilha (1-based, como no Excel).
        razao_social: Conteúdo da coluna "Razão Social".
        cidade: Conteúdo da coluna "Cidade".
        regiao: Conteúdo da coluna "Região".
        contato_existente: Conteúdo atual da coluna "Contato".
        uf: Unidade federativa presumida (default vem da configuração).
    """

    linha: int
    razao_social: str
    cidade: str = ""
    regiao: str = ""
    contato_existente: str = ""
    uf: str = config.UF_PADRAO

    # Preenchidos durante a pesquisa inteligente (etapa de identificação).
    cnpj: str = ""
    nome_fantasia: str = ""

    @property
    def identificador(self) -> str:
        """Rótulo curto usado em logs e na interface."""
        cidade = f" / {self.cidade}" if self.cidade else ""
        return f"L{self.linha} — {self.razao_social}{cidade}"

    def nome_busca(self) -> str:
        """
        Razão social preparada para consulta em buscador.

        Remove a forma societária (LTDA, ME, EPP, EIRELI) e as iniciais soltas
        comuns em razões sociais brasileiras: ``"A. B. CHISTELLI COMERCIAL"``
        vira ``"CHISTELLI COMERCIAL"``.

        Essa limpeza é decisiva na prática. Buscadores tratam iniciais isoladas
        como termos independentes: a consulta ``"A. B. CHISTELLI COMERCIAL"``
        devolvia páginas sobre a **letra B** — tabela do Brasileirão Série B,
        verbete da Wikipédia — e nenhum resultado sobre a empresa.
        """
        nome = self.razao_social.strip()

        # Iniciais com ponto: "A." , "A.R." , "A. B."
        nome = re.sub(r"\b[A-Za-zÀ-ÿ]\.\s*", " ", nome)
        # Letras isoladas remanescentes: "A & A" -> "&"
        nome = re.sub(r"(?:^|\s)[A-Za-zÀ-ÿ](?=\s|$)", " ", nome)
        # Forma societária ao final: "LTDA - ME", "EIRELI", "S/A"
        nome = re.sub(
            r"[\s\-,]*\b(?:LTDA|LIMITADA|EIRELI|EIRELLI|EPP|ME|MEI|S\.?/?A\.?|CIA)\b"
            r"(?:[\s\-,]*\b(?:LTDA|EPP|ME|MEI)\b)*\s*$",
            "",
            nome,
            flags=re.IGNORECASE,
        )
        # Pontuação órfã e espaços duplicados.
        nome = re.sub(r"[&\-,]+", " ", nome)
        nome = re.sub(r"\s+", " ", nome).strip(" .-&")

        # Se a limpeza esvaziou o nome, mantém o original.
        return nome if len(nome) >= 3 else self.razao_social.strip()

    def consultas(self) -> List[str]:
        """
        Gera as consultas usadas nos buscadores, da mais específica para a mais
        genérica.

        Alterna busca livre e busca por frase exata: a frase exata é precisa
        quando o nome está grafado igual na fonte, mas falha quando a planilha
        traz o nome truncado ou com grafia divergente — situação frequente.

        A cidade entra em todas as variações principais: é o principal
        desambiguador de homônimos.
        """
        nome = self.nome_busca()
        original = self.razao_social.strip()
        cidade = self.cidade.strip()
        consultas: List[str] = []

        if self.nome_fantasia and self.nome_fantasia.strip().upper() != original.upper():
            consultas.append(f"{self.nome_fantasia.strip()} {cidade} telefone".strip())

        if cidade:
            consultas.append(f"{nome} {cidade} telefone contato")
            consultas.append(f'"{nome}" {cidade}')
            consultas.append(f"{nome} {cidade} SP endereço telefone")
        consultas.append(f"{nome} telefone contato")

        # A razão social original nunca é usada como consulta: suas iniciais
        # soltas envenenam o buscador (ver nome_busca). Ela permanece como
        # referência para a validação de correspondência, não para a busca.
        return _sem_duplicatas(consultas)

    def consultas_cnpj(self) -> List[str]:
        """
        Consultas para descobrir o CNPJ, tentadas em ordem até haver candidatos.

        A última recorre explicitamente aos diretórios de CNPJ, que costumam ter
        a empresa indexada mesmo quando ela não tem presença própria na web —
        caso da maioria das micro e pequenas empresas da carteira.
        """
        nome = self.nome_busca()
        cidade = self.cidade.strip()

        return _sem_duplicatas([
            f"{nome} {cidade} CNPJ".strip(),
            f"{nome} CNPJ razão social {cidade}".strip(),
            f"{nome} {cidade} cnpj.biz consulta".strip(),
        ])


# ===========================================================================
# Resultado da pesquisa
# ===========================================================================

@dataclass
class ResultadoPesquisa:
    """
    Resultado consolidado da pesquisa de uma empresa.

    Acumula dados de contato de múltiplas fontes, deduplicando por valor e
    mantendo sempre a evidência de maior confiança para cada dado.
    """

    empresa: Empresa
    status: StatusPesquisa = StatusPesquisa.NAO_ENCONTRADO
    telefones: List[DadoContato] = field(default_factory=list)
    emails: List[DadoContato] = field(default_factory=list)
    whatsapps: List[DadoContato] = field(default_factory=list)
    site: Optional[DadoContato] = None
    endereco: str = ""
    observacao: str = ""
    erro: str = ""
    fontes_consultadas: List[str] = field(default_factory=list)
    inicio: float = field(default_factory=time.monotonic)
    duracao: float = 0.0

    # ------------------------------------------------------------------
    # Registro de dados (única porta de entrada — sempre exige evidência)
    # ------------------------------------------------------------------

    def adicionar_telefone(
        self, valor: str, evidencia: Evidencia, confianca: Optional[Confianca] = None
    ) -> bool:
        """
        Registra um telefone. Retorna ``True`` se foi de fato adicionado.

        Números já presentes não são duplicados; se o novo registro vier de uma
        fonte mais confiável, a evidência do existente é promovida.
        """
        celular = self._eh_celular(valor)
        tipo = TipoContato.CELULAR if celular else TipoContato.TELEFONE_FIXO
        return self._adicionar(self.telefones, valor, tipo, evidencia, confianca)

    def adicionar_email(
        self, valor: str, evidencia: Evidencia, confianca: Optional[Confianca] = None
    ) -> bool:
        """Registra um e-mail com sua evidência."""
        return self._adicionar(
            self.emails, valor.lower(), TipoContato.EMAIL, evidencia, confianca
        )

    def adicionar_whatsapp(
        self, valor: str, evidencia: Evidencia, confianca: Optional[Confianca] = None
    ) -> bool:
        """Registra um WhatsApp com sua evidência."""
        return self._adicionar(
            self.whatsapps, valor, TipoContato.WHATSAPP, evidencia, confianca
        )

    def definir_site(
        self, url: str, evidencia: Evidencia, confianca: Optional[Confianca] = None
    ) -> None:
        """Define o site oficial, mantendo o de maior confiança em caso de conflito."""
        novo = DadoContato(
            valor=url,
            tipo=TipoContato.SITE,
            evidencia=evidencia,
            confianca=confianca or evidencia.confianca,
        )
        if self.site is None or novo.confianca.peso > self.site.confianca.peso:
            self.site = novo

    def registrar_fonte(self, descricao: str) -> None:
        """Anota que uma fonte foi consultada (mesmo sem resultado)."""
        if descricao not in self.fontes_consultadas:
            self.fontes_consultadas.append(descricao)

    # ------------------------------------------------------------------
    # Consultas de estado
    # ------------------------------------------------------------------

    @property
    def confianca(self) -> Confianca:
        """Maior confiança entre os dados efetivamente coletados."""
        dados = self.telefones + self.emails + self.whatsapps
        if self.site:
            dados = dados + [self.site]
        if not dados:
            return Confianca.BAIXA
        return max((d.confianca for d in dados), key=lambda c: c.peso)

    @property
    def confianca_telefone(self) -> Optional[Confianca]:
        """Maior confiança entre os telefones coletados, se houver."""
        if not self.telefones:
            return None
        return max((t.confianca for t in self.telefones), key=lambda c: c.peso)

    @property
    def tem_telefone_preenchivel(self) -> bool:
        """Há ao menos um telefone com confiança Alta ou Média?"""
        return any(t.confianca.preenchivel for t in self.telefones)

    @property
    def tem_email_preenchivel(self) -> bool:
        """Há ao menos um e-mail com confiança Alta ou Média?"""
        return any(e.confianca.preenchivel for e in self.emails)

    @property
    def tem_algum_dado(self) -> bool:
        return bool(self.telefones or self.emails or self.whatsapps or self.site)

    @property
    def fonte_principal(self) -> Optional[Fonte]:
        """Fonte de maior prioridade entre as que geraram dados aproveitados."""
        dados = self.telefones + self.emails + self.whatsapps
        if self.site:
            dados = dados + [self.site]
        if not dados:
            return None
        return min((d.evidencia.fonte for d in dados), key=lambda f: f.prioridade)

    @property
    def url_principal(self) -> str:
        """URL da evidência do primeiro telefone (ou do primeiro dado disponível)."""
        for grupo in (self.telefones, self.emails, self.whatsapps):
            if grupo:
                return grupo[0].evidencia.url
        return self.site.evidencia.url if self.site else ""

    # ------------------------------------------------------------------
    # Formatação para a planilha
    # ------------------------------------------------------------------

    def telefones_texto(self, limite: int = config.MAX_TELEFONES_GRAVADOS) -> str:
        """Telefones preenchíveis, um por linha (quebra de linha na mesma célula)."""
        valores = self._valores_preenchiveis(self.telefones, limite)
        return "\n".join(valores)

    def emails_texto(self, limite: int = config.MAX_EMAILS_GRAVADOS) -> str:
        """E-mails preenchíveis, um por linha."""
        valores = self._valores_preenchiveis(self.emails, limite)
        return "\n".join(valores)

    def celula_contato(self) -> str:
        """
        Conteúdo final da coluna "Contato": telefones e, na sequência, e-mails,
        todos separados por quebra de linha dentro da mesma célula.
        """
        partes = [self.telefones_texto(), self.emails_texto()]
        return "\n".join(p for p in partes if p)

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _valores_preenchiveis(self, dados: List[DadoContato], limite: int) -> List[str]:
        """Ordena por confiança e prioridade da fonte, filtrando os não preenchíveis."""
        elegiveis = [d for d in dados if d.confianca.preenchivel]
        elegiveis.sort(
            key=lambda d: (-d.confianca.peso, d.evidencia.fonte.prioridade)
        )
        vistos, saida = set(), []
        for d in elegiveis:
            if d.chave in vistos:
                continue
            vistos.add(d.chave)
            saida.append(d.valor)
            if len(saida) >= limite:
                break
        return saida

    def _adicionar(
        self,
        colecao: List[DadoContato],
        valor: str,
        tipo: TipoContato,
        evidencia: Evidencia,
        confianca: Optional[Confianca],
    ) -> bool:
        """Insere ou promove um dado na coleção informada."""
        novo = DadoContato(
            valor=valor,
            tipo=tipo,
            evidencia=evidencia,
            confianca=confianca or evidencia.confianca,
        )
        for existente in colecao:
            if existente.chave == novo.chave:
                # Mesmo dado por fonte melhor -> promove evidência e confiança.
                if novo.confianca.peso > existente.confianca.peso or (
                    novo.evidencia.fonte.prioridade < existente.evidencia.fonte.prioridade
                ):
                    existente.confianca = max(
                        existente.confianca, novo.confianca, key=lambda c: c.peso
                    )
                    existente.evidencia = novo.evidencia
                return False
        colecao.append(novo)
        return True

    @staticmethod
    def _eh_celular(telefone: str) -> bool:
        """Um número brasileiro é celular quando tem 11 dígitos e o 3º é 9."""
        digitos = "".join(c for c in telefone if c.isdigit())
        if digitos.startswith("55") and len(digitos) > 11:
            digitos = digitos[2:]
        return len(digitos) == 11 and digitos[2] == "9"


# ===========================================================================
# Candidato de correspondência (usado na desambiguação de homônimos)
# ===========================================================================

@dataclass
class Candidato:
    """
    Uma empresa encontrada em alguma fonte, avaliada contra a empresa buscada.

    Attributes:
        nome: Nome da empresa conforme a fonte.
        url: URL do registro.
        fonte: Fonte de origem.
        cidade: Cidade informada pela fonte, se houver.
        uf: UF informada pela fonte, se houver.
        cnpj: CNPJ informado pela fonte, se houver.
        score_nome: Similaridade calculada (0..1) contra a razão social buscada.
        cidade_confere: ``True`` se a cidade bate, ``False`` se diverge,
            ``None`` se a fonte não informou cidade.
        texto: Texto bruto associado ao candidato (snippet ou página).
    """

    nome: str
    url: str
    fonte: Fonte
    cidade: str = ""
    uf: str = ""
    cnpj: str = ""
    score_nome: float = 0.0
    cidade_confere: Optional[bool] = None
    texto: str = ""

    @property
    def aceito(self) -> bool:
        """
        Candidato aprovado sem ressalvas.

        Exige similaridade de nome acima do limiar de aceite e que a cidade,
        quando conhecida, não divirja.
        """
        if self.cidade_confere is False:
            return False
        return self.score_nome >= config.SIMILARIDADE_MINIMA_ACEITE

    @property
    def duvidoso(self) -> bool:
        """Candidato plausível, mas insuficiente para preenchimento automático."""
        if self.aceito:
            return False
        return self.score_nome >= config.SIMILARIDADE_MINIMA_DUVIDA

    @property
    def descartado(self) -> bool:
        """Candidato claramente de outra empresa."""
        return not self.aceito and not self.duvidoso

    def __str__(self) -> str:
        local = f" [{self.cidade}/{self.uf}]" if self.cidade else ""
        return f"{self.nome}{local} score={self.score_nome:.2f} <{self.url}>"


def melhor_candidato(candidatos: Iterable[Candidato]) -> Optional[Candidato]:
    """Retorna o candidato de maior score entre os informados."""
    lista = list(candidatos)
    if not lista:
        return None
    return max(lista, key=lambda c: (c.aceito, c.score_nome))
