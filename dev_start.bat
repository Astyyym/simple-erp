@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 同 setup.bat：只看 .venv\Scripts\python.exe，避免 Linux/WSL 的 .venv 让这里误判。
if not exist .venv\Scripts\python.exe (
  if exist .venv rmdir /s /q .venv
  py -V:Astral/CPython3.11.15 -m venv .venv
  if errorlevel 1 py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
python app\app.py
