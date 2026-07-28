# -*- coding: utf-8 -*-
"""
config.py
=========

Configuração central da aplicação **Localizador de Contatos Empresariais**.

Todo valor ajustável do sistema vive aqui: caminhos, limites de rede, listas de
domínios classificados por confiabilidade, cores das linhas da planilha e
parâmetros dos algoritmos de correspondência de nomes.

Nada neste módulo executa I/O de rede ou depende de bibliotecas de terceiros —
ele pode ser importado por qualquer camada sem efeitos colaterais.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Identificação da aplicação
# ---------------------------------------------------------------------------

APP_NOME = "Localizador de Contatos Empresariais"
APP_VERSAO = "1.0.0"

# Diretório onde este arquivo está — usado como raiz do projeto.
RAIZ_PROJETO = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Nomes de arquivos gerados
# ---------------------------------------------------------------------------

ARQ_SAIDA_PLANILHA = "Empresas_Preenchidas.xlsx"
ARQ_LOG = "log.txt"
ARQ_LOG_TECNICO = "log_tecnico.txt"
ARQ_RELATORIO_SEM_CONTATO = "Relatorio_Sem_Contato.xlsx"
ARQ_RELATORIO_CSV = "Relatorio_Sem_Contato.csv"
ARQ_CHECKPOINT = "checkpoint.json"

# ---------------------------------------------------------------------------
# Colunas esperadas na planilha
# ---------------------------------------------------------------------------

# Chave lógica -> lista de rótulos aceitos no cabeçalho (comparação sem acento,
# sem caixa e sem espaços duplicados). A primeira entrada é o rótulo canônico
# usado quando a coluna precisa ser criada.
COLUNAS_PLANILHA: Dict[str, List[str]] = {
    "razao_social": ["Razão Social", "Razao Social", "Empresa", "Cliente", "Nome"],
    "contato": ["Contato", "Contatos", "Telefone", "Telefones"],
    "cidade": ["Cidade", "Município", "Municipio"],
    "regiao": ["Região", "Regiao"],
    "confianca": ["Confiança", "Confianca"],
    "observacao": ["Observação", "Observacao", "Obs"],
}

# Colunas que a aplicação cria automaticamente caso não existam.
COLUNAS_CRIADAS_AUTOMATICAMENTE = ["confianca", "observacao"]

# ---------------------------------------------------------------------------
# Cores das linhas (ARGB — formato aceito pelo openpyxl)
# ---------------------------------------------------------------------------

COR_VERDE_CLARO = "FFC6EFCE"   # telefone encontrado
COR_AMARELO = "FFFFEB9C"       # apenas e-mail encontrado
COR_VERMELHO = "FFFFC7CE"      # nenhum contato encontrado
COR_LARANJA = "FFFFD9A0"       # dúvida sobre a empresa -> revisão manual
COR_CINZA = "FFE7E6E6"         # ignorada (já possuía contato válido)

# ---------------------------------------------------------------------------
# Rede
# ---------------------------------------------------------------------------

TIMEOUT_HTTP = 15               # segundos por requisição HTTP
TIMEOUT_PLAYWRIGHT = 30_000     # milissegundos (Playwright trabalha em ms)
MAX_TENTATIVAS_HTTP = 3
BACKOFF_BASE = 1.8              # fator exponencial entre tentativas

# Intervalo aleatório (segundos) aplicado antes de cada requisição externa.
# Evita padrão de tráfego robótico e reduz risco de bloqueio.
DELAY_MIN = 1.2
DELAY_MAX = 3.5

# Delay extra aplicado especificamente a buscadores (mais sensíveis).
DELAY_BUSCADOR_MIN = 2.5
DELAY_BUSCADOR_MAX = 6.0

# Tamanho máximo de corpo HTTP lido (bytes). Evita baixar PDFs/vídeos enormes.
MAX_BYTES_RESPOSTA = 3_000_000

# ---------------------------------------------------------------------------
# Concorrência
# ---------------------------------------------------------------------------

MAX_WORKERS = 5                 # até 5 empresas simultâneas (requisito)
MAX_WORKERS_LIMITE = 8          # teto de segurança para o ajuste pela interface

# ---------------------------------------------------------------------------
# Escopo do rastreamento de sites oficiais
# ---------------------------------------------------------------------------

MAX_PAGINAS_SITE = 6            # páginas internas visitadas por site oficial
MAX_RESULTADOS_BUSCA = 8        # resultados lidos por consulta em buscador
MAX_CANDIDATOS_CNPJ = 4         # CNPJs candidatos validados por empresa

# Palavras que identificam páginas de contato dentro de um site.
PALAVRAS_PAGINA_CONTATO = [
    "contato", "contatos", "fale-conosco", "fale_conosco", "faleconosco",
    "fale-com-a-gente", "atendimento", "sac", "quem-somos", "quemsomos",
    "sobre", "sobre-nos", "sobrenos", "a-empresa", "aempresa", "nossa-empresa",
    "empresa", "institucional", "localizacao", "onde-estamos", "unidades",
    "filiais", "orcamento", "suporte", "ouvidoria",
]

# ---------------------------------------------------------------------------
# Classificação de domínios por confiabilidade
# ---------------------------------------------------------------------------

# Fontes oficiais / verificadas -> confiança ALTA.
DOMINIOS_OFICIAIS = [
    "receita.fazenda.gov.br",
    "servicos.receita.fazenda.gov.br",
    "brasilapi.com.br",
    "minhareceita.org",
    "publica.cnpj.ws",
    "google.com/maps",
    "maps.google.com",
]

# Diretórios empresariais reconhecidos -> confiança MÉDIA.
DOMINIOS_DIRETORIOS_CONFIAVEIS = [
    "linkedin.com",
    "econodata.com.br",
    "cnpj.biz",
    "casadosdados.com.br",
    "consultacnpj.com",
    "cnpja.com",
    "empresascnpj.com",
    "informecadastral.com.br",
    "gov.br",
    "sebrae.com.br",
    "jucesp.sp.gov.br",
    "guiadeempresas.com.br",
]

# Catálogos comerciais e agregadores -> confiança BAIXA.
DOMINIOS_CATALOGOS = [
    "telelistas.net",
    "apontador.com.br",
    "guiamais.com.br",
    "solutudo.com.br",
    "hotfrog.com.br",
    "encontraempresa.com.br",
    "kwikwi.com",
    "bing.com",
    "yelp.com",
    "foursquare.com",
    "waze.com",
    "listaempresas.com",
    "cylex.com.br",
    "opendi.com.br",
    "guialocal.com.br",
]

# Domínios que NUNCA podem ser considerados "site oficial" da empresa.
DOMINIOS_NAO_OFICIAIS = set(
    DOMINIOS_DIRETORIOS_CONFIAVEIS
    + DOMINIOS_CATALOGOS
    + [
        "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
        "tiktok.com", "wikipedia.org", "google.com", "googleusercontent.com",
        "mercadolivre.com.br", "olx.com.br", "indeed.com", "glassdoor.com",
        "catho.com.br", "vagas.com.br", "trabalhabrasil.com.br", "infojobs.com.br",
        "jusbrasil.com.br", "reclameaqui.com.br", "consumidor.gov.br",
        "wa.me", "api.whatsapp.com", "whatsapp.com", "t.me", "linktr.ee",
        "blogspot.com", "wordpress.com", "medium.com", "issuu.com",
        "amazon.com", "amazon.com.br", "shopee.com.br", "americanas.com.br",
    ]
)

# ---------------------------------------------------------------------------
# Validação de telefones
# ---------------------------------------------------------------------------

# DDDs válidos no Brasil (Anatel).
DDDS_VALIDOS = {
    11, 12, 13, 14, 15, 16, 17, 18, 19,
    21, 22, 24, 27, 28,
    31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49,
    51, 53, 54, 55,
    61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79,
    81, 82, 83, 84, 85, 86, 87, 88, 89,
    91, 92, 93, 94, 95, 96, 97, 98, 99,
}

# DDDs por UF — usado apenas como sinal de coerência (não como filtro rígido).
DDDS_POR_UF: Dict[str, set] = {
    "SP": {11, 12, 13, 14, 15, 16, 17, 18, 19},
    "RJ": {21, 22, 24},
    "ES": {27, 28},
    "MG": {31, 32, 33, 34, 35, 37, 38},
    "PR": {41, 42, 43, 44, 45, 46},
    "SC": {47, 48, 49},
    "RS": {51, 53, 54, 55},
    "DF": {61},
    "GO": {62, 64},
    "TO": {63},
    "MT": {65, 66},
    "MS": {67},
    "AC": {68},
    "RO": {69},
    "BA": {71, 73, 74, 75, 77},
    "SE": {79},
    "PE": {81, 87},
    "AL": {82},
    "PB": {83},
    "RN": {84},
    "CE": {85, 88},
    "PI": {86, 89},
    "PA": {91, 93, 94},
    "AM": {92, 97},
    "RR": {95},
    "AP": {96},
    "MA": {98, 99},
}

# UF padrão da carteira de clientes (região de Campinas).
UF_PADRAO = "SP"

# Telefones notoriamente genéricos que aparecem em rodapés de terceiros.
TELEFONES_BLOQUEADOS = {
    "0000000000", "00000000000", "1111111111", "11111111111",
    "1234567890", "12345678901", "9999999999", "99999999999",
    "1130039000",   # exemplo comum em templates
    "1140028922",   # placeholder recorrente
}

# Quando um número aparece sem DDD logo após um número que possui DDD,
# assume-se o mesmo DDD (convenção tipográfica brasileira). Distância máxima
# em caracteres entre os dois números para que a herança seja aplicada.
HERDAR_DDD = True
HERDAR_DDD_DISTANCIA_MAX = 40

# ---------------------------------------------------------------------------
# Validação de e-mails
# ---------------------------------------------------------------------------

# Domínios de e-mail que nunca pertencem à empresa pesquisada.
DOMINIOS_EMAIL_BLOQUEADOS = {
    "example.com", "example.org", "email.com", "dominio.com.br", "seudominio.com.br",
    "sentry.io", "wixpress.com", "wix.com", "godaddy.com", "squarespace.com",
    "schema.org", "w3.org", "sentry-next.wixpress.com", "jquery.com",
    "googleapis.com", "gstatic.com", "cloudflare.com", "gravatar.com",
    "wordpress.org", "elementor.com", "adobe.com", "font-awesome.com",
    "seusite.com.br", "empresa.com.br", "teste.com", "test.com",
}

# Prefixos de e-mail tipicamente de agências/webmasters de terceiros.
PREFIXOS_EMAIL_SUSPEITOS = {
    "wordpress", "no-reply", "noreply", "postmaster", "abuse", "hostmaster",
    "webmaster@wix", "u003e",
}

# Extensões que indicam que o "e-mail" capturado é, na verdade, um arquivo.
EXTENSOES_INVALIDAS_EMAIL = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "json",
    "woff", "woff2", "ttf", "eot", "ico", "mp4", "webm", "pdf",
}

# Provedores gratuitos: um e-mail nesses domínios pode legitimamente pertencer
# à empresa. Em um site oficial, qualquer e-mail fora do domínio próprio e
# fora desta lista pertence a um terceiro (prefeitura, parceiro, agência) e é
# descartado — foi assim que um contato da prefeitura quase entrou na planilha.
PROVEDORES_EMAIL_GRATUITOS = {
    "gmail.com", "hotmail.com", "hotmail.com.br", "outlook.com", "outlook.com.br",
    "live.com", "msn.com", "yahoo.com", "yahoo.com.br", "uol.com.br", "bol.com.br",
    "terra.com.br", "ig.com.br", "globo.com", "globomail.com", "r7.com",
    "oi.com.br", "zipmail.com.br", "superig.com.br", "icloud.com", "me.com",
    "protonmail.com", "proton.me", "aol.com", "gmail.com.br",
}

# ---------------------------------------------------------------------------
# Correspondência de nomes de empresas
# ---------------------------------------------------------------------------

# Sufixos e formas societárias removidos antes de comparar nomes.
SUFIXOS_SOCIETARIOS = [
    "sociedade anonima", "sociedade anônima", "sociedade limitada",
    "eireli", "eirelli", "ltda", "limitada", "s/a", "s.a.", "s a", "sa",
    "epp", "me", "mei", "cia", "companhia", "filial", "matriz",
    "microempresa", "empresa individual",
]

# Palavras genéricas — presentes em milhares de razões sociais brasileiras.
# Não identificam a empresa e por isso são excluídas dos "tokens distintivos".
#
# A lista inclui deliberadamente adjetivos comuns de negócio ("executiva",
# "express", "nacional"). Sem isso, "A & A EXECUTIVA TRANSPORTES" reduz-se ao
# token "executiva" e casa com qualquer site que use essa palavra no domínio —
# falso positivo real observado em teste de campo.
PALAVRAS_GENERICAS = {
    # ramo de atividade
    "comercio", "comercial", "industria", "industrial", "servicos", "servico",
    "transportes", "transporte", "transportadora", "logistica", "logistico",
    "distribuidora", "distribuidor", "representacoes", "representacao",
    "empreendimentos", "empreendimento", "imobiliarios", "imobiliaria",
    "participacoes", "holding", "grupo", "materiais", "material", "produtos",
    "equipamentos", "solucoes", "sistemas", "tecnologia", "construtora",
    "construcoes", "construcao", "engenharia", "consultoria", "assessoria",
    "manutencao", "locacao", "locadora", "terraplenagem", "alimentos",
    "confeccoes", "metalurgica", "mecanica", "eletrica", "hidraulica",
    "agropecuaria", "agricola", "veiculos", "automoveis", "pecas", "acessorios",
    "buffet", "eventos", "turismo", "viacao", "academia", "ginastica",
    # adjetivos e qualificadores comuns
    "executiva", "executivo", "express", "expresso", "rapido", "rapida",
    "nacional", "internacional", "central", "geral", "gerais", "moderna",
    "moderno", "nova", "novo", "uniao", "real", "prime", "premium", "master",
    "global", "total", "integral", "alianca", "brasil", "brasileira",
    "brasileiro", "paulista", "regional", "universal", "multi", "mega",
    "super", "top", "line", "center", "centro", "casa", "loja", "ponto",
    # conectivos e artigos
    "e", "de", "da", "do", "das", "dos", "em", "para", "com", "a", "o",
    "ltda", "me", "epp", "sa", "cia",
}

# Limiares do comparador de nomes.
SIMILARIDADE_MINIMA_ACEITE = 0.82   # >= aceita a correspondência
SIMILARIDADE_MINIMA_DUVIDA = 0.62   # entre dúvida e aceite -> revisão manual
# Abaixo de SIMILARIDADE_MINIMA_DUVIDA o candidato é simplesmente descartado.

# Modo paranoico: razões sociais com um único token distintivo (ex.: "A.R.
# MARSON MATERIAIS" -> "marson") só são aceitas mediante confirmação por CNPJ.
#
# Desligado por padrão porque boa parte das carteiras é formada por
# "SOBRENOME + ramo genérico", e exigir CNPJ nesses casos derrubaria muito a
# cobertura. Com a lista de PALAVRAS_GENERICAS acima, o token único remanescente
# costuma ser um nome próprio — razoavelmente seguro quando confirmado pelo
# domínio. Ligue se preferir precisão máxima sobre cobertura.
EXIGIR_CNPJ_PARA_TOKEN_UNICO = False

# Quando a razão social tem um único token distintivo, ele precisa aparecer no
# domínio do site (não basta estar no texto da página).
TOKEN_UNICO_EXIGE_DOMINIO = True

# Mínimo de tokens distintivos confirmados quando a razão social tem dois ou
# mais — evita aceitar um site por coincidência de uma só palavra.
MIN_TOKENS_CONFIRMACAO_SITE = 2

# ---------------------------------------------------------------------------
# Política de preenchimento
# ---------------------------------------------------------------------------

# Somente confiança ALTA ou MÉDIA são gravadas automaticamente na planilha.
CONFIANCAS_PREENCHIVEIS = {"Alta", "Média"}

TEXTO_CONFERENCIA_MANUAL = "Necessita conferência manual."
TEXTO_REVISAO_MANUAL = "Revisão Manual Necessária"
TEXTO_NADA_ENCONTRADO = "Nenhuma informação confiável encontrada."

# Interrompe a busca assim que um telefone de confiança ALTA for confirmado.
PARAR_NA_PRIMEIRA_ALTA = True

# Número máximo de telefones/e-mails gravados por empresa.
MAX_TELEFONES_GRAVADOS = 4
MAX_EMAILS_GRAVADOS = 3

# ---------------------------------------------------------------------------
# Detecção de bloqueio / captcha
# ---------------------------------------------------------------------------

MARCADORES_CAPTCHA = [
    "unusual traffic", "tráfego incomum", "trafego incomum",
    "detectamos tráfego", "our systems have detected",
    "/sorry/index", "recaptcha", "g-recaptcha", "captcha-delivery",
    "verifique que você não é um robô", "não é um robô",
    "are you a robot", "verify you are human", "cf-challenge",
    "please complete the security check", "access denied",
]

# Ao detectar captcha, quanto tempo (segundos) o motor aguarda antes de
# permitir retomada automática, caso o usuário não intervenha.
PAUSA_APOS_CAPTCHA = 90

# ---------------------------------------------------------------------------
# User-Agents de fallback (usados se `fake-useragent` não estiver disponível)
# ---------------------------------------------------------------------------

USER_AGENTS_FALLBACK = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# ---------------------------------------------------------------------------
# Endpoints públicos de consulta de CNPJ
# ---------------------------------------------------------------------------

# Consultados em ordem; o primeiro que responder com sucesso é utilizado.
ENDPOINTS_CNPJ = [
    "https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
    "https://minhareceita.org/{cnpj}",
    "https://publica.cnpj.ws/cnpj/{cnpj}",
]

# O cadastro da Receita traz também o fax. Um fax na coluna "Contato" é dado
# inútil para quem vai ligar, por isso fica de fora por padrão.
INCLUIR_FAX_DO_CADASTRO = False

# ---------------------------------------------------------------------------
# Buscadores
# ---------------------------------------------------------------------------

# Ordem de tentativa. O Google é mantido por exigência do escopo, mas é o mais
# suscetível a bloqueio — por isso fica após alternativas estáveis.
ORDEM_BUSCADORES = ["duckduckgo", "bing", "google"]

URL_DUCKDUCKGO = "https://html.duckduckgo.com/html/"
# Endpoint "lite": marcação mínima e limite de taxa mais brando. Usado como
# reserva quando o endpoint HTML recusa a requisição.
URL_DUCKDUCKGO_LITE = "https://lite.duckduckgo.com/lite/"
URL_BING = "https://www.bing.com/search"
URL_GOOGLE = "https://www.google.com/search"
URL_GOOGLE_MAPS_BUSCA = "https://www.google.com/maps/search/{query}?hl=pt-BR"

# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

TEMA_CUSTOMTKINTER = "dark"        # "dark", "light" ou "system"
TEMA_COR_PADRAO = "blue"
JANELA_LARGURA = 1180
JANELA_ALTURA = 760
INTERVALO_ATUALIZACAO_UI_MS = 200   # frequência de drenagem da fila de logs
MAX_LINHAS_LOG_UI = 800             # linhas mantidas na caixa de log da UI


# ---------------------------------------------------------------------------
# Configuração mutável em tempo de execução
# ---------------------------------------------------------------------------

@dataclass
class Configuracao:
    """
    Parâmetros que o usuário pode ajustar sem editar código.

    Os valores default espelham as constantes do módulo. A interface gráfica
    instancia um objeto destes e o repassa para todas as camadas, de modo que
    nenhum componente leia constantes globais diretamente — facilita testes e
    execuções com perfis diferentes.
    """

    caminho_planilha: str = ""
    pasta_saida: str = ""

    max_workers: int = MAX_WORKERS
    delay_min: float = DELAY_MIN
    delay_max: float = DELAY_MAX
    timeout_http: int = TIMEOUT_HTTP

    usar_playwright: bool = True
    usar_google_maps: bool = True
    usar_google_search: bool = True
    usar_cnpj: bool = True
    usar_site_oficial: bool = True

    headless: bool = True
    parar_na_primeira_alta: bool = PARAR_NA_PRIMEIRA_ALTA
    max_paginas_site: int = MAX_PAGINAS_SITE
    uf_padrao: str = UF_PADRAO

    # Lista de fontes já classificadas — mantida para futura expansão.
    fontes_desabilitadas: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Utilitários
    # ------------------------------------------------------------------

    def caminho_saida(self, nome_arquivo: str) -> Path:
        """Retorna o caminho completo de um arquivo dentro da pasta de saída."""
        base = Path(self.pasta_saida) if self.pasta_saida else RAIZ_PROJETO / "saida"
        base.mkdir(parents=True, exist_ok=True)
        return base / nome_arquivo

    def validar(self) -> List[str]:
        """
        Valida a configuração e devolve a lista de problemas encontrados.

        Uma lista vazia significa configuração utilizável.
        """
        problemas: List[str] = []

        if not self.caminho_planilha:
            problemas.append("Nenhuma planilha selecionada.")
        elif not os.path.isfile(self.caminho_planilha):
            problemas.append(f"Planilha não encontrada: {self.caminho_planilha}")
        elif not self.caminho_planilha.lower().endswith((".xlsx", ".xlsm")):
            problemas.append("A planilha precisa estar no formato .xlsx ou .xlsm.")

        if not 1 <= self.max_workers <= MAX_WORKERS_LIMITE:
            problemas.append(
                f"Número de threads deve estar entre 1 e {MAX_WORKERS_LIMITE}."
            )

        if self.delay_min < 0 or self.delay_max < self.delay_min:
            problemas.append("Intervalo de delay inválido (min deve ser <= max).")

        return problemas

    # ------------------------------------------------------------------
    # Persistência
    # ------------------------------------------------------------------

    def salvar(self, caminho: Path | str | None = None) -> Path:
        """Grava a configuração em JSON para reutilização entre execuções."""
        destino = Path(caminho) if caminho else RAIZ_PROJETO / "config_usuario.json"
        destino.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return destino

    @classmethod
    def carregar(cls, caminho: Path | str | None = None) -> "Configuracao":
        """
        Carrega a configuração salva anteriormente.

        Chaves desconhecidas são ignoradas, o que permite evoluir o dataclass
        sem quebrar arquivos gerados por versões antigas.
        """
        origem = Path(caminho) if caminho else RAIZ_PROJETO / "config_usuario.json"
        if not origem.is_file():
            return cls()

        try:
            dados: Dict[str, Any] = json.loads(origem.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()

        validos = {c.name for c in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in dados.items() if k in validos})
