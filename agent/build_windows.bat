@echo off
setlocal enabledelayedexpansion
REM build_windows.bat — Сборка агента для Windows в .exe
REM Запускать в командной строке или PowerShell (.\build_windows.bat)

echo === Определение интерпретатора Python ===
set PYTHON=py -3.13
where py >nul 2>nul
if %errorlevel% neq 0 (
    set PYTHON=python
)

echo === Проверка и установка зависимостей ===
%PYTHON% -m pip install -r requirements-tray.txt pyinstaller

echo === Сборка PyInstaller (SAM-VKO-Agent.spec) ===
%PYTHON% -m PyInstaller --clean --noconfirm SAM-VKO-Agent.spec
if %errorlevel% neq 0 (
    echo [ОШИБКА] Сборка завершилась с ошибкой!
    exit /b %errorlevel%
)

echo.
echo === Готово! ===
echo Файл: dist\SAM-VKO-Agent.exe
echo.
echo Установить автозапуск (добавляет в реестр):
echo   dist\SAM-VKO-Agent.exe --install-autostart
echo.
echo Запустить:
echo   dist\SAM-VKO-Agent.exe
