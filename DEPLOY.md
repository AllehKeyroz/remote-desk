# Deploy do Relay no EasyPanel (GitHub + Dockerfile)

O relay é um serviço pequeno e stateless. Você sobe ele no seu VPS via
EasyPanel usando o Dockerfile, e todos os apps (o seu PC e os PCs de clientes)
conectam pra ele. **Nenhum port forwarding** em nenhum PC — só os apps
conectarem pra fora até o VPS.

## Arquivos necessários no repo

```
relay.py          # o servidor relay
Dockerfile        # build da imagem (python:3.12-slim + aiohttp)
.dockerignore     # ignora tudo que não precisa na imagem
```

Tudo já está criado em `C:\Users\User\meucontrole\`.

## Passo a passo

### 1. Suba pro GitHub

Na pasta `C:\Users\User\meucontrole\`, envie os 3 arquivos pro seu repo:

```bash
git init
git add relay.py Dockerfile .dockerignore
git commit -m "relay: servidor de rendezvous do Meu Controle Remoto"
git remote add origin https://github.com/SEU-USUARIO/SEU-REPO.git
git push -u origin main
```

> Pode enviar só esses 3 arquivos ou o projeto inteiro — o `.dockerignore`
> garante que só o necessário vá pra imagem.

### 2. Crie o app no EasyPanel

1. No EasyPanel, vá em **Apps → Create App**.
2. Escolha **GitHub** e selecione o repo/branch.
3. Deixe o **Dockerfile** detectado automaticamente.
4. Build & Deploy.

### 3. Exponha a porta

O relay escuta na porta **8765** (definida no Dockerfile).

1. No app criado, abra **Network / Ports**.
2. Exponha a **porta privada 8765** — EasyPanel vai mapear pra uma
   porta pública ou domínio.
3. Copie o endereço público resultante, ex: `ws://SEU-DOMINIO:PORT` ou
   `ws://IP-DO-VPS:PORT`.

> Se o EasyPanel usar HTTPS/domínio, o app aceita tanto `ws://` quanto `wss://`.

### 4. Teste o relay

A partir de qualquer máquina (PowerShell):

```powershell
# troque pela porta/dominio publico do seu relay
curl http://SEU-DOMINIO:PORT/
```

Deve retornar **404** (é o correto — o relay só tem a rota `/ws`), o que
confirma que o servidor está no ar.
Se retornar erro de conexão/refused, a porta não está exposta ou o container
não subiu.

Para testar o handshake WebSocket:

```powershell
# instale websockets uma vez: pip install websockets
python -c "import asyncio,websockets; asyncio.run(websockets.connect('ws://SEU-DOMINIO:PORT/ws'))"
```

### 5. Aponte os apps pro relay

Em CADA PC (o seu e os de clientes), edite o `config.json`:

```json
{
  "relay": "ws://SEU-DOMINIO:PORT",
  "run_relay": false,
  "id": "",
  "auto_accept": false
}
```

Pronto. Agora funciona de **qualquer lugar**, com **qualquer cliente**, sem
mexer em roteador nunca mais. É o modelo AnyDesk.

## Escala

- O relay aceita **N dispositivos** simultâneos (cada um com seu ID).
- Pode haver **múltiplas sessões** ao mesmo tempo (PCs diferentes sendo
  controlados).
- 1 relay serve todos os seus clientes. Não precisa de um por cliente.
