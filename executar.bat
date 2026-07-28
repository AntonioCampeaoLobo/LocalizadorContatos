@echo off
REM ---------------------------------------------------------------------------
REM Localizador de Contatos Empresariais - atalho de execucao (Windows)
REM
REM Usa o ambiente virtual .venv se existir; caso contrario, o Python do sistema.
REM Argumentos extras sao repassados: executar.bat --cli --planilha "arquivo.xlsx"
REM ---------------------------------------------------------------------------

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    python main.py %*
)

if errorlevel 1 (
    echo.
    echo A aplicacao terminou com erro. Verifique as mensagens acima.
    pause
)
