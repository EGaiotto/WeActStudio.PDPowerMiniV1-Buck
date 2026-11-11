@echo off
rem So o servidor HTTP + MCP (interface em http://127.0.0.1:8765 no navegador)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pdpower.app --headless %*
