@echo off
rem ─────────────────────────────────────────────────────────────────────────
rem  Background Check (BGC) — lance l'interface depuis les sources (sans fenêtre noire).
rem
rem  Ordre de recherche de l'interpréteur :
rem    1. le lanceur Python « pyw » (installé avec Python pour Windows) ;
rem    2. pythonw.exe trouvé dans le PATH.
rem  Test en ligne de commande :  py background_check.py --selftest
rem ─────────────────────────────────────────────────────────────────────────
setlocal
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0background_check.py" %*
    exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0background_check.py" %*
    exit /b 0
)
echo Python introuvable : installe Python pour Windows (python.org) puis relance.
pause
exit /b 1
