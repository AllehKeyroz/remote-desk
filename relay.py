"""Relay do Meu Controle Remoto.

Servidor de rendezvous: os apps se conectam aqui, registram seus IDs (+ nome
opcional) e o relay faz a ponte entre quem compartilha (server) e quem e
controlado (client). Tambem serve de "hub de descoberta" (mensagem list).

Pode rodar em QUALQUER maquina com IP publico (VPS) ou com port forwarding.

Uso:
    python relay.py --port 8765
"""

import argparse
import json
import os
import sys

from aiohttp import web, WSMsgType

PKG_DIR = os.path.join(sys._MEIPASS, "packages") if getattr(sys, "frozen", False) \
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "packages")


async def index(request: web.Request) -> web.Response:
    """Pagina de download do app."""
    packages = []
    try:
        for f in sorted(os.listdir(PKG_DIR)):
            if f.endswith((".zip", ".exe")):
                size = os.path.getsize(os.path.join(PKG_DIR, f))
                packages.append((f, size))
    except FileNotFoundError:
        pass

    items = "".join(
        f'<a class="btn" href="/download/{f}" download>{f} ({size/1048576:.1f} MB)</a>'
        for f, size in packages
    )
    if not items:
        items = '<p class="muted">Nenhum pacote disponível ainda.</p>'

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meu Controle Remoto — Download</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:36px 40px;max-width:420px;text-align:center}}
h1{{font-size:22px;margin:0 0 6px}} p{{color:#8b949e;margin:0 0 22px}}
.btn{{display:block;background:#238636;color:#fff;text-decoration:none;padding:14px;
border-radius:10px;font-size:15px;font-weight:600;margin:10px 0}}
.btn:hover{{background:#2ea043}} .muted{{color:#484f58}}
.tag{{display:inline-block;background:#1f6feb;color:#fff;font-size:11px;padding:2px 8px;
border-radius:10px;vertical-align:middle;margin-left:8px}}</style></head>
<body><div class="card">
<h1>Meu Controle Remoto<span class="tag">KVM</span></h1>
<p>Compartilhe teclado e mouse entre PCs (estilo Barrier). Baixe e instale nos dois PCs.</p>
{items}
<p class="muted">Descompacte e rode o MeuControle.exe em cada PC.</p>
</div></body></html>"""
    return web.Response(text=html, content_type="text/html")


async def download(request: web.Request) -> web.Response:
    name = request.match_info.get("name", "")
    if not name or name not in os.listdir(PKG_DIR):
        return web.Response(status=404, text="não encontrado")
    path = os.path.join(PKG_DIR, name)
    return web.FileResponse(path)


class Relay:
    def __init__(self):
        self.peers: dict[str, dict] = {}  # id -> {"ws": ws, "name": str}
        self.pairs: dict[str, str] = {}  # id -> id pareado (sessao ativa)

    def peer_of(self, peer_id: str) -> str | None:
        return self.pairs.get(peer_id)

    def is_free(self, peer_id: str) -> bool:
        return peer_id in self.peers and peer_id not in self.pairs

    async def send(self, peer_id: str, data) -> bool:
        entry = self.peers.get(peer_id)
        if entry is None:
            return False
        ws = entry["ws"]
        if ws.closed:
            return False
        try:
            if isinstance(data, bytes):
                await ws.send_bytes(data)
            else:
                await ws.send_str(data if isinstance(data, str) else json.dumps(data))
            return True
        except Exception:
            return False

    def unpair(self, peer_id: str) -> str | None:
        other = self.pairs.pop(peer_id, None)
        if other:
            self.pairs.pop(other, None)
        return other

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=0, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        peer_id = None

        def online_list():
            mine = peer_id
            out = []
            for pid, entry in self.peers.items():
                if pid == mine:
                    continue
                out.append({"id": pid, "name": entry.get("name", "")})
            return out

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    t = data.get("type")
                    if t == "register":
                        peer_id = data.get("id", "")
                        if not peer_id:
                            await ws.send_str(json.dumps({"type": "error", "message": "ID vazio"}))
                            await ws.close()
                            return ws
                        # limpa sessao/par antigo deste ID (evita "dispositivo ocupado")
                        other = self.unpair(peer_id)
                        if other:
                            await self.send(other, {"type": "session_end", "peer": peer_id})
                        # substitui conexao antiga ainda aberta
                        old = self.peers.get(peer_id)
                        if old is not None and old["ws"] is not ws and not old["ws"].closed:
                            try:
                                await old["ws"].close()
                            except Exception:
                                pass
                        self.peers[peer_id] = {"ws": ws, "name": data.get("name", "")}
                        await ws.send_str(json.dumps({"type": "registered", "id": peer_id}))
                    elif t == "list":
                        await ws.send_str(json.dumps({"type": "list", "peers": online_list()}))
                    elif t == "connect":
                        target = data.get("target", "")
                        if target not in self.peers:
                            await ws.send_str(json.dumps({"type": "error", "message": "ID nao encontrado"}))
                        elif not self.is_free(peer_id) or not self.is_free(target):
                            await ws.send_str(json.dumps({"type": "error", "message": "dispositivo ocupado"}))
                        else:
                            await self.send(target, {"type": "incoming", "from": peer_id})
                    elif t == "accept":
                        controller = data.get("from", "")
                        if controller in self.peers and self.is_free(peer_id) and self.is_free(controller):
                            self.pairs[peer_id] = controller
                            self.pairs[controller] = peer_id
                            await self.send(controller, {"type": "session_start", "peer": peer_id, "role": "controller"})
                            await self.send(peer_id, {"type": "session_start", "peer": controller, "role": "host"})
                    elif t == "deny":
                        controller = data.get("from", "")
                        await self.send(controller, {"type": "session_denied", "peer": peer_id})
                    elif t == "disconnect":
                        other = self.unpair(peer_id)
                        if other:
                            await self.send(other, {"type": "session_end", "peer": peer_id})
                        await self.send(peer_id, {"type": "session_end", "peer": other or ""})
                    else:
                        other = self.peer_of(peer_id)
                        if other:
                            await self.send(other, msg.data)
                elif msg.type == WSMsgType.BINARY:
                    other = self.peer_of(peer_id)
                    if other:
                        await self.send(other, msg.data)
        finally:
            if peer_id:
                self.peers.pop(peer_id, None)
                other = self.unpair(peer_id)
                if other:
                    await self.send(other, {"type": "session_end", "peer": peer_id})
        return ws

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", index)
        app.router.add_get("/download/{name}", download)
        app.router.add_get("/ws", self.handler)
        return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Meu Controle Remoto - relay")
    parser.add_argument("--host", default="0.0.0.0", help="interface (padrao: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8765, help="porta (padrao: 8765)")
    args = parser.parse_args()
    relay = Relay()
    print(f"Relay rodando em ws://{args.host}:{args.port}/ws")
    web.run_app(relay.app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()