@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo Для запуска нужен Python 3.12 с компонентом Tcl/Tk.
  echo Установите Python 3.12 с python.org, затем повторите запуск.
  pause
  exit /b 1
)
start "RudyCortex" pyw -3.12 "%~dp0start_desktop.pyw"
