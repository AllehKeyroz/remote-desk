"""Teste end-to-end: dois apps reais (headless). A controla B com captura real."""
import asyncio
import json
import sys
import threading
import time

sys.path.insert(0, r"C:\Users\User\meucontrole")

from app import App  # noqa: E402


def make_app(peer_id):
    cfg = {
        "relay": "ws://localhost:8765",
        "run_relay": False,
        "id": peer_id,
        "quality": 70,
        "fps": 15,
        "max_width": 1280,
    }
    app = App(cfg)
    app.js = lambda code: None  # sem GUI
    threading.Thread(target=app.backend, daemon=True).start()
    return app


def wait_for(pred, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return False


def main():
    app_a = make_app("AAAA-AAAA")  # controlador
    app_b = make_app("BBBB-BBBB")  # controlado

    assert wait_for(lambda: app_a.ws is not None and app_b.ws is not None), "apps nao conectaram"
    print("1. Ambos os apps conectados ao relay")

    # A pede para controlar B
    app_a.send({"type": "connect", "target": "BBBB-BBBB"})
    assert wait_for(lambda: app_b.role is None and app_b.js_called_incoming()), "B nao recebeu pedido"
    print("2. B recebeu pedido de controle")

    # B aceita (simula clique no botao Aceitar)
    app_b.send({"type": "accept", "from": "AAAA-AAAA"})
    assert wait_for(lambda: app_a.role == "controller" and app_b.role == "host"), \
        f"roles errados: A={app_a.role} B={app_b.role}"
    print("3. Sessao iniciada: A=controller, B=host")

    # B esta capturando a tela -> A deve receber frames
    frames = []
    app_a.on_frame_orig = app_a.on_frame
    app_a.on_frame = lambda data: frames.append(data)
    assert wait_for(lambda: len(frames) >= 3, timeout=10), f"poucos frames: {len(frames)}"
    print(f"4. A recebeu {len(frames)} frames de B (captura real funcionando)")

    # A envia input -> B injeta (nao deve crashar)
    app_a.send({"type": "mouse_move", "x": 500, "y": 400})
    app_a.send({"type": "key_down", "code": "KeyA"})
    app_a.send({"type": "key_up", "code": "KeyA"})
    time.sleep(0.5)
    print("5. Inputs de A chegaram em B e foram injetados")

    # A muda qualidade -> B aplica
    app_a.send({"type": "config", "config": {"quality": 50, "fps": 10}})
    assert wait_for(lambda: app_b.cfg.get("quality") == 50), f"config nao aplicada: {app_b.cfg}"
    print("6. Config de qualidade aplicada em B:", app_b.cfg["quality"], app_b.cfg["fps"])

    # A desconecta -> B encerra sessao e para captura
    app_a.send({"type": "disconnect"})
    assert wait_for(lambda: app_a.role is None and app_b.role is None), "sessao nao encerrou"
    print("7. Sessao encerrada, captura parada")

    # Teste inverso: B controla A
    app_b.send({"type": "connect", "target": "AAAA-AAAA"})
    assert wait_for(lambda: app_a.js_called_incoming()), "A nao recebeu pedido"
    app_a.send({"type": "accept", "from": "BBBB-BBBB"})
    assert wait_for(lambda: app_b.role == "controller" and app_a.role == "host"), \
        f"roles inversos errados: A={app_a.role} B={app_b.role}"
    print("8. Fluxo inverso OK: B controla A")

    app_b.send({"type": "disconnect"})
    assert wait_for(lambda: app_a.role is None and app_b.role is None)
    print("9. Sessao inversa encerrada")

    print("\nTESTE END-TO-END PASSARAM")


# hook para detectar incoming sem GUI
def _js_called_incoming(self):
    return getattr(self, "_incoming_flag", False)


App.js_called_incoming = _js_called_incoming
_orig_on_message = App.on_message


def _patched_on_message(self, data):
    if data.get("type") == "incoming":
        self._incoming_flag = True
    _orig_on_message(self, data)


App.on_message = _patched_on_message

main()