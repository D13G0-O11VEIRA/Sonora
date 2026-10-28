@echo off
echo === Sonora: criando ambiente virtual ===
python -m venv .venv || (echo Python nao encontrado. Instale em python.org & pause & exit /b 1)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Pronto! Para abrir o Sonora de duplo clique use run.bat
pause
