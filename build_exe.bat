@echo off
cd /d "%~dp0"
echo === Snake: build Snake.exe ===
set PYV=3.13
py -%PYV% --version >nul 2>&1 || set PYV=3.12
py -%PYV% --version >nul 2>&1 || (echo Python 3.13 or 3.12 not found. Install Python 3.13 from python.org first. & pause & exit /b 1)
echo Using Python %PYV%
py -%PYV% -m pip install --upgrade pygame pyinstaller
if errorlevel 1 (echo pip failed. Check the message above. & pause & exit /b 1)
py -%PYV% -c "import pygame; print(\"pygame\", pygame.version.ver, \"OK\")"
if errorlevel 1 (echo pygame cannot be loaded. Check the message above. & pause & exit /b 1)
if exist build rmdir /s /q build
if exist dist\Snake.exe del /q dist\Snake.exe
py -%PYV% -m PyInstaller --noconfirm --clean --onefile --windowed --icon snake.ico --collect-all pygame --exclude-module tkinter --name Snake snake.py
if errorlevel 1 (echo Build failed. Check the message above. & pause & exit /b 1)
echo.
echo Done! Your game is here: dist\Snake.exe
echo Settings and high scores are stored in %%APPDATA%%\Snake
pause
