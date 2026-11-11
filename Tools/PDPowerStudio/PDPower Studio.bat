@echo off
rem Abre o PDPower Studio (janela nativa). MCP em http://127.0.0.1:8765/mcp
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Criando ambiente virtual...
  python -m venv .venv || goto :err
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)
start "" ".venv\Scripts\pythonw.exe" -m pdpower.app %*
exit /b 0
:err
echo Falha ao preparar o ambiente. Verifique se o Python 3.11+ esta instalado.
pause
