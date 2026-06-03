@echo off
cd /d "%~dp0.."
call venv\Scripts\activate.bat
python -m waitress --listen=0.0.0.0:5000 app:app
pause
