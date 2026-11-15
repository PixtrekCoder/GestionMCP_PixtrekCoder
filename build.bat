@echo off
REM Compile gestion_mcp.py en un exécutable Windows unique.
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean Gestionnaire-MCP-v1.0.spec
echo.
echo Executable : %~dp0dist\Gestionnaire-MCP-v1.0.exe
pause
