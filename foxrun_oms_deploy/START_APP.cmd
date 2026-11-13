@echo off
setlocal EnableExtensions
pushd "%~dp0" || exit /b 1

where py >nul 2>&1
if errorlevel 1 (
    echo [LOI] Chua tim thay Python.
    echo Hay cai Python 3.11 hoac 3.12 va chon Add Python to PATH.
    popd
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Dang tao moi truong Python...
    py -3 -m venv .venv
    if errorlevel 1 goto :FAILED
)

echo [2/3] Dang cai/cap nhat thu vien...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :FAILED

echo [3/3] Dang mo FOXRUN OMS...
".venv\Scripts\python.exe" -B -X utf8 run_local.py
set "APP_EXIT=%ERRORLEVEL%"
popd
if not "%APP_EXIT%"=="0" pause
exit /b %APP_EXIT%

:FAILED
echo.
echo [LOI] Khong the cai dat hoac khoi dong ung dung.
echo Hay chup man hinh Terminal va gui lai de kiem tra.
popd
pause
exit /b 1
