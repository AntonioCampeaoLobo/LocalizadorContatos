# -*- coding: utf-8 -*-
"""
pesquisa.py
===========

Orquestração da pesquisa.

Contém três peças:

* :class:`RegistradorLog` — escreve ``log.txt`` no formato exigido pelo escopo
  (um bloco por empresa, com fonte, URL, tempo e status);
* :class:`PesquisadorEmpresa` — executa, para **uma** empresa, a cascata de
  fontes na ordem de prioridade definida, validando cada correspondência;
* :class:`MotorPesquisa` — coordena o processamento de milhares de empresas com
  ``ThreadPoolExecutor``, pausa/continuação/cancelamento, salvamento automático
  após cada empresa e tratamento de captcha.

Cascata de fontes executada por empresa:

    1. Pesquisa inteligente: CNPJ e nome fantasia (Receita Federal)
    2. Site oficial (descoberto por busca, validado e rastreado)
    3. Google Business Profile (Maps)
    4. Snippets de busca e diretórios/catálogos

A cascata para assim que um telefone de confiança **Alta** é confirmado — mais
fontes não melhorariam o dado e só aumentariam o risco de bloqueio.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cnpj as mod_cnpj
import config
import excel
import maps as mod_maps
import utils
from modelos import (
    Confianca,
    Empresa,
    Evidencia,
    Fonte,
    ResultadoPesquisa,
    StatusPesquisa,
)

logger = logging.getLogger("localizador.pesquisa")


# ===========================================================================
# Importação dos módulos com nome colidente
# ===========================================================================

def _importar_local(nome_arquivo: str, alias: str):
    """
    Importa um módulo do diretório do projeto pelo caminho absoluto.

    Dois arquivos exigidos pelo escopo têm nomes que colidem com módulos já
    conhecidos do Python:

    * ``site.py`` colide com o módulo ``site`` da **biblioteca padrão**, que o
      interpretador carrega durante a inicialização — um ``import site`` comum
      devolveria o módulo da stdlib, e não o do projeto;
    * ``google.py`` colide com o *namespace package* ``google`` usado por
      bibliotecas como ``protobuf`` e ``google-cloud-*``.

    Carregar por caminho absoluto e registrar sob um alias exclusivo resolve os
    dois casos de forma definitiva, sem renomear os arquivos e sem quebrar
    nenhuma biblioteca de terceiros.

    Args:
        nome_arquivo: Nome do arquivo dentro da pasta do projeto (ex.: "site.py").
        alias: Chave usada em ``sys.modules`` (ex.: "localizador_site").

    Returns:
        O módulo carregado.
    """
    import importlib.util
    import sys

    if alias in sys.modules:
        return sys.modules[alias]

    caminho = Path(__file__).resolve().parent / nome_arquivo
    especificacao = importlib.util.spec_from_file_location(alias, caminho)
    if especificacao is None or especificacao.loader is None:
        raise ImportError(f"Não foi possível carregar {caminho}")

    modulo = importlib.util.module_from_spec(especificacao)
    sys.modules[alias] = modulo
    especificacao.loader.exec_module(modulo)
    return modulo


mod_site = _importar_local("site.py", "localizador_site")
mod_google = _importar_local("google.py", "localizador_google")


# ===========================================================================
# Registro em log.txt
# ===========================================================================

class RegistradorLog:
    """
    Escreve o arquivo ``log.txt`` no formato solicitado.

    Cada empresa gera um bloco legível contendo empresa, cidade, telefone,
    e-mail, site, fonte utilizada, URL, tempo de pesquisa e status. A escrita é
    serializada por lock — várias threads registram no mesmo arquivo.
    """

    SEPARADOR = "=" * 78

    def __init__(self, caminho: Path) -> None:
        self.caminho = Path(caminho)
        self._lock = threading.Lock()
        self.caminho.parent.mkdir(parents=True, exist_ok=True)

    def cabecalho(self, total: int, planilha: str) -> None:
        """Escreve o cabeçalho da execução."""
        agora = time.strftime("%d/%m/%Y %H:%M:%S")
        texto = (
            f"\n{self.SEPARADOR}\n"
            f"{config.APP_NOME} v{config.APP_VERSAO}\n"
            f"Início: {agora}\n"
            f"Planilha: {planilha}\n"
            f"Empresas a pesquisar: {total}\n"
            f"{self.SEPARADOR}\n\n"
        )
        self._escrever(texto)

    def registrar(self, res: ResultadoPesquisa) -> None:
        """Escreve o bloco de uma empresa."""
        emp = res.empresa
        fonte = res.fonte_principal

        telefones = "\n".join(t.valor for t in res.telefones) or "(nenhum)"
        emails = "\n".join(e.valor for e in res.emails) or "(nenhum)"
        whats = "\n".join(w.valor for w in res.whatsapps) or "(nenhum)"

        linhas = [
            self.SEPARADOR,
            f"Empresa:\n{emp.razao_social}",
            f"\nCidade:\n{emp.cidade or '(não informada)'}",
        ]
        if emp.cnpj:
            linhas.append(f"\nCNPJ:\n{utils.formatar_cnpj(emp.cnpj)}")
        if emp.nome_fantasia:
            linhas.append(f"\nNome Fantasia:\n{emp.nome_fantasia}")

        linhas += [
            f"\nTelefone encontrado:\n{telefones}",
            f"\nEmail encontrado:\n{emails}",
        ]
        if res.whatsapps:
            linhas.append(f"\nWhatsApp:\n{whats}")

        linhas += [
            f"\nSite:\n{res.site.valor if res.site else '(nenhum)'}",
            f"\nFonte utilizada:\n{fonte.value if fonte else '(nenhuma)'}",
            f"\nURL utilizada:\n{res.url_principal or '(nenhuma)'}",
            f"\nConfiança:\n{res.confianca.value if res.tem_algum_dado else '(sem dados)'}",
            f"\nTempo da pesquisa:\n{utils.formatar_duracao(res.duracao)}",
            f"\nStatus:\n{res.status.value}",
        ]

        if res.observacao:
            linhas.append(f"\nObservação:\n{res.observacao}")
        if res.erro:
            linhas.append(f"\nErro:\n{res.erro}")
        if res.endereco:
            linhas.append(f"\nEndereço:\n{res.endereco}")
        if res.fontes_consultadas:
            linhas.append("\nFontes consultadas:\n" + "\n".join(res.fontes_consultadas))

        # Detalhamento por dado: cada contato com sua origem exata.
        detalhes = []
        for dado in res.telefones + res.emails:
            detalhes.append(
                f"  - {dado.valor}  [{dado.confianca.value}]  "
                f"{dado.evidencia.fonte.value}  <{dado.evidencia.url}>"
            )
        if detalhes:
            linhas.append("\nOrigem de cada dado:\n" + "\n".join(detalhes))

        linhas.append(f"{self.SEPARADOR}\n\n")
        self._escrever("\n".join(linhas))

    def rodape(self, estatisticas: Dict[str, int], duracao: float) -> None:
        """Escreve o resumo final da execução."""
        linhas = [
            self.SEPARADOR,
            "RESUMO DA EXECUÇÃO",
            f"Duração total: {utils.formatar_duracao(duracao)}",
        ]
        linhas += [f"{chave}: {valor}" for chave, valor in estatisticas.items()]
        linhas.append(self.SEPARADOR + "\n\n")
        self._escrever("\n".join(linhas))

    def anotar(self, mensagem: str) -> None:
        """Registra uma linha avulsa (pausa, captcha, cancelamento)."""
        agora = time.strftime("%H:%M:%S")
        self._escrever(f"[{agora}] {mensagem}\n")

    def _escrever(self, texto: str) -> None:
        with self._lock:
            try:
                with open(self.caminho, "a", encoding="utf-8") as arquivo:
                    arquivo.write(texto)
            except OSError as exc:  # pragma: no cover
                logger.error("Não foi possível escrever em %s: %s", self.caminho, exc)


# ===========================================================================
# Recursos por thread
# ===========================================================================

class _RecursosThread(threading.local):
    """
    Recursos de rede criados sob demanda, um conjunto por thread.

    ``requests.Session`` e o Playwright não são thread-safe; herdar de
    ``threading.local`` garante isolamento automático entre os workers.
    """

    def __init__(self) -> None:
        self.cliente: Optional[utils.ClienteHTTP] = None
        self.buscador: Optional[mod_google.Buscador] = None
        self.consultor: Optional[mod_cnpj.ConsultorCNPJ] = None
        self.localizador: Optional[mod_cnpj.LocalizadorCNPJ] = None
        self.rastreador_site: Optional[mod_site.RastreadorSite] = None
        self.rastreador_maps: Optional[mod_maps.RastreadorMaps] = None


# ===========================================================================
# Pesquisador de uma empresa
# ===========================================================================

class PesquisadorEmpresa:
    """
    Executa a cascata de fontes para uma empresa.

    A instância é compartilhada entre threads; o estado mutável de rede fica em
    :class:`_RecursosThread`, isolado por thread.
    """

    def __init__(
        self,
        cfg: config.Configuracao,
        limitador: Optional[utils.Limitador] = None,
        rotacionador: Optional[utils.RotacionadorUserAgent] = None,
    ) -> None:
        self.cfg = cfg
        self.limitador = limitador or utils.Limitador(cfg.delay_min, cfg.delay_max)
        self.rotacionador = rotacionador or utils.RotacionadorUserAgent()
        self._recursos = _RecursosThread()
        self.log = logger

    # ------------------------------------------------------------------
    # Recursos
    # ------------------------------------------------------------------

    def _r(self) -> _RecursosThread:
        """Garante que os recursos da thread atual estejam inicializados."""
        r = self._recursos
        if r.cliente is None:
            r.cliente = utils.ClienteHTTP(
                limitador=self.limitador,
                rotacionador=self.rotacionador,
                timeout=self.cfg.timeout_http,
            )
            r.buscador = mod_google.Buscador(r.cliente, self.cfg)
            r.consultor = mod_cnpj.ConsultorCNPJ(r.cliente)
            r.localizador = mod_cnpj.LocalizadorCNPJ(r.cliente, r.buscador, r.consultor)
            r.rastreador_site = mod_site.RastreadorSite(r.cliente, self.cfg)
            r.rastreador_maps = mod_maps.RastreadorMaps(self.cfg)
        return r

    def liberar(self) -> None:
        """Fecha a sessão HTTP da thread atual."""
        if self._recursos.cliente is not None:
            self._recursos.cliente.fechar()
            self._recursos.cliente = None

    # ------------------------------------------------------------------
    # Pesquisa
    # ------------------------------------------------------------------

    def pesquisar(self, empresa: Empresa) -> ResultadoPesquisa:
        """
        Pesquisa uma empresa percorrendo as fontes em ordem de prioridade.

        Exceções de rede são capturadas e convertidas em status ``ERRO`` — uma
        empresa problemática nunca derruba o processamento das demais.
        :class:`utils.CaptchaDetectado` é propagada, pois exige pausa global.
        """
        resultado = ResultadoPesquisa(empresa=empresa)
        cronometro = utils.Cronometro()
        ambiguidades: List[str] = []

        with cronometro:
            try:
                self._executar_cascata(empresa, resultado, ambiguidades)
            except utils.CaptchaDetectado:
                resultado.duracao = cronometro.decorrido
                raise
            except utils.OperacaoCancelada:
                resultado.status = StatusPesquisa.CANCELADO
                resultado.duracao = cronometro.decorrido
                return resultado
            except Exception as exc:
                self.log.exception("Erro ao pesquisar %s", empresa.identificador)
                resultado.status = StatusPesquisa.ERRO
                resultado.erro = f"{type(exc).__name__}: {exc}"
                resultado.duracao = cronometro.decorrido
                return resultado

        resultado.duracao = cronometro.duracao
        self._decidir_status(resultado, ambiguidades)
        return resultado

    # ------------------------------------------------------------------

    def _executar_cascata(
        self, empresa: Empresa, resultado: ResultadoPesquisa, ambiguidades: List[str]
    ) -> None:
        """Percorre as fontes na ordem de prioridade, com parada antecipada."""
        r = self._r()
        resultados_busca: List[mod_google.ResultadoBusca] = []

        # --- Etapa 1: pesquisa inteligente (CNPJ / nome fantasia) ------
        if self.cfg.usar_cnpj:
            dados = self._etapa_cnpj(empresa, resultado)
            if dados is None and self.cfg.usar_cnpj:
                resultado.registrar_fonte("Receita Federal (CNPJ não confirmado)")

        if self._satisfeito(resultado):
            return

        # --- Busca web (compartilhada pelas etapas seguintes) ----------
        if self.cfg.usar_site_oficial or self.cfg.usar_google_search:
            try:
                resultados_busca = r.buscador.buscar_varias(empresa.consultas()[:2])
            except utils.CaptchaDetectado:
                raise
            except Exception as exc:
                self.log.debug("Busca falhou para %s: %s", empresa.identificador, exc)

        # --- Etapa 2: site oficial (prioridade máxima) -----------------
        if self.cfg.usar_site_oficial:
            self._etapa_site_oficial(empresa, resultado, resultados_busca)

        if self._satisfeito(resultado):
            return

        # --- Etapa 3: Google Business Profile --------------------------
        if self.cfg.usar_google_maps:
            self._etapa_maps(empresa, resultado, ambiguidades)

        if self._satisfeito(resultado):
            return

        # --- Etapa 4: snippets, diretórios e catálogos -----------------
        if self.cfg.usar_google_search and resultados_busca:
            r.buscador.coletar_de_resultados(empresa, resultado, resultados_busca)

    # ------------------------------------------------------------------
    # Etapas
    # ------------------------------------------------------------------

    def _etapa_cnpj(
        self, empresa: Empresa, resultado: ResultadoPesquisa
    ) -> Optional[mod_cnpj.DadosCNPJ]:
        """Localiza e confirma o CNPJ, aproveitando os contatos do cadastro."""
        r = self._r()
        try:
            dados = r.localizador.localizar(empresa)
        except utils.CaptchaDetectado:
            raise
        except Exception as exc:
            self.log.debug("Etapa CNPJ falhou para %s: %s", empresa.identificador, exc)
            return None

        if not dados:
            return None

        empresa.cnpj = dados.cnpj
        empresa.nome_fantasia = dados.nome_fantasia
        if dados.uf:
            empresa.uf = dados.uf

        registrados = mod_cnpj.aplicar_dados_cnpj(resultado, dados, empresa)
        self.log.info(
            "%s — CNPJ %s confirmado (%s/%s); %d contato(s) do cadastro.",
            empresa.razao_social, utils.formatar_cnpj(dados.cnpj),
            dados.municipio, dados.uf, registrados,
        )
        return dados

    def _etapa_site_oficial(
        self,
        empresa: Empresa,
        resultado: ResultadoPesquisa,
        resultados_busca: List[mod_google.ResultadoBusca],
    ) -> None:
        """Descobre e rastreia o site oficial da empresa."""
        r = self._r()

        candidato = r.buscador.descobrir_site_oficial(empresa, resultados_busca)
        url = candidato.url if candidato else (resultado.site.valor if resultado.site else "")
        if not url:
            return

        try:
            info = r.rastreador_site.rastrear(empresa, url, resultado)
        except utils.CaptchaDetectado:
            raise
        except Exception as exc:
            self.log.debug("Rastreio de %s falhou: %s", url, exc)
            return

        if info.confirmado:
            self.log.info(
                "%s — site oficial %s confirmado (%d página(s), %d contato(s)).",
                empresa.razao_social, info.dominio, info.paginas, info.contatos_registrados,
            )
            if info.tem_formulario and not resultado.observacao:
                resultado.observacao = "Site possui formulário de contato."
        else:
            resultado.registrar_fonte(f"Site descartado ({utils.dominio_de(url)})")

    def _etapa_maps(
        self, empresa: Empresa, resultado: ResultadoPesquisa, ambiguidades: List[str]
    ) -> None:
        """Consulta o Google Business Profile."""
        r = self._r()
        if not r.rastreador_maps.disponivel:
            return

        try:
            info = r.rastreador_maps.pesquisar(empresa, resultado)
        except utils.CaptchaDetectado:
            raise
        except Exception as exc:
            self.log.debug("Maps falhou para %s: %s", empresa.identificador, exc)
            return

        if info.ambiguo:
            ambiguidades.append(info.motivo)
            return

        # Se o Maps revelou um site oficial ainda não rastreado, aproveita.
        if (
            self.cfg.usar_site_oficial
            and resultado.site
            and not any(
                f.startswith("Site Oficial") for f in resultado.fontes_consultadas
            )
        ):
            try:
                r.rastreador_site.rastrear(empresa, resultado.site.valor, resultado)
            except utils.CaptchaDetectado:
                raise
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Decisão
    # ------------------------------------------------------------------

    def _satisfeito(self, resultado: ResultadoPesquisa) -> bool:
        """
        Diz se já há dado suficiente para encerrar a cascata.

        Critério: um telefone de confiança Alta — o melhor que o sistema
        consegue produzir. Buscar mais só aumentaria risco de bloqueio.
        """
        if not self.cfg.parar_na_primeira_alta:
            return False
        return resultado.confianca_telefone == Confianca.ALTA

    def _decidir_status(
        self, resultado: ResultadoPesquisa, ambiguidades: List[str]
    ) -> None:
        """
        Define o status final e aplica a política de confiabilidade.

        Regras (todas do escopo):

        * ambiguidade não resolvida **e** sem confirmação por CNPJ ->
          "Revisão Manual Necessária" e **nenhum** contato é gravado;
        * telefone com confiança Alta/Média -> "Encontrado";
        * apenas e-mail Alta/Média -> "Apenas e-mail";
        * somente dados de confiança Baixa -> "Necessita conferência manual"
          (a planilha recebe apenas a observação, nunca o contato);
        * nada -> "Não encontrado".
        """
        confirmado_por_cnpj = bool(resultado.empresa.cnpj)

        if ambiguidades and not confirmado_por_cnpj:
            resultado.status = StatusPesquisa.REVISAO_MANUAL
            resultado.observacao = utils.truncar("; ".join(ambiguidades), 400)
            # Dúvida sobre a identidade -> descarta tudo que foi coletado.
            resultado.telefones.clear()
            resultado.emails.clear()
            resultado.whatsapps.clear()
            return

        if ambiguidades:
            resultado.observacao = utils.limpar_espacos(
                f"{resultado.observacao} Ambiguidade resolvida pelo CNPJ."
            )

        if resultado.tem_telefone_preenchivel:
            resultado.status = StatusPesquisa.ENCONTRADO
        elif resultado.tem_email_preenchivel:
            resultado.status = StatusPesquisa.APENAS_EMAIL
        elif resultado.tem_algum_dado:
            resultado.status = StatusPesquisa.CONFERENCIA_MANUAL
        else:
            resultado.status = StatusPesquisa.NAO_ENCONTRADO


# ===========================================================================
# Estado de progresso (consumido pela interface)
# ===========================================================================

@dataclass
class EstadoProgresso:
    """Fotografia do andamento, entregue à interface a cada atualização."""

    total: int = 0
    processadas: int = 0
    encontradas: int = 0
    sem_resultado: int = 0
    revisao_manual: int = 0
    erros: int = 0
    ignoradas: int = 0
    empresa_atual: str = ""
    tempo_decorrido: float = 0.0
    tempo_estimado: float = 0.0
    pausado: bool = False
    executando: bool = False

    @property
    def restantes(self) -> int:
        return max(0, self.total - self.processadas)

    @property
    def percentual(self) -> float:
        return (self.processadas / self.total) if self.total else 0.0

    def resumo(self) -> Dict[str, int]:
        """Dicionário usado no rodapé do ``log.txt``."""
        return {
            "Total processado": self.processadas,
            "Contatos encontrados": self.encontradas,
            "Sem resultado": self.sem_resultado,
            "Revisão manual": self.revisao_manual,
            "Erros": self.erros,
            "Ignoradas (já tinham contato)": self.ignoradas,
        }


# ===========================================================================
# Motor
# ===========================================================================

class MotorPesquisa:
    """
    Coordena a pesquisa de todas as empresas da planilha.

    Recursos:

    * ``ThreadPoolExecutor`` com até 5 workers (configurável);
    * pausa, continuação e cancelamento cooperativos;
    * salvamento automático da planilha **após cada empresa**;
    * pausa automática ao detectar captcha, com aviso ao usuário e retomada;
    * geração de ``log.txt`` e do relatório de empresas sem contato.

    A instância não é reutilizável entre execuções — crie uma por rodada.
    """

    def __init__(
        self,
        cfg: config.Configuracao,
        planilha: excel.PlanilhaContatos,
        registrador: RegistradorLog,
        ao_progredir: Optional[Callable[[EstadoProgresso], None]] = None,
        ao_logar: Optional[Callable[[str, str], None]] = None,
        ao_detectar_captcha: Optional[Callable[[str], None]] = None,
        ao_terminar: Optional[Callable[[EstadoProgresso, List[ResultadoPesquisa]], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.planilha = planilha
        self.registrador = registrador

        self.ao_progredir = ao_progredir or (lambda _e: None)
        self.ao_logar = ao_logar or (lambda _n, _m: None)
        self.ao_detectar_captcha = ao_detectar_captcha or (lambda _m: None)
        self.ao_terminar = ao_terminar or (lambda _e, _r: None)

        # Eventos de controle. `_rodando` é setado enquanto NÃO está pausado.
        self._rodando = threading.Event()
        self._rodando.set()
        self._cancelado = threading.Event()
        self._captcha_ativo = threading.Event()

        self._lock = threading.Lock()
        self.estado = EstadoProgresso()
        self.resultados: List[ResultadoPesquisa] = []
        self._duracoes: List[float] = []
        self._inicio = 0.0
        self._thread: Optional[threading.Thread] = None

        self.pesquisador = PesquisadorEmpresa(
            cfg,
            limitador=utils.Limitador(cfg.delay_min, cfg.delay_max),
            rotacionador=utils.RotacionadorUserAgent(),
        )

    # ==================================================================
    # Controle
    # ==================================================================

    def iniciar(self, empresas: List[Empresa], ignoradas: int = 0) -> None:
        """Inicia o processamento em uma thread de fundo."""
        if self._thread and self._thread.is_alive():
            raise RuntimeError("O motor já está em execução.")

        # Uma nova execução merece uma chance limpa em todas as fontes:
        # o bloqueio que derrubou a rodada anterior pode já ter expirado.
        utils.Disjuntor.reiniciar_todos()

        self.estado = EstadoProgresso(
            total=len(empresas), ignoradas=ignoradas, executando=True
        )
        self.resultados.clear()
        self._duracoes.clear()
        self._cancelado.clear()
        self._rodando.set()
        self._inicio = time.monotonic()

        self._thread = threading.Thread(
            target=self._executar, args=(empresas,), name="MotorPesquisa", daemon=True
        )
        self._thread.start()

    def pausar(self) -> None:
        """Suspende o consumo de novas empresas (as em andamento terminam)."""
        if self._rodando.is_set():
            self._rodando.clear()
            self.estado.pausado = True
            self.ao_logar("AVISO", "Pesquisa pausada pelo usuário.")
            self.registrador.anotar("Pesquisa pausada pelo usuário.")
            self._notificar()

    def continuar(self) -> None:
        """Retoma o processamento após uma pausa."""
        if not self._rodando.is_set():
            self._captcha_ativo.clear()
            self._rodando.set()
            self.estado.pausado = False
            self.ao_logar("INFO", "Pesquisa retomada.")
            self.registrador.anotar("Pesquisa retomada.")
            self._notificar()

    def cancelar(self) -> None:
        """Cancela o processamento; empresas em andamento são finalizadas."""
        self._cancelado.set()
        self._rodando.set()   # libera quem estiver esperando na pausa
        self.estado.pausado = False
        self.ao_logar("AVISO", "Cancelamento solicitado — encerrando…")
        self.registrador.anotar("Cancelamento solicitado pelo usuário.")

    @property
    def executando(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def aguardar(self, timeout: Optional[float] = None) -> None:
        """Bloqueia até o término do processamento."""
        if self._thread:
            self._thread.join(timeout)

    # ==================================================================
    # Execução
    # ==================================================================

    def _executar(self, empresas: List[Empresa]) -> None:
        """Laço principal, executado na thread de fundo."""
        self.registrador.cabecalho(len(empresas), str(self.planilha.caminho_origem))
        self.ao_logar(
            "INFO",
            f"Iniciando pesquisa de {len(empresas)} empresa(s) "
            f"com {self.cfg.max_workers} thread(s).",
        )

        executor = ThreadPoolExecutor(
            max_workers=self.cfg.max_workers, thread_name_prefix="pesquisa"
        )
        futuros: Dict[Future, Empresa] = {}

        try:
            for empresa in empresas:
                if self._cancelado.is_set():
                    break
                self._aguardar_retomada()
                if self._cancelado.is_set():
                    break

                futuro = executor.submit(self._processar, empresa)
                futuros[futuro] = empresa

                # Mantém a fila curta: assim, pausa e cancelamento respondem
                # rápido em vez de esperar milhares de tarefas enfileiradas.
                while len(futuros) >= self.cfg.max_workers * 2:
                    self._drenar(futuros, bloquear=True)

            while futuros:
                self._drenar(futuros, bloquear=True)

        except Exception:
            logger.exception("Falha inesperada no motor de pesquisa.")
        finally:
            executor.shutdown(wait=True)
            self._finalizar()

    def _drenar(self, futuros: Dict[Future, Empresa], bloquear: bool) -> None:
        """Consome os futuros concluídos, aplicando cada resultado."""
        concluidos = [f for f in futuros if f.done()]

        if not concluidos and bloquear and futuros:
            # Espera o primeiro terminar sem consumir CPU.
            from concurrent.futures import wait, FIRST_COMPLETED

            wait(list(futuros), timeout=1.0, return_when=FIRST_COMPLETED)
            concluidos = [f for f in futuros if f.done()]

        for futuro in concluidos:
            empresa = futuros.pop(futuro)
            try:
                resultado = futuro.result()
            except Exception as exc:
                logger.exception("Falha ao processar %s", empresa.identificador)
                resultado = ResultadoPesquisa(empresa=empresa)
                resultado.status = StatusPesquisa.ERRO
                resultado.erro = f"{type(exc).__name__}: {exc}"
            self._aplicar(resultado)

    def _processar(self, empresa: Empresa) -> ResultadoPesquisa:
        """
        Pesquisa uma empresa, tratando captcha com pausa e uma retentativa.

        Executado dentro de um worker do pool.
        """
        with self._lock:
            self.estado.empresa_atual = empresa.identificador

        for tentativa in (1, 2):
            self._aguardar_retomada()
            if self._cancelado.is_set():
                resultado = ResultadoPesquisa(empresa=empresa)
                resultado.status = StatusPesquisa.CANCELADO
                return resultado

            try:
                return self.pesquisador.pesquisar(empresa)
            except utils.CaptchaDetectado as exc:
                self._tratar_captcha(exc)
                if tentativa == 2 or self._cancelado.is_set():
                    resultado = ResultadoPesquisa(empresa=empresa)
                    resultado.status = StatusPesquisa.ERRO
                    resultado.erro = f"Bloqueio persistente: {exc}"
                    return resultado

        # Inalcançável, mas mantém o tipo de retorno explícito.
        return ResultadoPesquisa(empresa=empresa)

    # ==================================================================
    # Captcha
    # ==================================================================

    def _tratar_captcha(self, exc: utils.CaptchaDetectado) -> None:
        """
        Pausa o motor e avisa o usuário ao detectar bloqueio anti-robô.

        A retomada pode ser manual (botão "Continuar") ou automática após
        :data:`config.PAUSA_APOS_CAPTCHA` segundos.
        """
        if self._captcha_ativo.is_set():
            self._aguardar_retomada()
            return

        self._captcha_ativo.set()
        self._rodando.clear()
        self.estado.pausado = True

        mensagem = (
            f"Captcha/bloqueio detectado em {exc.url}. "
            "A pesquisa foi pausada automaticamente. "
            "Aguarde alguns minutos, troque de rede/VPN se possível e clique em "
            "'Continuar'."
        )
        logger.warning(mensagem)
        self.registrador.anotar(f"CAPTCHA: {exc}")
        self.ao_logar("AVISO", mensagem)
        self.ao_detectar_captcha(mensagem)
        self._notificar()

        # Retomada automática caso o usuário não intervenha.
        limite = time.monotonic() + config.PAUSA_APOS_CAPTCHA
        while (
            not self._rodando.is_set()
            and not self._cancelado.is_set()
            and time.monotonic() < limite
        ):
            time.sleep(0.5)

        if not self._rodando.is_set() and not self._cancelado.is_set():
            self.ao_logar("INFO", "Retomada automática após a pausa por captcha.")
            self.continuar()

    def _aguardar_retomada(self) -> None:
        """Bloqueia enquanto o motor estiver pausado."""
        while not self._rodando.wait(timeout=0.5):
            if self._cancelado.is_set():
                return

    # ==================================================================
    # Aplicação de resultados
    # ==================================================================

    def _aplicar(self, resultado: ResultadoPesquisa) -> None:
        """Grava o resultado na planilha, no log e atualiza as estatísticas."""
        with self._lock:
            self.resultados.append(resultado)
            self.estado.processadas += 1
            if resultado.duracao:
                self._duracoes.append(resultado.duracao)

            if resultado.status == StatusPesquisa.ENCONTRADO:
                self.estado.encontradas += 1
            elif resultado.status == StatusPesquisa.APENAS_EMAIL:
                self.estado.encontradas += 1
            elif resultado.status == StatusPesquisa.REVISAO_MANUAL:
                self.estado.revisao_manual += 1
            elif resultado.status == StatusPesquisa.CONFERENCIA_MANUAL:
                self.estado.revisao_manual += 1
            elif resultado.status == StatusPesquisa.ERRO:
                self.estado.erros += 1
            elif resultado.status == StatusPesquisa.NAO_ENCONTRADO:
                self.estado.sem_resultado += 1

            self.estado.tempo_decorrido = time.monotonic() - self._inicio
            self.estado.tempo_estimado = self._estimar_restante()

        # Salvamento automático após CADA empresa (requisito do escopo).
        try:
            self.planilha.aplicar_resultado(resultado, salvar=True)
        except excel.PlanilhaError as exc:
            logger.error("Falha ao salvar a planilha: %s", exc)
            self.ao_logar("ERRO", str(exc))
        except Exception:
            logger.exception("Falha inesperada ao aplicar resultado na planilha.")

        try:
            self.registrador.registrar(resultado)
        except Exception:
            logger.exception("Falha ao registrar no log.txt.")

        self.ao_logar(
            _nivel_do_status(resultado.status), self._resumo_linha(resultado)
        )
        self._notificar()

    @staticmethod
    def _resumo_linha(res: ResultadoPesquisa) -> str:
        """Linha curta exibida na caixa de log da interface."""
        partes = [f"[{res.status.value}] {res.empresa.razao_social}"]
        if res.telefones:
            partes.append("Tel: " + ", ".join(t.valor for t in res.telefones[:2]))
        if res.emails:
            partes.append("E-mail: " + res.emails[0].valor)
        if res.tem_algum_dado:
            fonte = res.fonte_principal
            partes.append(f"Fonte: {fonte.value if fonte else '—'}")
            partes.append(f"Confiança: {res.confianca.value}")
        partes.append(f"({utils.formatar_duracao(res.duracao)})")
        return " | ".join(partes)

    def _estimar_restante(self) -> float:
        """
        Estima o tempo restante pela média das últimas 20 pesquisas.

        A média é dividida pelo número de workers, já que as empresas são
        processadas em paralelo.
        """
        if not self._duracoes or self.estado.restantes == 0:
            return 0.0
        amostra = self._duracoes[-20:]
        media = sum(amostra) / len(amostra)
        return (media * self.estado.restantes) / max(1, self.cfg.max_workers)

    def _notificar(self) -> None:
        """Entrega uma cópia do estado à interface."""
        try:
            self.ao_progredir(self.estado)
        except Exception:
            logger.debug("Callback de progresso falhou.", exc_info=True)

    # ==================================================================
    # Encerramento
    # ==================================================================

    def _finalizar(self) -> None:
        """Fecha recursos, gera relatório e notifica o término."""
        self.estado.executando = False
        self.estado.pausado = False
        self.estado.empresa_atual = ""
        self.estado.tempo_decorrido = time.monotonic() - self._inicio

        try:
            mod_maps.encerrar_navegadores()
        except Exception:
            logger.debug("Falha ao encerrar navegadores.", exc_info=True)

        try:
            self.pesquisador.liberar()
        except Exception:
            pass

        try:
            self.planilha.salvar(forcar=True)
        except Exception as exc:
            logger.error("Falha ao salvar a planilha no encerramento: %s", exc)

        try:
            relatorio = self.planilha.gerar_relatorio(self.resultados)
            if relatorio:
                self.ao_logar("INFO", f"Relatório gerado: {relatorio}")
        except Exception:
            logger.exception("Falha ao gerar o relatório.")

        try:
            self.registrador.rodape(self.estado.resumo(), self.estado.tempo_decorrido)
        except Exception:
            pass

        encerramento = (
            "Processamento cancelado."
            if self._cancelado.is_set()
            else "Processamento concluído."
        )
        self.ao_logar(
            "INFO",
            f"{encerramento} {self.estado.encontradas} com contato, "
            f"{self.estado.sem_resultado} sem resultado, "
            f"{self.estado.revisao_manual} para revisão, "
            f"{self.estado.erros} com erro. "
            f"Tempo total: {utils.formatar_duracao(self.estado.tempo_decorrido)}.",
        )
        self._notificar()

        try:
            self.ao_terminar(self.estado, self.resultados)
        except Exception:
            logger.exception("Callback de término falhou.")


def _nivel_do_status(status: StatusPesquisa) -> str:
    """Traduz o status em um nível de log para colorização na interface."""
    return {
        StatusPesquisa.ENCONTRADO: "SUCESSO",
        StatusPesquisa.APENAS_EMAIL: "AVISO",
        StatusPesquisa.NAO_ENCONTRADO: "ERRO",
        StatusPesquisa.REVISAO_MANUAL: "AVISO",
        StatusPesquisa.CONFERENCIA_MANUAL: "AVISO",
        StatusPesquisa.ERRO: "ERRO",
    }.get(status, "INFO")
