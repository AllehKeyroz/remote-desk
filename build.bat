@echo off
rem Gera o executavel do Meu Controle Remoto - KVM (compartilhamento teclado/mouse)
cd /d "%~dp0"
pip install -r requirements.txt pyinstaller

rem App (mesmo exe para os dois PCs)
pyinstaller --noconsole --onefile --name "MeuControle" ^
  --hidden-import pydirectinput ^
  kvm_input.py app.py

rem Relay (servidor de rendezvous - roda em VPS)
pyinstaller --noconsole --onefile --name "MeuControle-Relay" ^
  relay.py

echo.
echo EXEs gerados em: dist\
echo   MeuControle.exe      - o app KVM (copie para os dois PCs)
echo   MeuControle-Relay.exe - o servidor (se nao usar o VPS)
pause