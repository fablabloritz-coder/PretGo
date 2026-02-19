@echo off
chcp 65001 >nul
setlocal
title PretGo

set PYTHON_EXE=%~dp0python\python.exe

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║                 PretGo                           ║
echo  ╚══════════════════════════════════════════════════╝
echo.

REM ── Vérifier que Python embarqué est présent ──────────
if not exist "%PYTHON_EXE%" (
    echo  [ERREUR] Python embarqué introuvable.
    echo.
    echo  Veuillez d'abord exécuter "installer.bat"
    echo.
    pause
    exit /b 1
)

echo  Démarrage du serveur...
echo  L'application va s'ouvrir dans votre navigateur.
echo.
echo  ┌──────────────────────────────────────────────────┐
echo  │  NE FERMEZ PAS cette fenêtre tant que vous       │
echo  │  utilisez l'application !                        │
echo  │                                                  │
echo  │  Pour arrêter : fermez cette fenêtre             │
echo  │  ou appuyez sur Ctrl+C                           │
echo  └──────────────────────────────────────────────────┘
echo.

REM Ouvrir le navigateur après un court délai
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5000"

REM Lancer l'application Flask avec le Python embarqué
cd /d "%~dp0"
"%PYTHON_EXE%" app.py

echo.
echo  Le serveur s'est arrêté.
pause
