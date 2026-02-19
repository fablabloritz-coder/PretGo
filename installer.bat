@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title Installation - PretGo

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║         INSTALLATION - PretGo                   ║
echo  ╚══════════════════════════════════════════════════╝
echo.

REM ── Configuration ──────────────────────────────────────
set PYTHON_VERSION=3.13.9
set PYTHON_DIR=%~dp0python
set PYTHON_EXE=%PYTHON_DIR%\python.exe
set PYTHON_ZIP=python-%PYTHON_VERSION%-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_ZIP%

REM ── Vérifier si déjà installé ─────────────────────────
if exist "%PYTHON_EXE%" (
    echo  [OK] Python embarqué déjà présent.
    "%PYTHON_EXE%" -c "import flask" >nul 2>&1
    if not errorlevel 1 (
        echo  [OK] Flask déjà installé.
        echo.
        echo  L'installation est déjà complète !
        echo  Lancez l'application avec "lancer.bat"
        echo.
        pause
        exit /b 0
    )
    echo  Flask n'est pas encore installé, reprise...
    echo.
    goto install_pip
)

REM ── Étape 1 : Téléchargement ──────────────────────────
echo  [1/4] Téléchargement de Python %PYTHON_VERSION%...
echo         Cela peut prendre quelques minutes...
echo.
powershell -Command "& { try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%~dp0%PYTHON_ZIP%' -UseBasicParsing } catch { Write-Host '  [ERREUR]' $_.Exception.Message; exit 1 } }"
if errorlevel 1 (
    echo.
    echo  [ERREUR] Impossible de télécharger Python.
    echo  Vérifiez votre connexion Internet et réessayez.
    echo.
    pause
    exit /b 1
)
echo  [OK] Python téléchargé.
echo.

REM ── Étape 2 : Extraction ──────────────────────────────
echo  [2/4] Extraction de Python...
if exist "%PYTHON_DIR%" rmdir /s /q "%PYTHON_DIR%"
powershell -Command "Expand-Archive -Path '%~dp0%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
if errorlevel 1 (
    echo  [ERREUR] Extraction échouée.
    pause
    exit /b 1
)
del "%~dp0%PYTHON_ZIP%" >nul 2>&1
echo  [OK] Python extrait.
echo.

REM ── Étape 3 : Configuration pip ───────────────────────
:install_pip
echo  [3/4] Installation de pip...

REM Activer pip en décommentant "import site" dans le fichier ._pth
for %%f in ("%PYTHON_DIR%\python*._pth") do (
    powershell -Command "(Get-Content '%%f') -replace '#import site','import site' | Set-Content '%%f'"
)

REM Télécharger et installer pip
powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%~dp0get-pip.py' -UseBasicParsing }"
if errorlevel 1 (
    echo  [ERREUR] Impossible de télécharger pip.
    pause
    exit /b 1
)
"%PYTHON_EXE%" "%~dp0get-pip.py" --no-warn-script-location >nul 2>&1
del "%~dp0get-pip.py" >nul 2>&1
echo  [OK] pip installé.
echo.

REM ── Étape 4 : Installation de Flask ───────────────────
echo  [4/4] Installation de Flask...
"%PYTHON_EXE%" -m pip install -r "%~dp0requirements.txt" --no-warn-script-location -q
if errorlevel 1 (
    echo  [ERREUR] Installation de Flask échouée.
    pause
    exit /b 1
)
echo  [OK] Flask installé.
echo.

REM ── Terminé ────────────────────────────────────────────
echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║         INSTALLATION TERMINÉE !                  ║
echo  ╠══════════════════════════════════════════════════╣
echo  ║                                                  ║
echo  ║  Pour lancer l'application, double-cliquez       ║
echo  ║  sur le fichier "lancer.bat"                     ║
echo  ║                                                  ║
echo  ╚══════════════════════════════════════════════════╝
echo.
pause
