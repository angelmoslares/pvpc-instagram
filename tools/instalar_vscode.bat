@echo off
REM Copia la configuracion de VS Code del proyecto a .vscode\
cd /d "%~dp0.."
if not exist .vscode mkdir .vscode
copy /Y tools\vscode\*.json .vscode\ >nul
echo Configuracion de VS Code instalada en .vscode\
dir /b .vscode
pause
