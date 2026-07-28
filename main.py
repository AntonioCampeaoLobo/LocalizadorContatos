# -*- coding: utf-8 -*-
"""
main.py
=======

Ponto de entrada do **Localizador de Contatos Empresariais**.

Dois modos de execução:

* **Gráfico** (padrão) — abre a janela CustomTkinter::

      python main.py

* **Linha de comando** — útil para execuções longas em servidor, agendamento
  ou testes::

      python main.py --cli --planilha "C:\\caminho\\carteira.xlsx"
      python main.py --cli --planilha carteira.xlsx --limite 10 --threads 3

O modo CLI aceita ``Ctrl+C`` a qualquer momento: o motor é cancelado com
segurança e a planilha permanece salva com tudo o que já foi encontrado.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional

import config


# ===========================================================================
# Verificação de dependências
# ===========================================================================

# (módulo importável, pacote no pip, obrigatório?)
DEPENDENCIAS = [
    ("openpyxl", "openpyxl", True),
    ("requests", "requests", True),
    ("bs4", "beautifulsoup4", True),
    ("lxml", "lxml", False),
    ("customtkinter", "customtkinter", False),
    ("playwright", "playwright", False),
    ("fake_useragent", "fake-useragent", False),
    ("pandas", "pandas", False),
]


def verificar_dependencias(exigir_interface: bool) -> List[str]:
    """
    Confere as bibliotecas instaladas.

    Args:
        exigir_interface: Se ``True``, o CustomTkinter passa a ser obrigatório.

    Returns:
        Lista de mensagens sobre pacotes ausentes. Itens obrigatórios são
        prefixados com ``[FALTA]``; opcionais, com ``[opcional]``.
    """
    problemas: List[str] = []

    for modulo, pacote, obrigatorio in DEPENDENCIAS:
        obrigatorio_agora = obrigatorio or (modulo == "customtkinter" and exigir_interface)
        try:
            __import__(modulo)
        except ImportError:
            marcador = "[FALTA]" if obrigatorio_agora else "[opcional]"
            problemas.append(f"{marcador} {pacote} — instale com: pip install {pacote}")

    return problemas


# ===========================================================================
# Modo linha de comando
# ===========================================================================

def executar_cli(args: argparse.Namespace) -> int:
    """
    Executa a pesquisa sem interface gráfica.

    Returns:
        Código de saída do processo (0 = sucesso).
    """
    # Importações tardias: só o modo CLI precisa delas.
    import excel
    import pesquisa
    import utils

    cfg = config.Configuracao.carregar()
    cfg.caminho_planilha = str(Path(args.planilha).expanduser().resolve())
    cfg.pasta_saida = str(
        Path(args.saida).expanduser().resolve()
        if args.saida
        else config.RAIZ_PROJETO / "saida"
    )
    if args.threads:
        cfg.max_workers = args.threads
    if args.delay_min is not None:
        cfg.delay_min = args.delay_min
    if args.delay_max is not None:
        cfg.delay_max = args.delay_max

    cfg.usar_google_maps = not args.sem_maps
    cfg.usar_playwright = cfg.usar_google_maps
    cfg.usar_google_search = not args.sem_busca
    cfg.usar_cnpj = not args.sem_cnpj
    cfg.usar_site_oficial = not args.sem_site
    cfg.headless = True

    problemas = cfg.validar()
    if problemas:
        for problema in problemas:
            print(f"ERRO: {problema}", file=sys.stderr)
        return 2

    pasta_saida = Path(cfg.pasta_saida)
    utils.configurar_logging(pasta_saida, logging.DEBUG if args.verboso else logging.INFO)
    log = logging.getLogger("localizador.main")

    # --- planilha -------------------------------------------------------
    planilha = excel.PlanilhaContatos(cfg.caminho_planilha, pasta_saida)
    try:
        planilha.abrir()
    except Exception as exc:
        print(f"ERRO ao abrir a planilha: {exc}", file=sys.stderr)
        return 3

    todas = planilha.empresas()
    pendentes = [
        e for e in todas if not excel.possui_telefone_valido(e.contato_existente)
    ]
    ignoradas = len(todas) - len(pendentes)

    if args.limite:
        pendentes = pendentes[: args.limite]

    if not pendentes:
        print("Nada a fazer: todas as empresas já possuem telefone.")
        planilha.fechar()
        return 0

    print(
        f"\n{config.APP_NOME} v{config.APP_VERSAO}\n"
        f"Planilha ....: {cfg.caminho_planilha}\n"
        f"Saída .......: {pasta_saida}\n"
        f"Empresas ....: {len(todas)} (pendentes: {len(pendentes)}, "
        f"ignoradas: {ignoradas})\n"
        f"Threads .....: {cfg.max_workers}\n"
        f"Fontes ......: "
        f"{'site ' if cfg.usar_site_oficial else ''}"
        f"{'maps ' if cfg.usar_google_maps else ''}"
        f"{'busca ' if cfg.usar_google_search else ''}"
        f"{'cnpj' if cfg.usar_cnpj else ''}\n"
    )

    # --- motor ----------------------------------------------------------
    registrador = pesquisa.RegistradorLog(pasta_saida / config.ARQ_LOG)

    def ao_progredir(estado: pesquisa.EstadoProgresso) -> None:
        """Imprime uma barra de progresso simples no terminal."""
        largura = 34
        preenchido = int(largura * estado.percentual)
        barra = "█" * preenchido + "░" * (largura - preenchido)
        sys.stdout.write(
            f"\r[{barra}] {estado.processadas}/{estado.total} "
            f"| ok {estado.encontradas} | sem {estado.sem_resultado} "
            f"| rev {estado.revisao_manual} | erro {estado.erros} "
            f"| restam ~{utils.formatar_duracao(estado.tempo_estimado)}   "
        )
        sys.stdout.flush()

    def ao_logar(nivel: str, mensagem: str) -> None:
        if nivel in ("ERRO", "AVISO") or args.verboso:
            sys.stdout.write("\r" + " " * 110 + "\r")
            print(f"{nivel}: {mensagem}")

    motor = pesquisa.MotorPesquisa(
        cfg=cfg,
        planilha=planilha,
        registrador=registrador,
        ao_progredir=ao_progredir,
        ao_logar=ao_logar,
    )

    def _interromper(_sig, _frame) -> None:
        print("\n\nCancelando… aguarde o encerramento seguro.")
        motor.cancelar()

    signal.signal(signal.SIGINT, _interromper)

    motor.iniciar(pendentes, ignoradas=ignoradas)
    while motor.executando:
        time.sleep(0.4)
    motor.aguardar(timeout=60)

    estado = motor.estado
    print(
        f"\n\nConcluído em {utils.formatar_duracao(estado.tempo_decorrido)}.\n"
        f"  Com contato .....: {estado.encontradas}\n"
        f"  Sem resultado ...: {estado.sem_resultado}\n"
        f"  Revisão manual ..: {estado.revisao_manual}\n"
        f"  Erros ...........: {estado.erros}\n"
        f"\nArquivos gerados em: {pasta_saida}"
    )

    try:
        planilha.fechar()
    except Exception as exc:
        log.error("Falha ao fechar a planilha: %s", exc)

    return 0


# ===========================================================================
# Modo gráfico
# ===========================================================================

def executar_gui() -> int:
    """Abre a interface gráfica. Returns: código de saída."""
    import utils

    utils.configurar_logging(config.RAIZ_PROJETO / "saida")

    try:
        import interface
    except ImportError as exc:
        print(
            "ERRO: não foi possível carregar a interface gráfica.\n"
            f"Detalhe: {exc}\n"
            "Instale as dependências com: pip install -r requirements.txt\n"
            "Ou execute em modo texto: python main.py --cli --planilha ARQUIVO.xlsx",
            file=sys.stderr,
        )
        return 4

    interface.executar()
    return 0


# ===========================================================================
# CLI
# ===========================================================================

def construir_parser() -> argparse.ArgumentParser:
    """Monta o parser de argumentos da linha de comando."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=f"{config.APP_NOME} v{config.APP_VERSAO}",
        epilog=(
            "Exemplos:\n"
            "  python main.py\n"
            '  python main.py --cli --planilha "carteira.xlsx"\n'
            "  python main.py --cli --planilha carteira.xlsx --limite 10 --threads 3\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--cli", action="store_true", help="executa sem interface gráfica")
    parser.add_argument("--planilha", help="caminho da planilha .xlsx (obrigatório no modo --cli)")
    parser.add_argument("--saida", help="pasta de saída (padrão: ./saida)")
    parser.add_argument("--threads", type=int, help=f"empresas simultâneas (padrão: {config.MAX_WORKERS})")
    parser.add_argument("--limite", type=int, help="processa apenas as N primeiras empresas pendentes")
    parser.add_argument("--delay-min", type=float, dest="delay_min", help="menor intervalo entre requisições (s)")
    parser.add_argument("--delay-max", type=float, dest="delay_max", help="maior intervalo entre requisições (s)")

    parser.add_argument("--sem-maps", action="store_true", help="não consultar o Google Maps")
    parser.add_argument("--sem-busca", action="store_true", help="não usar buscadores web")
    parser.add_argument("--sem-cnpj", action="store_true", help="não consultar CNPJ/Receita Federal")
    parser.add_argument("--sem-site", action="store_true", help="não rastrear sites oficiais")

    parser.add_argument("--verboso", action="store_true", help="log detalhado no terminal")
    parser.add_argument("--checar", action="store_true", help="apenas verifica as dependências e sai")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Função principal. Returns: código de saída do processo."""
    parser = construir_parser()
    args = parser.parse_args(argv)

    # Garante que o Windows não quebre ao imprimir acentos/emoji no console.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    problemas = verificar_dependencias(exigir_interface=not args.cli)

    if args.checar:
        print(f"{config.APP_NOME} v{config.APP_VERSAO} — verificação de dependências\n")
        if not problemas:
            print("Todas as dependências estão instaladas.")
        else:
            for problema in problemas:
                print(" ", problema)
        return 0 if not any(p.startswith("[FALTA]") for p in problemas) else 1

    faltando = [p for p in problemas if p.startswith("[FALTA]")]
    if faltando:
        print("Dependências obrigatórias ausentes:\n", file=sys.stderr)
        for problema in faltando:
            print(" ", problema, file=sys.stderr)
        print("\nInstale tudo de uma vez: pip install -r requirements.txt", file=sys.stderr)
        return 1

    for problema in problemas:
        print(f"Aviso: {problema}", file=sys.stderr)

    if args.cli:
        if not args.planilha:
            parser.error("--planilha é obrigatório no modo --cli")
        return executar_cli(args)

    return executar_gui()


if __name__ == "__main__":
    sys.exit(main())
