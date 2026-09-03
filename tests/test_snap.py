import asyncio, json, websockets
URL = 'wss://dev-kds-sistemas-remote-desk.aoi8gd.easypanel.host/ws'

async def main():
    async with websockets.connect(URL, open_timeout=15) as ws:
        await ws.send(json.dumps({'type': 'register', 'id': 'TEST-SNAP-01'}))
        await ws.recv()
        await ws.send(json.dumps({'type': 'connect', 'target': '84ML-JPSX'}))
        start = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        if start.get('type') != 'session_start':
            print('Nao aceitou:', start); return
        print('>>> ACEITO. Coletando 2 frames do dispositivo...')
        saved = 0
        while saved < 2:
            m = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(m, bytes):
                with open(rf'C:\Users\User\meucontrole\tests\device_frame_{saved}.jpg', 'wb') as f:
                    f.write(m)
                print('  Frame', saved, 'salvo:', len(m), 'bytes')
                saved += 1
        print('>>> Frames salvos. Analisar em tests/device_frame_*.jpg')

asyncio.run(main())