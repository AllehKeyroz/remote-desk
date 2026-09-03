"""Teste do backend do app: conecta ao relay, registra ID e simula sessao."""
import asyncio
import json
import sys
import threading
import time

sys.path.insert(0, r"C:\Users\User\meucontrole")

from app import App  # noqa: E402


def test_app_backend():
    cfg = {
        "relay": "ws://localhost:8765",
        "run_relay": False,
        "id": "TEST-1234",
        "quality": 70,
        "fps": 15,
        "max_width": 1280,
    }
    app = App(cfg)
    app.js = lambda code: print(f"  [JS] {code[:80]}")

    # inicia o backend em thread
    t = threading.Thread(target=app.backend, daemon=True)
    t.start()

    # espera conectar e registrar
    deadline = time.time() + 5
    while time.time() < deadline and app.ws is None:
        time.sleep(0.1)
    assert app.ws is not None, "nao conectou ao relay"
    print("App conectou ao relay e registrou ID:", app.peer_id)

    # simula pedido de controle recebido
    app.on_message({"type": "incoming", "from": "OUTRO-9999"})
    print("onIncoming chamado (JS)")

    # simula inicio de sessao como host
    app.on_message({"type": "session_start", "peer": "OUTRO-9999", "role": "host"})
    time.sleep(1.5)
    assert app.role == "host", "role deveria ser host"
    print("Sessao como host iniciada, capture rodando")

    # simula input recebido (nao deve crashar)
    app.input_queue.put({"type": "mouse_move", "x": 10, "y": 10})
    app.input_queue.put({"type": "key_down", "code": "KeyA"})
    app.input_queue.put({"type": "key_up", "code": "KeyA"})
    time.sleep(0.5)
    print("Inputs injetados sem erro")

    # encerra sessao
    app.on_message({"type": "session_end", "peer": "OUTRO-9999"})
    time.sleep(0.5)
    assert app.role is None, "role deveria ser None apos session_end"
    print("Sessao encerrada corretamente")

    print("\nBACKEND DO APP OK")


test_app_backend()