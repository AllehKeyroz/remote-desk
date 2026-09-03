# Meu Controle Remoto

Controle remoto **100% próprio**, no estilo AnyDesk: um único app, cada
dispositivo tem seu ID, e você digita o ID do outro para controlar (com o
outro dispositivo **aceitando** a conexão).

## Como funciona

```
[PC A]  ──app──▶  [Relay]  ◀──app──  [PC B]
 ID: K7X2-9M4P    (ponte)    ID: 9P4M-X2K7
```

- **App** (`MeuControle.exe`): o mesmo exe roda nos dois PCs. Mostra seu ID,
  aceita conexões e controla outros dispositivos.
- **Relay** (`MeuControle-Relay.exe`): servidor de rendezvous que faz a ponte.
  Pode rodar **embutido no app** (config `run_relay: true`) ou em qualquer
  máquina com IP público (VPS, etc.). Não depende da rede de quem usa.

## Uso (fluxo AnyDesk)

1. Os dois PCs abrem o **mesmo app**.
2. Cada um vê seu **ID** na tela.
3. Para controlar: digite o **ID do outro PC** e clique em Conectar.
4. O outro PC recebe o pedido e clica em **Aceitar**.
5. Pronto — você controla a tela dele (mouse, teclado, scroll).
6. Funciona nos **dois sentidos** (o outro PC também pode controlar o seu).

## Setup

### PC que roda o relay (ex: este PC)

1. Copie `MeuControle.exe` + `config.json` para uma pasta.
2. No `config.json`:
   ```json
   {
     "relay": "ws://localhost:8765",
     "run_relay": true,
     "id": "",
     "auto_accept": false
   }
   ```
   - `run_relay: true` → o relay roda embutido neste PC.
   - `id` vazio → gera um ID automático no primeiro uso.
3. Abra o app. Ele mostra seu ID e fica aguardando.

### Outro PC (controlado)

1. Copie `MeuControle.exe` + `config.json` para uma pasta.
2. No `config.json`:
   ```json
   {
     "relay": "ws://SEU-IP-PUBLICO:8765",
     "run_relay": false,
     "id": "",
     "auto_accept": false
   }
   ```
   - `relay`: endereço do relay (IP público do PC que roda o relay, ou IP do
     VPS). Existe um pronto em `config.other-pc.json`.
   - `run_relay: false` → não roda relay local.
3. Abra o app. Ele gera seu próprio ID.

### Relay em VPS (para acessar de qualquer lugar)

```bash
# no VPS (Linux)
python relay.py --port 8765
# ou rode MeuControle-Relay.exe no Windows
```

Depois configure o `relay` dos apps para `ws://IP-DO-VPS:8765`.

## Rede (como o AnyDesk — sem port forwarding)

O **método recomendado** é rodar o relay num **servidor público (VPS)**.
Os apps **sempre conectam pra fora** até ele, então **nenhum** port forwarding
é necessário, em nenhum PC, e funciona de **qualquer lugar** (clientes,
escritórios, casa). É exatamente o modelo do AnyDesk.

**Deploy num VPS (EasyPanel / GitHub + Dockerfile):** veja o guia completo
em `DEPLOY.md`. O relay sobe como container, os apps apontam pra ele:

```json
{ "relay": "ws://SEU-DOMINIO:PORT", "run_relay": false }
```

### Alternativa: port forwarding (sem VPS)

Só se você mantiver o relay num PC atrás de roteador. Libere a porta 8765 no
roteador (ex: `http://192.168.1.1` → Port Forwarding → `8765` → IP do PC →
`8765` TCP). O outro PC conecta em `ws://IP-PUBLICO:8765`. Isso amarra àquela
rede — não serve pra "qualquer cliente" fora dali.

## Configurações do app

| Campo | Descrição |
|---|---|
| `relay` | Endereço do servidor relay |
| `run_relay` | `true` roda o relay embutido neste PC |
| `relay_port` | Porta do relay embutido (padrão 8765) |
| `id` | ID deste dispositivo (vazio = gera automático) |
| `auto_accept` | `true` aceita conexões sem pedir confirmação |
| `quality` | Qualidade JPEG do stream (30-95) |
| `fps` | Quadros por segundo (5-30) |
| `max_width` | Largura máxima do stream (reduz banda) |

O endereço do relay também pode ser alterado pela interface
(Configurações → Salvar servidor).

## Rebuild dos executáveis

```bat
build.bat
```

## Solução de problemas

| Problema | Causa provável |
|---|---|
| App mostra "offline" | Relay inacessível — confira o endereço e o port forwarding |
| "ID nao encontrado" | O outro PC não está com o app aberto/online |
| "dispositivo ocupado" | O outro PC já está numa sessão |
| Tela lenta | Reduza FPS/qualidade no app |
| Porta 8765 ocupada | Mude `relay_port` e a porta no relay |