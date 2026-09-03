"""Meu Controle Remoto - app.

Um unico app que roda nos dois PCs. Cada instancia tem seu proprio ID.
Para controlar outro dispositivo, digite o ID dele. O dispositivo
controlado precisa aceitar a conexao.

O relay (servidor de rendezvous) pode rodar embutido (run_relay=true) ou
em qualquer outra maquina (VPS, etc.) - o endereco fica no config.json.

Uso:
    python app.py [--config config.json]
"""

import argparse
import asyncio
import base64
import io
import json
import os
import queue
import random
import subprocess
import sys
import threading
import time

import mss
import pydirectinput
import webview
import websockets
from PIL import Image

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


class Api:
    """Metodos chamados pela interface (JS)."""

    def __init__(self, app: "App"):
        self.app = app

    def get_id(self) -> str:
        return self.app.peer_id

    def get_status(self) -> str:
        return self.app.status

    def get_relay(self) -> str:
        return self.app.cfg.get("relay", "")

    def set_relay(self, url: str) -> None:
        self.app.set_relay(url)

    def connect_to(self, target_id: str) -> None:
        target_id = (target_id or "").strip().upper()
        if target_id and target_id != self.app.peer_id:
            self.app.send({"type": "connect", "target": target_id})

    def respond_incoming(self, accept: bool, from_id: str) -> None:
        self.app.send({"type": "accept" if accept else "deny", "from": from_id})

    def disconnect(self) -> None:
        self.app.send({"type": "disconnect"})
        self.app.end_session()
        self.app.js("onSessionEnd()")

    def send_input(self, evt: dict) -> None:
        self.app.send(evt)

    def set_config(self, quality: int, fps: int) -> None:
        self.app.cfg["quality"] = int(quality)
        self.app.cfg["fps"] = int(fps)
        if self.app.role == "controller":
            self.app.send({"type": "config", "config": {"quality": int(quality), "fps": int(fps)}})


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.peer_id = cfg.get("id") or generate_id()
        self.role: str | None = None
        self.peer: str | None = None
        self.status = "conectando"
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws: websockets.ClientConnection | None = None
        self.capture_task: asyncio.Task | None = None
        self.input_queue: "queue.Queue[dict | None]" = queue.Queue()
        self.window: webview.Window | None = None
        self.api = Api(self)

    # ---------- helpers ----------

    def js(self, code: str) -> None:
        if self.window is not None:
            try:
                self.window.evaluate_js(code)
            except Exception:
                pass

    def set_status(self, status: str) -> None:
        self.status = status
        self.js(f"onStatus({json.dumps(status)})")

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

    # ---------- backend ----------

    def backend(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.ws_loop())

    async def ws_loop(self) -> None:
        url = self.cfg.get("relay", "").rstrip("/")
        if not url:
            self.set_status("sem servidor")
            return
        url = f"{url}/ws"
        while True:
            try:
                async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({"type": "register", "id": self.peer_id}))
                    self.set_status("online")
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            self.on_frame(msg)
                        else:
                            try:
                                self.on_message(json.loads(msg))
                            except json.JSONDecodeError:
                                pass
            except Exception:
                self.set_status("offline")
            self.ws = None
            await asyncio.sleep(3)

    def on_message(self, data: dict) -> None:
        t = data.get("type")
        if t == "registered":
            self.set_status("online")
        elif t == "incoming":
            self.js(f"onIncoming({json.dumps(data.get('from', ''))})")
        elif t == "session_start":
            self.role = data.get("role")
            self.peer = data.get("peer")
            if self.role == "host":
                self._start_capture()
            self.js(f"onSessionStart({json.dumps(data)})")
        elif t == "session_end":
            self.end_session()
            self.js("onSessionEnd()")
        elif t == "session_denied":
            self.js("onError('O dispositivo recusou a conexao')")
        elif t == "error":
            self.js(f"onError({json.dumps(data.get('message', 'erro'))})")
        elif t == "config":
            self.cfg.update(data.get("config", {}))
        else:
            if self.role == "host":
                self.input_queue.put(data)

    def on_frame(self, data: bytes) -> None:
        if self.role == "controller":
            b64 = base64.b64encode(data).decode()
            self.js(f"onFrame('{b64}')")

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
            self.capture_task.cancel()
            self.capture_task = None

    async def capture_loop(self) -> None:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            frame = 0
            while self.role == "host":
                t0 = time.monotonic()
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

    # ---------- relay embutido ----------

    def spawn_relay(self) -> None:
        """Sobe o relay como processo separado (isola do pywebview)."""
        port = str(self.cfg.get("relay_port", 8765))
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--relay-only", "--port", port]
        else:
            args = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "relay.py"), "--port", port]
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            self._log(f"falha ao subir relay: {exc!r}")

    # ---------- config ----------

    def set_relay(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        self.cfg["relay"] = url
        self.save_config()
        self.set_status("reconectando")
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self._reconnect(), self.loop)

    async def _reconnect(self) -> None:
        if self.ws is not None:
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

    def _log(self, msg: str) -> None:
        try:
            with open(os.path.join(app_dir(), "meucontrole.log"), "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    # ---------- main ----------

    def start(self) -> None:
        if self.cfg.get("run_relay", False):
            self.spawn_relay()
        threading.Thread(target=self.backend, daemon=True).start()
        threading.Thread(target=self.input_worker, daemon=True).start()

        self.window = webview.create_window(
            "Meu Controle Remoto",
            resource_path("static/ui.html"),
            js_api=self.api,
            width=1000,
            height=650,
            min_size=(720, 480),
        )
        webview.start()


def load_config(path: str) -> dict:
    cfg = {
        "relay": "ws://localhost:8765",
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