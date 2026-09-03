"""Teste do relay: simula dois apps (A controla B) e valida o fluxo completo."""
import asyncio
import json

import websockets


async def peer(name, relay_url, on_msg):
    ws = await websockets.connect(f"{relay_url}/ws", max_size=8 * 1024 * 1024)
    await ws.send(json.dumps({"type": "register", "id": name}))
    reg = json.loads(await ws.recv())
    assert reg["type"] == "registered", reg
    return ws


async def main():
    relay_url = "ws://localhost:8765"

    # A e B registram
    ws_a = await peer("AAAA-AAAA", relay_url, None)
    ws_b = await peer("BBBB-BBBB", relay_url, None)
    print("A e B registrados")

    # A quer controlar B
    await ws_a.send(json.dumps({"type": "connect", "target": "BBBB-BBBB"}))
    incoming = json.loads(await ws_b.recv())
    assert incoming == {"type": "incoming", "from": "AAAA-AAAA"}, incoming
    print("B recebeu pedido de controle de A:", incoming)

    # B aceita
    await ws_b.send(json.dumps({"type": "accept", "from": "AAAA-AAAA"}))
    start_a = json.loads(await ws_a.recv())
    start_b = json.loads(await ws_b.recv())
    assert start_a["type"] == "session_start" and start_a["role"] == "controller", start_a
    assert start_b["type"] == "session_start" and start_b["role"] == "host", start_b
    print("Sessao iniciada: A=controller, B=host")

    # A envia input -> B recebe
    await ws_a.send(json.dumps({"type": "mouse_move", "x": 100, "y": 200}))
    got = json.loads(await ws_b.recv())
    assert got == {"type": "mouse_move", "x": 100, "y": 200}, got
    print("Input de A chegou em B:", got)

    # B envia frame (binario) -> A recebe
    fake_frame = b"\xff\xd8fakejpeg\xff\xd9"
    await ws_b.send(fake_frame)
    got_frame = await ws_a.recv()
    assert got_frame == fake_frame, "frame nao bateu"
    print(f"Frame de B chegou em A ({len(got_frame)} bytes)")

    # B envia screen_info -> A recebe
    await ws_b.send(json.dumps({"type": "screen_info", "width": 1920, "height": 1080}))
    got_info = json.loads(await ws_a.recv())
    assert got_info["type"] == "screen_info", got_info
    print("screen_info de B chegou em A:", got_info)

    # A desconecta -> B recebe session_end
    await ws_a.send(json.dumps({"type": "disconnect"}))
    end_b = json.loads(await ws_b.recv())
    assert end_b["type"] == "session_end", end_b
    print("Sessao encerrada, B notificado:", end_b)

    # Teste de erro: conectar para ID inexistente
    await ws_a.send(json.dumps({"type": "connect", "target": "ZZZZ-ZZZZ"}))
    err = json.loads(await ws_a.recv())
    assert err["type"] == "error", err
    print("Erro esperado para ID inexistente:", err)

    await ws_a.close()
    await ws_b.close()
    print("\nTODOS OS TESTES PASSARAM")


asyncio.run(main())