@echo off
REM build_windows.bat — Сборка агента для Windows в .exe
REM Запускать в командной строке на Windows-машине

echo === Установка зависимостей ===
pip install --quiet -r requirements-tray.txt pyinstaller

echo === Сборка PyInstaller ===
pyinstaller --onefile ^
  --noconsole ^
  --name "SAM-VKO-Agent" ^
  --hidden-import "pystray._win32" ^
  --hidden-import "PIL.ImageFont" ^
  --hidden-import "PIL.ImageDraw" ^
  --hidden-import "tkinter" ^
  --hidden-import "tkinter.ttk" ^
  --hidden-import "speedtest" ^
  --add-data "." ^
  tray_agent.py

echo.
echo === Готово! ===
echo Файл: dist\SAM-VKO-Agent.exe
echo.
echo Установить автозапуск (добавляет в реестр):
echo   dist\SAM-VKO-Agent.exe --install-autostart
echo.
echo Запустить:
echo   dist\SAM-VKO-Agent.exe
pause
