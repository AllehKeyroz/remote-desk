"""Meu Controle Remoto - KVM (compartilhamento de teclado/mouse, estilo Barrier).

Cada PC pode ser SERVER (compartilha o teclado/mouse fisico, controla o outro)
ou CLIENT (recebe e injeta). Quem clica em "conectar" vira SERVER.
Descoberta via relay (lista de dispositivos online) - sem digitar ID/IP.

Config (opcional): config.json { name, relay, auto_accept, ... }
"""

import argparse
import asyncio
import json
import os
import queue
import random
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import simpledialog, messagebox

import pydirectinput
import websockets

import kvm_input as ki

ID_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_id() -> str:
    part = lambda: "".join(random.choice(ID_CHARS) for _ in range(4))
    return f"{part()}-{part()}"


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_name() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "PC"


def get_screen() -> tuple[int, int]:
    import ctypes
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


# VK -> nome de tecla do pydirectinput (para encaminhar do server pro client)
VK_TO_NAME = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x1B: "esc", 0x20: "space",
    0x2E: "delete", 0x2D: "insert", 0x24: "home", 0x23: "end",
    0x21: "pageup", 0x22: "pagedown", 0x2C: "printscreen", 0x13: "pause",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x14: "capslock", 0x90: "numlock", 0x91: "scrolllock",
    0x5D: "apps", 0x2A: "shift", 0x2B: "shift",
}
VK_TO_NAME.update({0x70 + i: f"f{i+1}" for i in range(12)})
VK_TO_NAME.update({0x30 + i: str(i) for i in range(10)})
VK_TO_NAME.update({0x41 + i: chr(0x61 + i) for i in range(26)})
VK_TO_NAME[0x11] = "ctrl"; VK_TO_NAME[0x12] = "alt"; VK_TO_NAME[0x5B] = "win"
VK_TO_NAME[0x60] = "num0"; VK_TO_NAME[0x61] = "num1"; VK_TO_NAME[0x62] = "num2"
VK_TO_NAME[0x63] = "num3"; VK_TO_NAME[0x64] = "num4"; VK_TO_NAME[0x65] = "num5"
VK_TO_NAME[0x66] = "num6"; VK_TO_NAME[0x67] = "num7"; VK_TO_NAME[0x68] = "num8"
VK_TO_NAME[0x69] = "num9"; VK_TO_NAME[0x6F] = "divide"; VK_TO_NAME[0x6A] = "multiply"
VK_TO_NAME[0x6B] = "add"; VK_TO_NAME[0x6D] = "subtract"; VK_TO_NAME[0x6E] = "decimal"


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.peer_id = cfg.get("id") or generate_id()
        self.name = cfg.get("name") or default_name()
        self.role: str | None = None  # None | "server" | "client"
        self.peer: str | None = None
        self.link: str | None = None  # "right"|"left" (lado do client em relacao ao server)
        self.status = "conectando"
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self.online: list[dict] = []
        self.server_sw, self.server_sh = get_screen()
        self.client_w, self.client_h = 1920, 1080
        self.cursor = (0, 0)  # posicao do cursor no CLIENT (para deteccao de volta)
        self.root: tk.Tk | None = None
        # input (server)
        self.mouse = None
        self.kbhook = None
        self.mwhook = None
        self._btn_state = [False, False, False]

    # ---------- infra de UI (thread-safe) ----------
    def ui(self, fn, *args, **kw):
        if self.root is not None:
            try:
                self.root.after(0, lambda: fn(*args, **kw))
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        try:
            with open(os.path.join(app_dir(), "meucontrole.log"), "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    # ---------- backend ----------
    def backend(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.ws_loop())

    async def ws_loop(self):
        url = self.cfg.get("relay", "").rstrip("/")
        if not url:
            self._set_status("sem servidor"); return
        url = f"{url}/ws"
        while True:
            try:
                async with websockets.connect(url, max_size=8 * 1024 * 1024,
                                              ping_interval=None, ping_timeout=None) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({"type": "register", "id": self.peer_id, "name": self.name}))
                    self._set_status("online")
                    self._request_list()
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            continue
                        try:
                            self.on_message(json.loads(msg))
                        except json.JSONDecodeError:
                            pass
            except Exception as exc:
                self._log(f"ws errou: {exc!r}")
                self._set_status("offline")
            self.ws = None
            await asyncio.sleep(3)

    async def _send(self, msg):
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:
            pass

    def send(self, msg):
        if self.loop is not None and self.ws is not None:
            asyncio.run_coroutine_threadsafe(self._send(msg), self.loop)

    def _set_status(self, s):
        self.status = s
        self.ui(self._ui_status, s)

    def _request_list(self):
        self.send({"type": "list"})

    def on_message(self, data: dict):
        t = data.get("type")
        if t == "registered":
            self._set_status("online")
            self._request_list()
        elif t == "list":
            self.online = data.get("peers", [])
            self.ui(self._ui_online)
        elif t == "incoming":
            self.ui(self._ui_incoming, data.get("from", ""))
        elif t == "session_start":
            self._on_session_start(data)
        elif t == "session_end":
            self._on_session_end()
        elif t == "session_denied":
            self.ui(self._ui_error, "O dispositivo recusou a conexao")
        elif t == "error":
            self.ui(self._ui_error, data.get("message", "erro"))
        elif self.role == "client":
            self._on_client_msg(data)
        elif self.role == "server":
            self._on_server_msg(data)

    # ---------- sessao ----------
    def _on_session_start(self, data: dict):
        self.peer = data.get("peer")
        role = data.get("role")
        # quem conectou (controller) = SERVER (compartilha); target (host) = CLIENT
        self.role = "server" if role == "controller" else "client"
        self.cursor = (0, 0)
        self._btn_state = [False, False, False]
        if self.role == "server":
            self.server_sw, self.server_sh = get_screen()
            self.send_kvm({"type": "link", "side": self.link or "right"})
            self._start_server_input()
            self.ui(self._ui_role, "server")
        else:
            self.send_kvm({"type": "kvm_hello", "w": self.client_w, "h": self.client_h, "name": self.name})
            self.ui(self._ui_role, "client")

    def _on_session_end(self):
        self._stop_input()
        role = self.role
        self.role = None
        self.peer = None
        self.ui(self._ui_role, None)

    def send_kvm(self, msg):
        self.send(msg)

    # ---------- SERVER: captura de input ----------
    def _start_server_input(self):
        self.mouse = ki.MouseCapture(
            on_cross_out=self._kvm_cross_out,
            on_first=self._kvm_first,
            on_rel=self._kvm_rel,
            on_button=self._kvm_button,
        )
        self.kbhook = ki.KeyboardHook(on_key=self._kvm_key, on_escape=self._kvm_escape)
        self.mwhook = ki.MouseWheelHook(on_wheel=lambda d: self.send_kvm({"type": "scroll", "dy": d}))
        # cria tudo primeiro, depois inicia (evita corrida)
        self.mwhook.start(enabled=False)
        self.kbhook.start(enabled=False)
        self.mouse.start()

    def _stop_input(self):
        for c in (self.mouse, self.kbhook, self.mwhook):
            if c:
                try:
                    c.shutdown()
                except Exception:
                    pass
        self.mouse = self.kbhook = self.mwhook = None
        if self.role in (None, "client"):
            ki.show_cursor()

    def _kvm_cross_out(self, zone: int):
        # zone: 0=esquerda, 1=direita. atravessa se bate com o lado do client.
        target = 1 if self.link == "right" else 0
        if zone == target and self.link and self.mouse:
            self.mouse.cross_to_client()
            if self.kbhook:
                self.kbhook.set_enabled(True)
            if self.mwhook:
                self.mwhook.set_enabled(True)
            self.ui(self._ui_mode, "client")

    def _kvm_first(self, ax: int, ay: int):
        # posiciona o cursor do client na borda oposta, mesma proporcao de altura
        if self.link == "right":
            cx, cy = 0, int(ay / self.server_sh * self.client_h)
        else:
            cx, cy = self.client_w - 1, int(ay / self.server_sh * self.client_h)
        self.cursor = (cx, cy)
        self.send_kvm({"type": "mouse_abs", "x": cx, "y": cy})

    def _kvm_rel(self, dx: int, dy: int):
        self.send_kvm({"type": "mouse_rel", "dx": dx, "dy": dy})

    def _kvm_button(self, idx: int, down: bool):
        name = {0: "left", 1: "right", 2: "middle"}[idx]
        self.send_kvm({"type": "mouse_down" if down else "mouse_up", "button": name})

    def _kvm_key(self, vk: int, down: bool):
        name = VK_TO_NAME.get(vk)
        if name:
            self.send_kvm({"type": "key_down" if down else "key_up", "code": name})

    def _kvm_escape(self):
        # hotkey Ctrl+Alt+K: volta ao server
        if self.mouse and self.mouse.get_mode() == "client":
            self._kvm_cross_back(force=True)

    def _kvm_cross_back(self, force: bool = False):
        if not self.mouse:
            return
        self.mouse.cross_to_local()
        if self.kbhook:
            self.kbhook.set_enabled(False)
        if self.mwhook:
            self.mwhook.set_enabled(False)
        if force or self.link == "right":
            x = self.server_sw - 1
        else:
            x = 0
        # repoe o cursor do server na borda correspondente, mesma proporcao
        cy = int(self.cursor[1] / self.client_h * self.server_sh) if self.client_h else 0
        ki.set_cursor(x, cy)
        self.ui(self._ui_mode, "local")

    def _on_server_msg(self, data: dict):
        if data.get("type") == "kvm_hello":
            self.client_w = int(data.get("w", 1920))
            self.client_h = int(data.get("h", 1080))
        elif data.get("type") == "kvm_cross_back":
            self._kvm_cross_back()

    # ---------- CLIENT: injecao de input ----------
    def _on_client_msg(self, data: dict):
        t = data.get("type")
        try:
            if t == "link":
                self.link = data.get("side")
            elif t == "mouse_abs":
                x, y = int(data["x"]), int(data["y"])
                pydirectinput.moveTo(x, y)
                self.cursor = (x, y)
                self._check_cross_back()
            elif t == "mouse_rel":
                dx, dy = int(data.get("dx", 0)), int(data.get("dy", 0))
                if dx or dy:
                    pydirectinput.moveRel(dx, dy)
                    self.cursor = (self.cursor[0] + dx, self.cursor[1] + dy)
                    self._check_cross_back()
            elif t == "mouse_down":
                pydirectinput.mouseDown(button=data.get("button", "left"))
            elif t == "mouse_up":
                pydirectinput.mouseUp(button=data.get("button", "left"))
            elif t == "scroll":
                pydirectinput.scroll(int(data.get("dy", 0)))
            elif t == "key_down":
                pydirectinput.keyDown(data.get("code"))
            elif t == "key_up":
                pydirectinput.keyUp(data.get("code"))
        except Exception as exc:
            self._log(f"client msg erro {t}: {exc!r}")

    def _check_cross_back(self):
        # qual borda do client conecta com o server
        if self.link == "right" and self.cursor[0] <= ki.MouseCapture.ZONE:
            self._cross_back()
        elif self.link == "left" and self.cursor[0] >= self.client_w - ki.MouseCapture.ZONE:
            self._cross_back()

    def _cross_back(self):
        self.send_kvm({"type": "kvm_cross_back"})

    # ---------- acoes da UI ----------
    def connect_to(self, peer_id: str):
        side = simpledialog.askstring("Posição", "O outro PC está à DIREITA ou à ESQUERDA da sua tela?\n(escreva: direita / esquerda)", parent=self.root)
        if not side:
            return
        side = side.strip().lower()
        self.link = "right" if side.startswith("d") else "left"
        self.send_kvm({"type": "link", "side": self.link})
        self.send({"type": "connect", "target": peer_id})

    def respond_incoming(self, accept: bool, from_id: str):
        self.send({"type": "accept" if accept else "deny", "from": from_id})

    def disconnect(self):
        self.send({"type": "disconnect"})
        self._on_session_end()

    # ---------- UI (tkinter) ----------
    def _ui_status(self, s):
        if getattr(self, "status_label", None):
            self.status_label.config(text=s, fg="#3fb950" if s == "online" else "#f85149")

    def _ui_online(self):
        if not hasattr(self, "online_frame"):
            return
        for w in self.online_frame.winfo_children():
            w.destroy()
        if not self.online:
            tk.Label(self.online_frame, text="Nenhum dispositivo online", bg="#0d1117", fg="#8b949e").pack()
            return
        for p in self.online:
            row = tk.Frame(self.online_frame, bg="#161b22")
            row.pack(fill="x", pady=3)
            nm = p.get("name") or p["id"]
            tk.Label(row, text=f"{nm}  ({p['id']})", bg="#161b22", fg="#e6edf3", anchor="w").pack(side="left", padx=8)
            self._btn(row, "Conectar", lambda pid=p["id"]: self.connect_to(pid), bg="#1f6feb").pack(side="right", padx=4, pady=3)

    def _btn(self, parent, text, cmd, bg="#238636"):
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg="#fff",
                      activebackground=bg, activeforeground="#fff", relief="flat",
                      padx=10, pady=4, font=("Segoe UI", 10))
        return b

    def _ui_incoming(self, from_id: str):
        ok = messagebox.askyesno("Controle recebido", f"O dispositivo {from_id} quer compartilhar o teclado/mouse E CONTROLAR este PC.\n\nAceitar?")
        self.respond_incoming(ok, from_id)

    def _ui_role(self, role):
        if role is None:
            self.session_frame.pack_forget()
            self.idle_frame.pack(fill="both", expand=True)
            return
        self.idle_frame.pack_forget()
        self.session_frame.pack(fill="both", expand=True)
        if role == "server":
            self.session_info.config(text=f"Compartilhando com {self.peer} — mova o mouse até a borda {'direita' if self.link=='right' else 'esquerda'} para atravessar. (Ctrl+Alt+K volta)", fg="#3fb950")
        else:
            self.session_info.config(text=f"Sendo controlado por {self.peer}", fg="#58a6ff")

    def _ui_mode(self, mode):
        if mode == "client":
            if getattr(self, "mode_label", None):
                self.mode_label.config(text="ATRAVESSOU → controlando o outro PC (olhe para a tela ao lado)")
        else:
            if getattr(self, "mode_label", None):
                self.mode_label.config(text="")

    def _ui_error(self, msg):
        if getattr(self, "error_label", None):
            self.error_label.config(text=msg)

    def build_ui(self):
        self.root = tk.Tk()
        self.root.title("Meu Controle Remoto (KVM)")
        self.root.geometry("720x560")
        self.root.minsize(640, 480)
        self.root.configure(bg="#0d1117")

        # id frame
        self.idle_frame = tk.Frame(self.root, bg="#0d1117")
        self.idle_frame.pack(fill="both", expand=True)
        header = tk.Frame(self.idle_frame, bg="#0d1117")
        header.pack(fill="x", padx=20, pady=(24, 8))
        tk.Label(header, text="Meu Controle Remoto", bg="#0d1117", fg="#e6edf3", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        self.id_label = tk.Label(header, text="", bg="#0d1117", fg="#58a6ff", font=("Consolas", 20, "bold"))
        self.id_label.pack(anchor="w")
        self.status_label = tk.Label(header, text="Conectando...", bg="#0d1117", fg="#f85149", font=("Segoe UI", 11))
        self.status_label.pack(anchor="w")
        self.error_label = tk.Label(header, text="", bg="#0d1117", fg="#f85149", font=("Segoe UI", 10))
        self.error_label.pack(anchor="w")

        tk.Label(self.idle_frame, text="Dispositivos online:", bg="#0d1117", fg="#8b949e", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=20, pady=(12, 4))
        self.online_frame = tk.Frame(self.idle_frame, bg="#0d1117")
        self.online_frame.pack(fill="both", expand=True, padx=20)
        tk.Label(self.online_frame, text="Carregando...", bg="#0d1117", fg="#8b949e").pack()

        # session frame
        self.session_frame = tk.Frame(self.root, bg="#0d1117")
        bar = tk.Frame(self.session_frame, bg="#161b22", height=44)
        bar.pack(fill="x")
        self.session_info = tk.Label(bar, text="", bg="#161b22", fg="#e6edf3", font=("Segoe UI", 11))
        self.session_info.pack(side="left", padx=12)
        self._btn(bar, "Desconectar", self.disconnect, bg="#21262d").pack(side="right", padx=12, pady=6)
        self.mode_label = tk.Label(self.session_frame, text="", bg="#0d1117", fg="#f85149", font=("Segoe UI", 12, "bold"))
        self.mode_label.pack(pady=30)

        self.ui(self._ui_show_id)

    def _ui_show_id(self):
        self.id_label.config(text=f"Seu ID: {self.peer_id}   ({self.name})")
        self._ui_status(self.status)

    def start(self):
        if self.cfg.get("run_relay", False):
            self._spawn_relay()
        threading.Thread(target=self.backend, daemon=True).start()
        self.build_ui()
        self.root.mainloop()

    def _spawn_relay(self):
        port = str(self.cfg.get("relay_port", 8765))
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--relay-only", "--port", port]
        else:
            args = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay.py"), "--port", port]
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            self._log(f"relay falhou: {exc!r}")


def load_config(path: str) -> dict:
    cfg = {
        "relay": "wss://dev-kds-sistemas-remote-desk.aoi8gd.easypanel.host",
        "run_relay": False,
        "relay_port": 8765,
        "id": "",
        "name": default_name(),
        "auto_accept": False,
    }
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            cfg.update(json.load(fh))
    return cfg


def main():
    parser = argparse.ArgumentParser(description="Meu Controle Remoto - KVM")
    parser.add_argument("--config", default=os.path.join(app_dir(), "config.json"))
    parser.add_argument("--relay-only", action="store_true", help="roda apenas o relay (processo interno)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.relay_only:
        from relay import Relay
        import aiohttp.web as aiohttp_web
        relay = Relay()
        aiohttp_web.run_app(relay.app(), host="0.0.0.0", port=args.port, print=None)
        return

    cfg = load_config(args.config)
    if not cfg.get("id"):
        cfg["id"] = generate_id()
        with open(args.config, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)

    App(cfg).start()


if __name__ == "__main__":
    main()