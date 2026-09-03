@echo off
rem Gera o executavel do Meu Controle Remoto (interface tkinter, sem WebView2)
cd /d "%~dp0"
pip install -r requirements.txt pyinstaller

rem App (mesmo exe para os dois PCs)
pyinstaller --noconsole --onefile --name "MeuControle" ^
  --hidden-import mss --hidden-import pydirectinput ^
  --collect-all PIL ^
  app.py

rem Relay (servidor de rendezvous - roda em VPS ou PC com IP publico)
pyinstaller --noconsole --onefile --name "MeuControle-Relay" ^
  relay.py

echo.
echo EXEs gerados em: dist\
echo   MeuControle.exe      - o app (copie para os dois PCs)
echo   MeuControle-Relay.exe - o servidor (opcional, se nao usar VPS)
pause