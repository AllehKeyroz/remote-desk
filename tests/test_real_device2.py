import asyncio, json, websockets
URL = 'wss://dev-kds-sistemas-remote-desk.aoi8gd.easypanel.host/ws'

async def main():
    async with websockets.connect(URL, open_timeout=15) as ws:
        await ws.send(json.dumps({'type': 'register', 'id': 'TEST-REAL-02'}))
        await ws.recv()
        await ws.send(json.dumps({'type': 'connect', 'target': '84ML-JPSX'}))
        print('Pedi controle do 84ML-JPSX...')
        try:
            start = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            print('Resposta:', start)
            if start.get('type') != 'session_start':
                print('Nao foi aceito:', start)
                return
            print('>>> ACEITO. Recebendo dados do dispositivo por 6 segundos...')
            frames = 0
            infos = 0
            end = asyncio.get_event_loop().time() + 6
            while asyncio.get_event_loop().time() < end:
                try:
                    m = await asyncio.wait_for(ws.recv(), timeout=6)
                except asyncio.TimeoutError:
                    break
                if isinstance(m, bytes):
                    frames += 1
                    if frames == 1:
                        print('  Primeiro FRAME (video) recebido:', len(m), 'bytes')
                else:
                    d = json.loads(m)
                    if d.get('type') == 'screen_info':
                        infos += 1
                # envia input de teste a cada 1s
        except asyncio.TimeoutError:
            print('Nao aceitou em 20s (aguardando clique em Aceitar).')
            return

        # envia inputs de teste
        print(f'>>> Recebidos {frames} frames de video + {infos} screen_info')
        for i, evt in enumerate([
            {'type': 'mouse_move', 'x': 400, 'y': 300},
            {'type': 'mouse_down', 'button': 'left'},
            {'type': 'mouse_up', 'button': 'left'},
        ]):
            await ws.send(json.dumps(evt))
            await asyncio.sleep(0.3)
        print('>>> Enviei mouse_move + clique para o 84ML-JPSX')

        # continua recebendo frames para confirmar que segue vivo
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=6)
            ok = 'REAL: video FLUI (frame)' if isinstance(m, bytes) else 'REAL: chegou ' + str(m)[:40]
            print('>>> Ainda recebendo:', ok)
        except asyncio.TimeoutError:
            print('>>> Parou de receber (dispositivo desconectou?)')

        print('=== TESTE REAL CONCLUIDO ===')

asyncio.run(main())