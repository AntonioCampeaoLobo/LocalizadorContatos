# -*- coding: utf-8 -*-
"""
interface.py
============

Interface gráfica em CustomTkinter.

Estrutura da janela:

* **Painel de configuração** — seleção da planilha, pasta de saída, número de
  threads, intervalo entre requisições e quais fontes utilizar;
* **Barra de controle** — Selecionar planilha, Iniciar, Pausar, Continuar,
  Cancelar;
* **Painel de progresso** — barra de progresso, empresa atual, quantidade
  pesquisada/restante, tempo decorrido e estimado, empresas encontradas, sem
  resultado e pendentes;
* **Console de log** — mensagens em tempo real, coloridas por severidade.

Regra de thread-safety do Tkinter: **nenhum callback vindo do motor toca a UI
diretamente**. Todos empurram eventos para uma ``queue.Queue`` drenada pelo
laço ``after()`` da própria thread da interface.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Optional

import customtkinter as ctk

import config
import excel
import pesquisa
import utils
from modelos import Empresa, ResultadoPesquisa

logger = logging.getLogger("localizador.interface")


# Cores das mensagens do console por severidade.
CORES_LOG = {
    "INFO": "#dfe6ee",
    "SUCESSO": "#57d38c",
    "AVISO": "#f3c05b",
    "ERRO": "#f0736a",
    "DEBUG": "#8a94a6",
}


class JanelaPrincipal(ctk.CTk):
    """Janela principal da aplicação."""

    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode(config.TEMA_CUSTOMTKINTER)
        ctk.set_default_color_theme(config.TEMA_COR_PADRAO)

        self.title(f"{config.APP_NOME} — v{config.APP_VERSAO}")
        self.geometry(f"{config.JANELA_LARGURA}x{config.JANELA_ALTURA}")
        self.minsize(980, 660)

        # ---- estado ---------------------------------------------------
        self.cfg = config.Configuracao.carregar()
        self.fila_eventos: "queue.Queue[tuple]" = queue.Queue()
        self.motor: Optional[pesquisa.MotorPesquisa] = None
        self.planilha: Optional[excel.PlanilhaContatos] = None
        self.total_empresas = 0
        self.total_pendentes = 0
        self.total_ignoradas = 0
        self._linhas_log = 0

        # ---- construção da UI -----------------------------------------
        self._montar_layout()
        self._montar_cabecalho()
        self._montar_configuracao()
        self._montar_controles()
        self._montar_progresso()
        self._montar_log()
        self._montar_rodape()

        self._atualizar_botoes(executando=False, pausado=False)
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self.after(config.INTERVALO_ATUALIZACAO_UI_MS, self._drenar_eventos)

        self._logar(
            "INFO",
            f"{config.APP_NOME} v{config.APP_VERSAO} pronto. "
            "Selecione uma planilha .xlsx para começar.",
        )
        if self.cfg.caminho_planilha:
            self.entrada_planilha.delete(0, "end")
            self.entrada_planilha.insert(0, self.cfg.caminho_planilha)

    # ==================================================================
    # Construção da interface
    # ==================================================================

    def _montar_layout(self) -> None:
        """Define a grade principal da janela."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)   # a linha do log é a elástica

    def _montar_cabecalho(self) -> None:
        """Título e subtítulo."""
        quadro = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        quadro.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        quadro.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            quadro,
            text=config.APP_NOME,
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            quadro,
            text=(
                "Pesquisa contatos em fontes públicas e preenche a planilha. "
                "Nenhum dado é inventado: todo contato gravado tem origem registrada no log."
            ),
            font=ctk.CTkFont(size=12),
            text_color="#9aa4b2",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _montar_configuracao(self) -> None:
        """Painel de seleção de arquivo e parâmetros."""
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=1, column=0, sticky="ew", padx=16, pady=12)
        quadro.grid_columnconfigure(1, weight=1)

        # --- planilha ---------------------------------------------------
        ctk.CTkLabel(quadro, text="Planilha (.xlsx):").grid(
            row=0, column=0, padx=(14, 8), pady=(14, 6), sticky="w"
        )
        self.entrada_planilha = ctk.CTkEntry(
            quadro, placeholder_text="Selecione a planilha de clientes…"
        )
        self.entrada_planilha.grid(row=0, column=1, sticky="ew", pady=(14, 6))
        self.botao_selecionar = ctk.CTkButton(
            quadro, text="Selecionar planilha", width=170, command=self.selecionar_planilha
        )
        self.botao_selecionar.grid(row=0, column=2, padx=14, pady=(14, 6))

        # --- pasta de saída ---------------------------------------------
        ctk.CTkLabel(quadro, text="Pasta de saída:").grid(
            row=1, column=0, padx=(14, 8), pady=6, sticky="w"
        )
        self.entrada_saida = ctk.CTkEntry(
            quadro, placeholder_text="Padrão: subpasta 'saida' do projeto"
        )
        self.entrada_saida.grid(row=1, column=1, sticky="ew", pady=6)
        self.entrada_saida.insert(0, self.cfg.pasta_saida or str(config.RAIZ_PROJETO / "saida"))
        ctk.CTkButton(
            quadro, text="Escolher pasta", width=170, command=self.selecionar_saida
        ).grid(row=1, column=2, padx=14, pady=6)

        # --- parâmetros --------------------------------------------------
        parametros = ctk.CTkFrame(quadro, fg_color="transparent")
        parametros.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=(6, 4))

        ctk.CTkLabel(parametros, text="Empresas simultâneas:").pack(side="left")
        self.seletor_threads = ctk.CTkOptionMenu(
            parametros,
            width=70,
            values=[str(i) for i in range(1, config.MAX_WORKERS_LIMITE + 1)],
        )
        self.seletor_threads.set(str(self.cfg.max_workers))
        self.seletor_threads.pack(side="left", padx=(8, 22))

        ctk.CTkLabel(parametros, text="Intervalo entre buscas (s):").pack(side="left")
        self.entrada_delay_min = ctk.CTkEntry(parametros, width=60)
        self.entrada_delay_min.insert(0, str(self.cfg.delay_min))
        self.entrada_delay_min.pack(side="left", padx=(8, 4))
        ctk.CTkLabel(parametros, text="a").pack(side="left")
        self.entrada_delay_max = ctk.CTkEntry(parametros, width=60)
        self.entrada_delay_max.insert(0, str(self.cfg.delay_max))
        self.entrada_delay_max.pack(side="left", padx=(4, 22))

        # --- fontes ------------------------------------------------------
        fontes = ctk.CTkFrame(quadro, fg_color="transparent")
        fontes.grid(row=3, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 14))

        ctk.CTkLabel(fontes, text="Fontes:").pack(side="left", padx=(0, 10))
        self.var_site = ctk.BooleanVar(value=self.cfg.usar_site_oficial)
        self.var_maps = ctk.BooleanVar(value=self.cfg.usar_google_maps)
        self.var_busca = ctk.BooleanVar(value=self.cfg.usar_google_search)
        self.var_cnpj = ctk.BooleanVar(value=self.cfg.usar_cnpj)
        self.var_headless = ctk.BooleanVar(value=self.cfg.headless)

        for texto, variavel in (
            ("Site oficial", self.var_site),
            ("Google Maps", self.var_maps),
            ("Busca web", self.var_busca),
            ("CNPJ / Receita", self.var_cnpj),
        ):
            ctk.CTkCheckBox(fontes, text=texto, variable=variavel).pack(
                side="left", padx=(0, 18)
            )
        ctk.CTkCheckBox(
            fontes, text="Navegador oculto", variable=self.var_headless
        ).pack(side="left", padx=(0, 18))

    def _montar_controles(self) -> None:
        """Botões Iniciar / Pausar / Continuar / Cancelar."""
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=2, column=0, sticky="ew", padx=16)
        quadro.grid_columnconfigure(5, weight=1)

        self.botao_iniciar = ctk.CTkButton(
            quadro, text="▶  Iniciar pesquisa", width=180, height=38,
            font=ctk.CTkFont(size=13, weight="bold"), command=self.iniciar,
        )
        self.botao_iniciar.grid(row=0, column=0, padx=(14, 8), pady=12)

        self.botao_pausar = ctk.CTkButton(
            quadro, text="⏸  Pausar", width=130, height=38,
            fg_color="#8a6d1f", hover_color="#a5831f", command=self.pausar,
        )
        self.botao_pausar.grid(row=0, column=1, padx=8, pady=12)

        self.botao_continuar = ctk.CTkButton(
            quadro, text="⏵  Continuar", width=130, height=38,
            fg_color="#1f6f4a", hover_color="#248456", command=self.continuar,
        )
        self.botao_continuar.grid(row=0, column=2, padx=8, pady=12)

        self.botao_cancelar = ctk.CTkButton(
            quadro, text="■  Cancelar", width=130, height=38,
            fg_color="#8c3a35", hover_color="#a4443e", command=self.cancelar,
        )
        self.botao_cancelar.grid(row=0, column=3, padx=8, pady=12)

        self.botao_abrir_saida = ctk.CTkButton(
            quadro, text="📂  Abrir pasta de saída", width=190, height=38,
            fg_color="transparent", border_width=1, command=self.abrir_pasta_saida,
        )
        self.botao_abrir_saida.grid(row=0, column=4, padx=8, pady=12)

    def _montar_progresso(self) -> None:
        """Barra de progresso e painel de indicadores."""
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=3, column=0, sticky="ew", padx=16, pady=12)
        quadro.grid_columnconfigure(0, weight=1)

        # --- barra -------------------------------------------------------
        linha = ctk.CTkFrame(quadro, fg_color="transparent")
        linha.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 4))
        linha.grid_columnconfigure(0, weight=1)

        self.barra = ctk.CTkProgressBar(linha, height=16)
        self.barra.set(0)
        self.barra.grid(row=0, column=0, sticky="ew")

        self.rotulo_percentual = ctk.CTkLabel(
            linha, text="0%", width=60, font=ctk.CTkFont(size=13, weight="bold")
        )
        self.rotulo_percentual.grid(row=0, column=1, padx=(12, 0))

        # --- empresa atual -----------------------------------------------
        self.rotulo_empresa = ctk.CTkLabel(
            quadro, text="Empresa atual: —", anchor="w",
            font=ctk.CTkFont(size=13), text_color="#cbd5e1",
        )
        self.rotulo_empresa.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 8))

        # --- indicadores --------------------------------------------------
        painel = ctk.CTkFrame(quadro, fg_color="transparent")
        painel.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 14))
        for coluna in range(4):
            painel.grid_columnconfigure(coluna, weight=1)

        self.indicadores = {}
        definicoes = [
            ("pesquisadas", "Pesquisadas", "#8ab4f8"),
            ("restantes", "Restantes", "#cbd5e1"),
            ("encontradas", "Empresas encontradas", "#57d38c"),
            ("sem_resultado", "Sem resultado", "#f0736a"),
            ("revisao", "Revisão manual", "#f3c05b"),
            ("pendentes", "Pendentes na planilha", "#cbd5e1"),
            ("decorrido", "Tempo decorrido", "#cbd5e1"),
            ("estimado", "Tempo estimado", "#8ab4f8"),
        ]
        for indice, (chave, titulo, cor) in enumerate(definicoes):
            cartao = ctk.CTkFrame(painel, corner_radius=8)
            cartao.grid(
                row=indice // 4, column=indice % 4, padx=6, pady=6, sticky="ew"
            )
            ctk.CTkLabel(
                cartao, text=titulo, font=ctk.CTkFont(size=11), text_color="#94a3b8"
            ).pack(padx=12, pady=(8, 0), anchor="w")
            valor = ctk.CTkLabel(
                cartao, text="0", font=ctk.CTkFont(size=19, weight="bold"), text_color=cor
            )
            valor.pack(padx=12, pady=(0, 8), anchor="w")
            self.indicadores[chave] = valor

    def _montar_log(self) -> None:
        """Console de mensagens em tempo real."""
        quadro = ctk.CTkFrame(self, corner_radius=10)
        quadro.grid(row=4, column=0, sticky="nsew", padx=16)
        quadro.grid_columnconfigure(0, weight=1)
        quadro.grid_rowconfigure(1, weight=1)

        cabecalho = ctk.CTkFrame(quadro, fg_color="transparent")
        cabecalho.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        cabecalho.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            cabecalho, text="Log da pesquisa",
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            cabecalho, text="Limpar", width=90, height=26,
            fg_color="transparent", border_width=1, command=self.limpar_log,
        ).grid(row=0, column=1, padx=4)

        ctk.CTkButton(
            cabecalho, text="Abrir log.txt", width=120, height=26,
            fg_color="transparent", border_width=1, command=self.abrir_log,
        ).grid(row=0, column=2, padx=4)

        self.caixa_log = ctk.CTkTextbox(
            quadro, font=ctk.CTkFont(family="Consolas", size=12), wrap="word"
        )
        self.caixa_log.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))
        self.caixa_log.configure(state="disabled")

        # Tags de cor — CTkTextbox delega ao widget Text interno.
        alvo = getattr(self.caixa_log, "_textbox", self.caixa_log)
        for nivel, cor in CORES_LOG.items():
            try:
                alvo.tag_config(nivel, foreground=cor)
            except Exception:  # pragma: no cover - depende da versão do CTk
                pass

    def _montar_rodape(self) -> None:
        """Barra de status inferior."""
        self.rotulo_status = ctk.CTkLabel(
            self, text="Pronto.", anchor="w",
            font=ctk.CTkFont(size=12), text_color="#94a3b8",
        )
        self.rotulo_status.grid(row=5, column=0, sticky="ew", padx=20, pady=(6, 12))

    # ==================================================================
    # Ações da interface
    # ==================================================================

    def selecionar_planilha(self) -> None:
        """Abre o seletor de arquivos e carrega a planilha escolhida."""
        caminho = filedialog.askopenfilename(
            title="Selecione a planilha de clientes",
            filetypes=[("Planilhas Excel", "*.xlsx *.xlsm"), ("Todos os arquivos", "*.*")],
        )
        if not caminho:
            return

        self.entrada_planilha.delete(0, "end")
        self.entrada_planilha.insert(0, caminho)
        self._analisar_planilha(caminho)

    def selecionar_saida(self) -> None:
        """Escolhe a pasta onde os arquivos gerados serão gravados."""
        pasta = filedialog.askdirectory(title="Escolha a pasta de saída")
        if pasta:
            self.entrada_saida.delete(0, "end")
            self.entrada_saida.insert(0, pasta)

    def _analisar_planilha(self, caminho: str) -> None:
        """
        Lê a planilha e informa quantas empresas estão pendentes.

        Usa a leitura somente-leitura: apenas selecionar o arquivo não deve
        gerar ``Empresas_Preenchidas.xlsx`` nem alterar nada em disco.
        """
        try:
            empresas, com_telefone = excel.inspecionar(caminho)
        except Exception as exc:
            self._logar("ERRO", f"Não foi possível ler a planilha: {exc}")
            messagebox.showerror("Erro ao abrir a planilha", str(exc))
            return

        self.total_empresas = len(empresas)
        self.total_ignoradas = com_telefone
        self.total_pendentes = len(empresas) - com_telefone

        self.indicadores["pendentes"].configure(text=str(self.total_pendentes))
        self.indicadores["restantes"].configure(text=str(self.total_pendentes))
        self._logar(
            "INFO",
            f"Planilha carregada: {self.total_empresas} empresa(s); "
            f"{self.total_pendentes} sem telefone (serão pesquisadas); "
            f"{self.total_ignoradas} já possuem telefone e serão ignoradas.",
        )
        self._status(f"Planilha pronta: {self.total_pendentes} empresa(s) a pesquisar.")

    # ------------------------------------------------------------------

    def iniciar(self) -> None:
        """Valida a configuração e dispara o motor de pesquisa."""
        if self.motor and self.motor.executando:
            messagebox.showinfo("Em execução", "A pesquisa já está em andamento.")
            return

        cfg = self._coletar_configuracao()
        problemas = cfg.validar()
        if problemas:
            messagebox.showwarning("Configuração inválida", "\n".join(problemas))
            return

        self.cfg = cfg
        try:
            cfg.salvar()
        except Exception:
            logger.debug("Não foi possível salvar a configuração.", exc_info=True)

        # --- abre a planilha -------------------------------------------
        try:
            self.planilha = excel.PlanilhaContatos(cfg.caminho_planilha, self._pasta_saida())
            self.planilha.abrir()
            todas = self.planilha.empresas()
            pendentes = [
                e for e in todas if not excel.possui_telefone_valido(e.contato_existente)
            ]
        except Exception as exc:
            self._logar("ERRO", f"Falha ao abrir a planilha: {exc}")
            messagebox.showerror("Erro", str(exc))
            return

        if not pendentes:
            messagebox.showinfo(
                "Nada a fazer", "Todas as empresas já possuem telefone preenchido."
            )
            self.planilha.fechar()
            return

        self.total_empresas = len(todas)
        self.total_pendentes = len(pendentes)
        self.total_ignoradas = len(todas) - len(pendentes)

        # --- avisos sobre fontes indisponíveis --------------------------
        if cfg.usar_google_maps:
            import maps as mod_maps

            disponivel, motivo = mod_maps.playwright_disponivel()
            if not disponivel:
                self._logar("AVISO", motivo + " — o Google Maps será ignorado.")
                cfg.usar_google_maps = False

        # --- inicia -----------------------------------------------------
        registrador = pesquisa.RegistradorLog(self._pasta_saida() / config.ARQ_LOG)
        self.motor = pesquisa.MotorPesquisa(
            cfg=cfg,
            planilha=self.planilha,
            registrador=registrador,
            ao_progredir=lambda estado: self.fila_eventos.put(("progresso", estado)),
            ao_logar=lambda nivel, msg: self.fila_eventos.put(("log", (nivel, msg))),
            ao_detectar_captcha=lambda msg: self.fila_eventos.put(("captcha", msg)),
            ao_terminar=lambda estado, res: self.fila_eventos.put(("fim", (estado, res))),
        )

        self.limpar_log()
        self._logar(
            "INFO",
            f"Iniciando: {self.total_pendentes} empresa(s) a pesquisar, "
            f"{self.total_ignoradas} ignorada(s) por já possuírem telefone.",
        )
        self.indicadores["pendentes"].configure(text=str(self.total_pendentes))
        self.motor.iniciar(pendentes, ignoradas=self.total_ignoradas)
        self._atualizar_botoes(executando=True, pausado=False)
        self._status("Pesquisando…")

    def pausar(self) -> None:
        """Pausa o motor."""
        if self.motor and self.motor.executando:
            self.motor.pausar()
            self._atualizar_botoes(executando=True, pausado=True)
            self._status("Pausado.")

    def continuar(self) -> None:
        """Retoma o motor."""
        if self.motor and self.motor.executando:
            self.motor.continuar()
            self._atualizar_botoes(executando=True, pausado=False)
            self._status("Pesquisando…")

    def cancelar(self) -> None:
        """Cancela o processamento após confirmação."""
        if not (self.motor and self.motor.executando):
            return
        if not messagebox.askyesno(
            "Cancelar pesquisa",
            "Deseja realmente cancelar?\n\n"
            "Tudo que já foi encontrado permanece salvo na planilha de saída.",
        ):
            return
        self.motor.cancelar()
        self._status("Cancelando…")

    def abrir_pasta_saida(self) -> None:
        """Abre a pasta de saída no explorador de arquivos."""
        pasta = self._pasta_saida()
        pasta.mkdir(parents=True, exist_ok=True)
        webbrowser.open(pasta.as_uri())

    def abrir_log(self) -> None:
        """Abre o ``log.txt`` no aplicativo padrão."""
        caminho = self._pasta_saida() / config.ARQ_LOG
        if caminho.is_file():
            webbrowser.open(caminho.as_uri())
        else:
            messagebox.showinfo("Log", "O arquivo log.txt ainda não foi gerado.")

    def limpar_log(self) -> None:
        """Esvazia o console."""
        self.caixa_log.configure(state="normal")
        self.caixa_log.delete("1.0", "end")
        self.caixa_log.configure(state="disabled")
        self._linhas_log = 0

    # ==================================================================
    # Ponte thread -> interface
    # ==================================================================

    def _drenar_eventos(self) -> None:
        """
        Consome a fila de eventos vinda do motor.

        Executa na thread da interface — é o único lugar que altera widgets.
        """
        try:
            while True:
                tipo, dado = self.fila_eventos.get_nowait()

                if tipo == "log":
                    nivel, mensagem = dado
                    self._logar(nivel, mensagem)
                elif tipo == "progresso":
                    self._atualizar_progresso(dado)
                elif tipo == "captcha":
                    self._avisar_captcha(dado)
                elif tipo == "fim":
                    estado, resultados = dado
                    self._ao_terminar(estado, resultados)
        except queue.Empty:
            pass
        finally:
            self.after(config.INTERVALO_ATUALIZACAO_UI_MS, self._drenar_eventos)

    def _atualizar_progresso(self, estado: pesquisa.EstadoProgresso) -> None:
        """Atualiza barra e indicadores."""
        self.barra.set(estado.percentual)
        self.rotulo_percentual.configure(text=f"{estado.percentual * 100:.0f}%")
        self.rotulo_empresa.configure(
            text=f"Empresa atual: {estado.empresa_atual or '—'}"
        )

        self.indicadores["pesquisadas"].configure(text=str(estado.processadas))
        self.indicadores["restantes"].configure(text=str(estado.restantes))
        self.indicadores["encontradas"].configure(text=str(estado.encontradas))
        self.indicadores["sem_resultado"].configure(text=str(estado.sem_resultado))
        self.indicadores["revisao"].configure(text=str(estado.revisao_manual))
        self.indicadores["pendentes"].configure(text=str(estado.restantes))
        self.indicadores["decorrido"].configure(
            text=utils.formatar_duracao(estado.tempo_decorrido)
        )
        self.indicadores["estimado"].configure(
            text=utils.formatar_duracao(estado.tempo_estimado)
            if estado.tempo_estimado
            else "—"
        )

        if estado.executando:
            self._status("Pausado." if estado.pausado else "Pesquisando…")

    def _avisar_captcha(self, mensagem: str) -> None:
        """Traz a janela à frente e alerta sobre o bloqueio."""
        self._atualizar_botoes(executando=True, pausado=True)
        self._status("Pausado por captcha.")
        try:
            self.bell()
            self.lift()
        except Exception:
            pass
        messagebox.showwarning("Captcha detectado", mensagem)

    def _ao_terminar(
        self, estado: pesquisa.EstadoProgresso, resultados: List[ResultadoPesquisa]
    ) -> None:
        """Fecha a planilha e apresenta o resumo final."""
        self._atualizar_botoes(executando=False, pausado=False)
        self._status("Concluído.")

        if self.planilha:
            try:
                self.planilha.fechar()
            except Exception as exc:
                self._logar("ERRO", f"Falha ao fechar a planilha: {exc}")

        pasta = self._pasta_saida()
        messagebox.showinfo(
            "Pesquisa concluída",
            f"Empresas processadas: {estado.processadas}\n"
            f"Com contato encontrado: {estado.encontradas}\n"
            f"Sem resultado: {estado.sem_resultado}\n"
            f"Para revisão manual: {estado.revisao_manual}\n"
            f"Erros: {estado.erros}\n\n"
            f"Tempo total: {utils.formatar_duracao(estado.tempo_decorrido)}\n\n"
            f"Arquivos gerados em:\n{pasta}",
        )

    # ==================================================================
    # Utilitários internos
    # ==================================================================

    def _logar(self, nivel: str, mensagem: str) -> None:
        """Escreve uma linha colorida no console da interface."""
        nivel = nivel if nivel in CORES_LOG else "INFO"
        agora = time.strftime("%H:%M:%S")

        self.caixa_log.configure(state="normal")
        self.caixa_log.insert("end", f"[{agora}] {mensagem}\n", nivel)
        self._linhas_log += 1

        # Mantém o console leve descartando linhas antigas.
        if self._linhas_log > config.MAX_LINHAS_LOG_UI:
            excedente = self._linhas_log - config.MAX_LINHAS_LOG_UI
            self.caixa_log.delete("1.0", f"{excedente + 1}.0")
            self._linhas_log = config.MAX_LINHAS_LOG_UI

        self.caixa_log.see("end")
        self.caixa_log.configure(state="disabled")

    def _status(self, texto: str) -> None:
        self.rotulo_status.configure(text=texto)

    def _pasta_saida(self) -> Path:
        """Pasta de saída atualmente configurada."""
        texto = self.entrada_saida.get().strip()
        return Path(texto) if texto else config.RAIZ_PROJETO / "saida"

    def _coletar_configuracao(self) -> config.Configuracao:
        """Monta o objeto de configuração a partir dos widgets."""
        cfg = config.Configuracao.carregar()
        cfg.caminho_planilha = self.entrada_planilha.get().strip()
        cfg.pasta_saida = str(self._pasta_saida())

        try:
            cfg.max_workers = int(self.seletor_threads.get())
        except ValueError:
            cfg.max_workers = config.MAX_WORKERS

        for atributo, entrada, padrao in (
            ("delay_min", self.entrada_delay_min, config.DELAY_MIN),
            ("delay_max", self.entrada_delay_max, config.DELAY_MAX),
        ):
            try:
                setattr(cfg, atributo, float(entrada.get().replace(",", ".")))
            except ValueError:
                setattr(cfg, atributo, padrao)

        cfg.usar_site_oficial = self.var_site.get()
        cfg.usar_google_maps = self.var_maps.get()
        cfg.usar_google_search = self.var_busca.get()
        cfg.usar_cnpj = self.var_cnpj.get()
        cfg.headless = self.var_headless.get()
        cfg.usar_playwright = cfg.usar_google_maps
        return cfg

    def _atualizar_botoes(self, executando: bool, pausado: bool) -> None:
        """Habilita/desabilita botões conforme o estado do motor."""
        self.botao_iniciar.configure(state="disabled" if executando else "normal")
        self.botao_selecionar.configure(state="disabled" if executando else "normal")
        self.botao_pausar.configure(
            state="normal" if executando and not pausado else "disabled"
        )
        self.botao_continuar.configure(
            state="normal" if executando and pausado else "disabled"
        )
        self.botao_cancelar.configure(state="normal" if executando else "disabled")

    def _ao_fechar(self) -> None:
        """Encerramento seguro: cancela o motor e salva a planilha."""
        if self.motor and self.motor.executando:
            if not messagebox.askyesno(
                "Sair", "A pesquisa está em andamento. Cancelar e sair?"
            ):
                return
            self.motor.cancelar()
            self.motor.aguardar(timeout=20)

        if self.planilha:
            try:
                self.planilha.fechar()
            except Exception:
                pass

        try:
            import maps as mod_maps

            mod_maps.encerrar_navegadores()
        except Exception:
            pass

        self.destroy()


def executar() -> None:
    """Cria e executa a janela principal."""
    janela = JanelaPrincipal()
    janela.mainloop()
