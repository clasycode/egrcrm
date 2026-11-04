@echo off
setlocal
cd /d "%~dp0"
title EGR CRM - local development
where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.12 from python.org and restart this file.
  pause
  exit /b 1
)
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo Python 3.12 is required. Install it from python.org.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv
  if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
if not defined CRM_DATA_DIR set "CRM_DATA_DIR=%LOCALAPPDATA%\RudyCortex\EGRCRM"
.venv\Scripts\python.exe prepare_local.py
if errorlevel 1 goto fail
set /p DJANGO_SECRET_KEY=<"%CRM_DATA_DIR%\.local-key"
set DEBUG=1
set PYTHONUNBUFFERED=1
set ALLOWED_HOSTS=localhost,127.0.0.1
.venv\Scripts\python.exe manage.py migrate
if errorlevel 1 goto fail
.venv\Scripts\python.exe manage.py shell -c "from django.contrib.auth import get_user_model; import sys; sys.exit(0 if get_user_model().objects.filter(is_active=True,is_superuser=True).exists() else 1)"
if errorlevel 1 (
  echo Create your CRM owner. Password input is invisible; this is normal.
  .venv\Scripts\python.exe manage.py createsuperuser
  if errorlevel 1 goto fail
)
echo.
echo Open http://127.0.0.1:8000 in your browser after the server starts.
echo Keep this window open. Press Ctrl+C to stop.
echo This is a local test server, not a public deployment.
.venv\Scripts\python.exe manage.py run_local
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Startup failed. Copy the error above and send it in the chat. Do not send .local-key.
pause
exit /b 1
