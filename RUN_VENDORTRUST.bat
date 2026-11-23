@echo off
cd /d "%~dp0"
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo Python 3.12 was not found. Install Python 3.12 and run this file again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo Could not create virtual environment.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Dependency installation failed.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run app.py
pause
