@echo off
REM Arranca PARKA Deposito en esta PC (http://localhost:8100)
cd /d %~dp0
if not exist .venv (
  echo Creando entorno de Python...
  python -m venv .venv
  call .venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate
)
if not exist .env copy .env.example .env
echo.
echo  PARKA Deposito corriendo en http://localhost:8100
echo  Desde un celular en la misma red: http://IP-DE-ESTA-PC:8100
echo.
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
