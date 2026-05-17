@echo off
echo Instalando dependencias...
pip install -r requirements.txt
echo.
echo Iniciando AgroFrut...
echo Acesse: http://localhost:5000
echo.
python app.py
pause
