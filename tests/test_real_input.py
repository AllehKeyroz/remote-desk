import asyncio, io, json, websockets
from PIL import Image

URL = 'wss://dev-kds-sistemas-remote-desk.aoi8gd.easypanel.host/ws'

def sig(b):
    # assinatura da imagem: reduz para 32x16, media em um grayscale simples
    try:
        img = Image.open(io.BytesIO(b)).convert('L').resize((32, 16))
        return list(img.getdata())
    except Exception:
        return None

def diff(a, b):
    if a is None or b is None or len(a) != len(b):
        return None
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)

async def main():
    async with websockets.connect(URL, open_timeout=15) as ws:
        await ws.send(json.dumps({'type': 'register', 'id': 'TEST-INPUT-01'}))
        await ws.recv()
        await ws.send(json.dumps({'type': 'connect', 'target': '84ML-JPSX'}))
        start = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
        if start.get('type') != 'session_start':
            print('Nao aceitou:', start); return
        print('>>> ACEITO. Coletando frames baseline (tela parada, sem input)...')

        # baseline: 4 frames seguidos, mede ruido natural (animacoes etc)
        baseline = []
        for _ in range(6):
            m = await asyncio.wait_for(ws.recv(), timeout=8)
            if isinstance(m, bytes):
                s = sig(m)
                if s: baseline.append(s)
        # ruido natural = diff medio entre frames baseline consecutivos
        noise = []
        for i in range(1, len(baseline)):
            d = diff(baseline[i-1], baseline[i])
            if d is not None: noise.append(d)
        noise = sum(noise)/len(noise) if noise else 0
        print(f'  Ruido natural da tela (sem input): {noise:.2f}')
        base_ref = baseline[-1]

        # ---- TESTE DE TECLADO: tecla Windows (abre menu, mudanca grande) ----
        print('>>> Enviando tecla Windows (Meta down/up)...')
        await ws.send(json.dumps({'type': 'key_down', 'code': 'MetaLeft'}))
        await asyncio.sleep(0.2)
        await ws.send(json.dumps({'type': 'key_up', 'code': 'MetaLeft'}))
        await asyncio.sleep(1.0)
        # compara frames pos-tecla com baseline
        post = []
        for _ in range(4):
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=6)
            except asyncio.TimeoutError:
                break
            if isinstance(m, bytes):
                s = sig(m)
                if s: post.append(s)
        poss_diff = [diff(base_ref, p) for p in post]
        poss_diff = [d for d in poss_diff if d is not None]
        max_d = max(poss_diff) if poss_diff else 0
        print(f'  Maior mudanca apos a tecla Windows: {max_d:.2f}')
        keyboard_worked = max_d > noise * 3 and max_d > 8
        print(f'  => TECLADO {"FUNCIONOU (tela mudou)" if keyboard_worked else "SEM efeito detectavel"}')

        # ---- TESTE DE MOUSE: move + clique ----
        print('>>> Enviando mouse_move para (683,400) + clique...')
        await ws.send(json.dumps({'type': 'mouse_move', 'x': 683, 'y': 400}))
        await ws.send(json.dumps({'type': 'mouse_down', 'button': 'left'}))
        await ws.send(json.dumps({'type': 'mouse_up', 'button': 'left'}))
        await asyncio.sleep(0.8)
        print('  Mouse enviado (move + clique). Confirma visualmente na tela do dispositivo.')

        # continua recebendo para ver se host vivo
        try:
            m = await asyncio.wait_for(ws.recv(), timeout=6)
            print('  Host continua mandando video =', isinstance(m, bytes))
        except asyncio.TimeoutError:
            print('  Host parou de mandar video')

        print('=== TESTE DE INPUT (teclado + mouse) CONCLUIDO ===')

asyncio.run(main())