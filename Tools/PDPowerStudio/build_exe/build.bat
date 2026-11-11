@echo off
rem Recompila dist\PDPower Studio.exe
cd /d "%~dp0\.."
".venv\Scripts\python.exe" -m pip install -q pyinstaller pillow
".venv\Scripts\python.exe" build_exe\make_icon.py
".venv\Scripts\pyinstaller.exe" build_exe\pdpower_studio.spec --noconfirm --distpath dist --workpath build_exe\work
echo.
echo Pronto: dist\PDPower Studio.exe
pause
