import asyncio, json, websockets
URL = 'wss://dev-kds-sistemas-remote-desk.aoi8gd.easypanel.host/ws'

async def main():
    async with websockets.connect(URL, open_timeout=15) as ws:
        await ws.send(json.dumps({'type': 'register', 'id': 'TEST-REAL-01'}))
        print('Registrei TEST-REAL-01:', (await asyncio.wait_for(ws.recv(), 10)))
        await ws.send(json.dumps({'type': 'connect', 'target': '84ML-JPSX'}))
        print('Pedi controle do 84ML-JPSX. Aguardando resposta do relay...')
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=12))
            print('Resposta do relay:', msg)
            if msg.get('type') == 'session_start':
                print('!!! ACEITO! Recebendo frames agora...')
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=8)
                    print('  Frame recebido do dispositivo:', len(frame) if isinstance(frame, bytes) else frame)
                except Exception as e:
                    print('  Nao chegou frame:', type(e).__name__)
        except asyncio.TimeoutError:
            print('NENHUMA resposta em 12s.')
            print('O relay ENCAMINHOU o pedido pro 84ML-JPSX (ou seja, ele esta ONLINE).')
            print('Mas o dispositivo NAO respondeu -> a conexao esta esperando o clique em ACEITAR la no 84ML-JPSX.')
            print('Se fosse OFFLINE, o relay teria respondido "ID nao encontrado" na hora.')

asyncio.run(main())