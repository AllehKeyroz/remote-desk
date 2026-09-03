"""Meu Controle Remoto - app.

App estilo AnyDesk: cada PC tem um ID e voce digita o ID do outro para
controlar (com o outro aceitando). Interface em tkinter (sem dependencia de
WebView2 - funciona em qualquer Windows).

O relay pode rodar embutido (run_relay=true, sobe como processo separado)
ou em qualquer servidor publico (VPS) - so mudar o config.

Uso:
    python app.py [--config config.json]
"""

import argparse
import asyncio
import io
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import mss
import pydirectinput
import websockets
from PIL import Image, ImageTk

ID_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_id() -> str:
    part = lambda: "".join(random.choice(ID_CHARS) for _ in range(4))
    return f"{part()}-{part()}"


def resource_path(rel: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


KEY_MAP = {
    **{f"Key{chr(c)}": chr(c).lower() for c in range(ord("A"), ord("Z") + 1)},
    **{f"Digit{i}": str(i) for i in range(10)},
    **{f"Numpad{i}": str(i) for i in range(10)},
    **{f"F{i}": f"f{i}" for i in range(1, 13)},
    "NumpadAdd": "add", "NumpadSubtract": "subtract", "NumpadMultiply": "multiply",
    "NumpadDivide": "divide", "NumpadDecimal": "decimal", "NumpadEnter": "enter",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "ShiftLeft": "shift", "ShiftRight": "shift",
    "ControlLeft": "ctrl", "ControlRight": "ctrl",
    "AltLeft": "alt", "AltRight": "alt",
    "MetaLeft": "win", "MetaRight": "win",
    "Enter": "enter", "Escape": "esc", "Space": "space", "Tab": "tab",
    "Backspace": "backspace", "Delete": "delete", "Insert": "insert",
    "Home": "home", "End": "end", "PageUp": "pageup", "PageDown": "pagedown",
    "CapsLock": "capslock", "NumLock": "numlock", "ScrollLock": "scrolllock",
    "PrintScreen": "printscreen", "Pause": "pause", "ContextMenu": "apps",
    "Minus": "-", "Equal": "=", "BracketLeft": "[",
    "BracketRight": "]", "Backslash": "\\", "Semicolon": ";",
    "Quote": "'", "Comma": ",", "Period": ".", "Slash": "/",
    "Backquote": "`",
}


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.peer_id = cfg.get("id") or generate_id()
        self.role: str | None = None
        self.peer: str | None = None
        self.status = "conectando"
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self.capture_task = None
        self.input_queue: "queue.Queue[dict | None]" = queue.Queue()
        self.root: tk.Tk | None = None
        self._photo = None  # evita garbage collection das imagens
        self.remote_w = 1920
        self.remote_h = 1080
        self.scroll_acc = 0

    # ---------- helpers de UI (thread-safe) ----------

    def ui(self, fn, *args, **kw) -> None:
        """Agenda fn para rodar na thread principal do tkinter."""
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

    async def ws_loop(self) -> None:
        url = self.cfg.get("relay", "").rstrip("/")
        if not url:
            self._set_status("sem servidor")
            return
        url = f"{url}/ws"
        while True:
            try:
                async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({"type": "register", "id": self.peer_id}))
                    self._set_status("online")
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            self.on_frame(msg)
                        else:
                            try:
                                self.on_message(json.loads(msg))
                            except json.JSONDecodeError:
                                pass
            except Exception as exc:
                self._log(f"ws errou: {exc!r}")
                self._set_status("offline")
            self.ws = None
            if self.loop is not None and self.loop.is_running():
                await asyncio.sleep(3)

    def _set_status(self, status: str) -> None:
        self.status = status
        self.ui(self._ui_status, status)

    def on_message(self, data: dict) -> None:
        t = data.get("type")
        if t == "registered":
            self._set_status("online")
        elif t == "incoming":
            self.ui(self._ui_incoming, data.get("from", ""))
        elif t == "session_start":
            self.role = data.get("role")
            self.peer = data.get("peer")
            if self.role == "host":
                self._start_capture()
            self.ui(self._ui_session_start, data)
        elif t == "session_end":
            self.end_session()
            self.ui(self._ui_session_end)
        elif t == "session_denied":
            self.ui(self._ui_error, "O dispositivo recusou a conexao")
        elif t == "error":
            self.ui(self._ui_error, data.get("message", "erro"))
        elif t == "config":
            self.cfg.update(data.get("config", {}))
        else:
            if self.role == "host":
                self.input_queue.put(data)

    def on_frame(self, data: bytes) -> None:
        if self.role == "controller":
            try:
                img = Image.open(io.BytesIO(data))
                img.load()
                self.ui(self._ui_draw, img)
            except Exception:
                pass

    def end_session(self) -> None:
        self._stop_capture()
        self.role = None
        self.peer = None

    def _start_capture(self) -> None:
        if self.capture_task is not None or self.loop is None:
            return
        try:
            asyncio.get_running_loop()
            self.capture_task = asyncio.create_task(self.capture_loop())
        except RuntimeError:
            self.capture_task = asyncio.run_coroutine_threadsafe(self.capture_loop(), self.loop)

    def _stop_capture(self) -> None:
        if self.capture_task is not None:
            try:
                self.capture_task.cancel()
            except Exception:
                pass
            self.capture_task = None

    async def capture_loop(self) -> None:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            frame = 0
            while self.role == "host":
                t0 = time.monotonic()
                try:
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.rgb)
                    max_w = int(self.cfg.get("max_width", 1280))
                    if img.width > max_w:
                        ratio = max_w / img.width
                        img = img.resize((max_w, int(img.height * ratio)), Image.BILINEAR)
                    buf = io.BytesIO()
                    img.save(buf, "JPEG", quality=int(self.cfg.get("quality", 70)))
                    if frame % 30 == 0:
                        await self._send({"type": "screen_info",
                                          "width": monitor["width"], "height": monitor["height"]})
                    await self._send(buf.getvalue())
                    frame += 1
                    target = 1.0 / max(1, int(self.cfg.get("fps", 15)))
                    dt = time.monotonic() - t0
                    if dt < target:
                        await asyncio.sleep(target - dt)
                except asyncio.CancelledError:
                    return
                except Exception:
                    await asyncio.sleep(0.2)

    def input_worker(self) -> None:
        while True:
            evt = self.input_queue.get()
            if evt is None:
                break
            try:
                self.handle_event(evt)
            except Exception:
                pass

    def handle_event(self, evt: dict) -> None:
        t = evt.get("type")
        try:
            if t == "mouse_move":
                pydirectinput.moveTo(int(evt["x"]), int(evt["y"]))
            elif t == "mouse_down":
                pydirectinput.mouseDown(button=evt.get("button", "left"))
            elif t == "mouse_up":
                pydirectinput.mouseUp(button=evt.get("button", "left"))
            elif t == "scroll":
                pydirectinput.scroll(int(evt.get("dy", 0)))
            elif t == "key_down":
                key = KEY_MAP.get(evt.get("code", ""))
                if key:
                    pydirectinput.keyDown(key)
            elif t == "key_up":
                key = KEY_MAP.get(evt.get("code", ""))
                if key:
                    pydirectinput.keyUp(key)
        except Exception:
            pass

    def send(self, msg) -> None:
        if self.loop is not None and self.ws is not None:
            asyncio.run_coroutine_threadsafe(self._send(msg), self.loop)

    async def _send(self, msg) -> None:
        try:
            if isinstance(msg, (dict, list)):
                await self.ws.send(json.dumps(msg))
            else:
                await self.ws.send(msg)
        except Exception:
            pass

    # ---------- relay embutido ----------

    def spawn_relay(self) -> None:
        port = str(self.cfg.get("relay_port", 8765))
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--relay-only", "--port", port]
        else:
            args = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay.py"), "--port", port]
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            self._log(f"falha ao subir relay: {exc!r}")

    def set_relay(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        self.cfg["relay"] = url
        self.save_config()
        self._set_status("reconectando")
        if self.loop is not None and self.ws is not None:
            asyncio.run_coroutine_threadsafe(self._reconnect(), self.loop)

    async def _reconnect(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass

    def save_config(self) -> None:
        path = os.path.join(app_dir(), "config.json")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.cfg, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ---------- controle de UI (thread principal) ----------

    def _ui_status(self, status: str) -> None:
        if self.status_label is not None:
            self.status_label.config(text=status)
            self.status_label.config(fg="#3fb950" if status == "online" else "#f85149")
            self.connect_btn.config(state="normal" if status == "online" else "disabled")

    def _ui_incoming(self, from_id: str) -> None:
        ok = messagebox.askyesno(
            "Controle recebido",
            f"O dispositivo {from_id} quer controlar este PC.\n\nAceitar?",
        )
        self.send({"type": "accept" if ok else "deny", "from": from_id})

    def _ui_session_start(self, data: dict) -> None:
        self.idle_frame.pack_forget()
        self.session_frame.pack(fill="both", expand=True)
        if data.get("role") == "controller":
            self.session_info.config(text=f"Controlando {data.get('peer')}", fg="#e6edf3")
            self.host_banner.pack_forget()
            self.session_toolbar.pack(fill="x")
        else:
            self.host_banner.config(text=f"Você está sendo controlado por {data.get('peer')}")
            self.host_banner.pack(fill="x")
            self.session_toolbar.pack_forget()

    def _ui_session_end(self) -> None:
        self.session_frame.pack_forget()
        self.idle_frame.pack(fill="both", expand=True)
        self.role = None

    def _ui_error(self, msg: str) -> None:
        self.error_label.config(text=msg)

    def _ui_draw(self, img) -> None:
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        scale = min(cw / img.width, ch / img.height)
        nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        if nw != img.width or nh != img.height:
            img = img.resize((nw, nh), Image.BILINEAR)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._photo)

    # ---------- montagem da janela ----------

    def _btn(self, parent, text, command, bg="#238636", fg="#ffffff", padx=10, pady=6):
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                      activebackground=bg, activeforeground=fg,
                      relief="flat", padx=padx, pady=pady, font=("Segoe UI", 10))
        return b

    def build_ui(self) -> None:
        self.root = tk.Tk()
        self.root.title("Meu Controle Remoto")
        self.root.geometry("1000x650")
        self.root.minsize(760, 480)
        self.root.configure(bg="#0d1117")

        # ---------- tela inicial ----------
        self.idle_frame = tk.Frame(self.root, bg="#0d1117")
        self.idle_frame.pack(fill="both", expand=True)
        card = tk.Frame(self.idle_frame, bg="#0d1117")
        card.place(relx=0.5, rely=0.48, anchor="center")

        tk.Label(card, text="Meu Controle Remoto", bg="#0d1117", fg="#e6edf3",
                 font=("Segoe UI", 22, "bold")).pack()
        tk.Label(card, text="Controle qualquer dispositivo com este app", bg="#0d1117",
                 fg="#8b949e", font=("Segoe UI", 11)).pack(pady=(2, 26))

        tk.Label(card, text="SEU ID", bg="#0d1117", fg="#8b949e",
                 font=("Segoe UI", 9, "bold")).pack()
        self.id_label = tk.Label(card, text="----", bg="#0d1117", fg="#58a6ff",
                                 font=("Consolas", 40, "bold"))
        self.id_label.pack()

        self.status_label = tk.Label(card, text="Conectando...", bg="#0d1117", fg="#f85149",
                                     font=("Segoe UI", 11))
        self.status_label.pack(pady=(4, 20))

        self.target_entry = tk.Entry(card, justify="center", font=("Consolas", 16, "bold"),
                                     bg="#161b22", fg="#e6edf3", insertbackground="#e6edf3",
                                     relief="flat", highlightthickness=1,
                                     highlightbackground="#30363d", highlightcolor="#58a6ff")
        self.target_entry.pack(fill="x", ipady=9)
        self.target_entry.bind("<Return>", lambda e: self._do_connect())

        self.connect_btn = self._btn(card, "Conectar", self._do_connect)
        self.connect_btn.pack(fill="x", pady=(12, 0))
        self.connect_btn.config(state="disabled")

        self.error_label = tk.Label(card, text="", bg="#0d1117", fg="#f85149", font=("Segoe UI", 10))
        self.error_label.pack(pady=(8, 0))

        # config do servidor
        tk.Label(card, text="", bg="#0d1117").pack()
        self.relay_frame = tk.Frame(card, bg="#0d1117")
        self.relay_frame.pack(pady=(10, 0))
        self.relay_entry = tk.Entry(self.relay_frame, font=("Segoe UI", 10), bg="#161b22",
                                    fg="#e6edf3", insertbackground="#e6edf3", relief="flat",
                                    highlightthickness=1, highlightbackground="#30363d")
        self.relay_entry.pack(side="left", ipady=5, padx=(0, 6))
        self._btn(self.relay_frame, "Salvar servidor", self._save_relay,
                  bg="#21262d", fg="#e6edf3").pack(side="left")

        # ---------- tela de sessao ----------
        self.session_frame = tk.Frame(self.root, bg="#0d1117")

        self.session_toolbar = tk.Frame(self.session_frame, bg="#161b22", height=40)
        self.session_toolbar.pack(fill="x")
        self.session_info = tk.Label(self.session_toolbar, text="", bg="#161b22", fg="#e6edf3",
                                     font=("Segoe UI", 10))
        self.session_info.pack(side="left", padx=12)

        self._btn(self.session_toolbar, "Desconectar", self._disconnect,
                  bg="#21262d", fg="#f85149").pack(side="right", padx=12, pady=5)

        self.host_banner = tk.Label(self.session_frame, bg="#1f6feb", fg="#ffffff",
                                    font=("Segoe UI", 11, "bold"), pady=6)
        self.canvas = tk.Canvas(self.session_frame, bg="#000000", highlightthickness=0)

        # bindings de input (so ativos quando controller)
        self.canvas.bind("<Motion>", self._on_move)
        self.canvas.bind("<ButtonPress-1>", lambda e: self._on_btn(e, 1))
        self.canvas.bind("<ButtonPress-2>", lambda e: self._on_btn(e, 2))
        self.canvas.bind("<ButtonPress-3>", lambda e: self._on_btn(e, 3))
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._on_release(e, 1))
        self.canvas.bind("<ButtonRelease-2>", lambda e: self._on_release(e, 2))
        self.canvas.bind("<ButtonRelease-3>", lambda e: self._on_release(e, 3))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<KeyPress>", self._on_key_press)
        self.root.bind("<KeyRelease>", self._on_key_release)

        # mostra o ID depois do loop iniciar
        self.ui(self._ui_show_id)
        self.ui(self._ui_status, self.status)

    def _ui_show_id(self) -> None:
        self.id_label.config(text=self.peer_id)
        self.relay_entry.insert(0, self.cfg.get("relay", ""))

    def _do_connect(self) -> None:
        target = self.target_entry.get().strip().upper()
        if target and target != self.peer_id:
            self.error_label.config(text="")
            self.send({"type": "connect", "target": target})

    def _save_relay(self) -> None:
        url = self.relay_entry.get().strip()
        if url:
            self.set_relay(url)

    def _disconnect(self) -> None:
        self.send({"type": "disconnect"})
        self.end_session()
        self.ui(self._ui_session_end)

    # ---------- bindings de input ----------

    def _to_remote(self, x: int, y: int):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        scale = min(cw / self.remote_w, ch / self.remote_h)
        dw, dh = self.remote_w * scale, self.remote_h * scale
        dx, dy = (cw - dw) / 2, (ch - dh) / 2
        rx = int((x - dx) / scale) if scale else 0
        ry = int((y - dy) / scale) if scale else 0
        return rx, ry

    def _on_move(self, e):
        if self.role != "controller":
            return
        rx, ry = self._to_remote(e.x, e.y)
        self.send({"type": "mouse_move", "x": rx, "y": ry})

    def _on_btn(self, e, btn):
        if self.role != "controller":
            return
        rx, ry = self._to_remote(e.x, e.y)
        name = {1: "left", 2: "middle", 3: "right"}[btn]
        self.send({"type": "mouse_move", "x": rx, "y": ry})
        self.send({"type": "mouse_down", "button": name})

    def _on_release(self, e, btn):
        if self.role != "controller":
            return
        name = {1: "left", 2: "middle", 3: "right"}[btn]
        self.send({"type": "mouse_up", "button": name})

    def _on_wheel(self, e):
        if self.role != "controller":
            return
        self.scroll_acc += e.delta
        step = 120
        if abs(self.scroll_acc) >= step:
            clicks = int(self.scroll_acc / step)
            self.send({"type": "scroll", "dy": clicks})
            self.scroll_acc -= clicks * step

    def _on_key_press(self, e):
        if self.role != "controller":
            return
        code = f"Key{e.keysym}" if len(e.keysym) == 1 else e.keysym
        self.send({"type": "key_down", "code": code})

    def _on_key_release(self, e):
        if self.role != "controller":
            return
        code = f"Key{e.keysym}" if len(e.keysym) == 1 else e.keysym
        self.send({"type": "key_up", "code": code})

    # ---------- main ----------

    def start(self) -> None:
        if self.cfg.get("run_relay", False):
            self.spawn_relay()
        threading.Thread(target=self.backend, daemon=True).start()
        threading.Thread(target=self.input_worker, daemon=True).start()
        self.build_ui()
        self.root.mainloop()


def load_config(path: str) -> dict:
    cfg = {
        "relay": "wss://dev-kds-sistemas-remote-desk.aoi8gd.easypanel.host",
        "run_relay": False,
        "relay_port": 8765,
        "id": "",
        "auto_accept": False,
        "quality": 70,
        "fps": 15,
        "max_width": 1280,
    }
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as fh:
            cfg.update(json.load(fh))
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="Meu Controle Remoto")
    parser.add_argument("--config", default=os.path.join(app_dir(), "config.json"))
    parser.add_argument("--relay-only", action="store_true", help="roda apenas o relay (processo interno)")
    parser.add_argument("--port", type=int, default=8765, help="porta do relay (com --relay-only)")
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

    app = App(cfg)
    app.start()


if __name__ == "__main__":
    main()