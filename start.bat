@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Remote PC
if exist ".venv\Scripts\python.exe" goto run
echo Installation - premiere fois uniquement...
python -m venv .venv
if errorlevel 1 goto nopython
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
:run
".venv\Scripts\python.exe" server.py %*
pause
exit /b
:nopython
echo Python introuvable. Installe-le depuis python.org
:fail
pause
