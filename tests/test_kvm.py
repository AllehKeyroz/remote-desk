"""Teste do fluxo KVM com 2 instancias headless + relay local."""
import asyncio
import json
import sys
import threading
import time

sys.path.insert(0, r"C:\Users\User\meucontrole")

from app import App  # noqa: E402


def make(pid, name):
    cfg = {"relay": "ws://localhost:8765", "run_relay": False, "id": pid, "name": name}
    a = App(cfg)
    a._log = lambda m: None
    threading.Thread(target=a.backend, daemon=True).start()
    return a


def wait(pred, t=8):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def main():
    A = make("KVM-AAAA", "PC-PRINCIPAL")
    B = make("KVM-BBBB", "PC-CLIENTE")

    assert wait(lambda: A.ws and B.ws), "apps nao conectaram no relay"
    print("1. A e B conectados e registrados no relay")

    # descoberta: A pede lista
    A.send({"type": "list"})
    assert wait(lambda: any(p["id"] == "KVM-BBBB" for p in A.online)), f"lista: {A.online}"
    print("2. A descobriu B na lista:", [p["id"] for p in A.online])

    # A conecta em B (A vira SERVER)
    A.link = "right"
    A.send({"type": "connect", "target": "KVM-BBBB"})
    assert wait(lambda: getattr(B, "_inc", False)), "B nao recebeu incoming"
    print("3. B recebeu o pedido de A")

    # B aceita
    B.send({"type": "accept", "from": "KVM-AAAA"})
    assert wait(lambda: A.role == "server" and B.role == "client"), f"roles A={A.role} B={B.role}"
    print("4. Sessao: A=SERVER (compartilha), B=CLIENT (recebe)")

    # hooking flags p/ mensagens
    A._kvm_msgs, B._kvm_msgs = [], []
    _a_on = A.on_message
    _b_on = B.on_message

    def a_on(d):
        if d.get("type") in ("kvm_cross_back",): A._kvm_msgs.append(d["type"])
        _a_on(d)
    def b_on(d):
        if d.get("type") in ("link", "kvm_hello", "mouse_abs", "mouse_rel", "mouse_down", "scroll", "key_down", "key_up"):
            B._kvm_msgs.append(d)
        _b_on(d)
    A.on_message, B.on_message = a_on, b_on

    # server envia link + um mouse_abs (simula travessia)
    A.client_w, A.client_h = 1920, 1080
    A.send_kvm({"type": "mouse_abs", "x": 0, "y": 300})
    assert wait(lambda: any(m.get("type") == "mouse_abs" for m in B._kvm_msgs)), f"{B._kvm_msgs}"
    print("5. mouse_abs de A chegou em B e foi injetado (moveTo)")

    # server envia tecla
    A.send_kvm({"type": "key_down", "code": "a"})
    A.send_kvm({"type": "key_up", "code": "a"})
    assert wait(lambda: any(m.get("type") == "key_down" for m in B._kvm_msgs))
    print("6. key_down/key_up de A chegou em B")

    # client detecta volta (cursor na borda esquerda) e envia cross_back
    B.link = "right"
    B.cursor = (2, 300)
    B._cross_back()
    assert wait(lambda: A._kvm_msgs), f"{A._kvm_msgs}"
    print("7. B avisou cross_back, A recebeu:", A._kvm_msgs)

    # desconecta
    A.send({"type": "disconnect"})
    assert wait(lambda: A.role is None and B.role is None), "sessao nao encerrou"
    print("8. Desconexao OK")

    print("\n=== TESTE KVM OK ===")


# hook p/ detectar incoming sem GUI
_orig = App.on_message
def _p(self, d):
    if d.get("type") == "incoming":
        self._inc = True
    _orig(self, d)
App.on_message = _p

main()