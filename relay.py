"""Relay do Meu Controle Remoto.

Servidor de rendezvous: os apps se conectam aqui, registram seus IDs e o
relay faz a ponte entre quem controla e quem e controlado.

Pode rodar em QUALQUER maquina com IP publico (VPS) ou com port forwarding.
Nao tem nenhuma dependencia da rede de quem usa.

Uso:
    python relay.py --port 8765
"""

import argparse
import json

from aiohttp import web, WSMsgType


class Relay:
    def __init__(self):
        self.peers: dict[str, web.WebSocketResponse] = {}
        self.pairs: dict[str, str] = {}  # id -> id pareado

    def peer_of(self, peer_id: str) -> str | None:
        return self.pairs.get(peer_id)

    def is_free(self, peer_id: str) -> bool:
        return peer_id in self.peers and peer_id not in self.pairs

    async def send(self, peer_id: str, data) -> bool:
        ws = self.peers.get(peer_id)
        if ws is None or ws.closed:
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
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        peer_id = None
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
                        # se ha uma conexao antiga ainda aberta, substitui pela nova
                        old = self.peers.get(peer_id)
                        if old is not None and old is not ws and not old.closed:
                            try:
                                await old.close()
                            except Exception:
                                pass
                        self.peers[peer_id] = ws
                        await ws.send_str(json.dumps({"type": "registered", "id": peer_id}))
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