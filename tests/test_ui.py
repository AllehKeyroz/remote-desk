"""Teste da interface: carrega ui.html com API mockada e valida o comportamento."""
import asyncio
import base64
import io

from PIL import Image
from playwright.async_api import async_playwright

# gera um JPEG real (imagem 320x200 com um retangulo colorido)
_img = Image.new("RGB", (320, 200), (13, 17, 23))
for x in range(60, 260):
    for y in range(40, 160):
        _img.putpixel((x, y), (88, 166, 255))
_buf = io.BytesIO()
_img.save(_buf, "JPEG", quality=80)
FAKE_JPEG = base64.b64encode(_buf.getvalue()).decode()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport={"width": 1000, "height": 650})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # Nao injeta pywebview no inicio - simula o bug: API so existe apos pywebviewready
        await page.goto("file:///C:/Users/User/meucontrole/static/ui.html")
        await page.wait_for_timeout(300)

        # 0a. Sem API ainda: ID deve estar no estado inicial (----, sem crash)
        my_id_before = await page.text_content("#myId")
        assert my_id_before == "----", f"antes do ready, ID deveria ser '----': {my_id_before}"
        print("0a. Pagina carrega SEM crash quando a API ainda nao existe (ID:", my_id_before, ")")

        # 0b. Agora a API chega via evento pywebviewready
        await page.evaluate("""
            window.pywebview = {
                api: {
                    get_id: () => Promise.resolve('K7X2-9M4P'),
                    get_status: () => Promise.resolve('online'),
                    get_relay: () => Promise.resolve('ws://localhost:8765'),
                    connect_to: (id) => window.__connect = id,
                    respond_incoming: (a, f) => window.__respond = [a, f],
                    disconnect: () => window.__disconnect = true,
                    send_input: (evt) => { window.__inputs = window.__inputs || []; window.__inputs.push(evt); },
                    set_config: (q, f) => window.__config = [q, f],
                    set_relay: (u) => window.__relay = u,
                }
            };
            window.dispatchEvent(new Event('pywebviewready'));
        """)
        await page.wait_for_timeout(400)

        # 1. ID e status agora visiveis
        my_id = await page.text_content("#myId")
        status = await page.text_content("#status")
        assert my_id == "K7X2-9M4P", f"ID errado: {my_id}"
        assert "online" in status, f"status errado: {status}"
        print("1. ID e status OK apos pywebviewready:", my_id, "/", status)

        # 2. botao conectar habilitado e envia ID
        btn_disabled = await page.is_disabled("#connectBtn")
        assert not btn_disabled, "botao deveria estar habilitado"
        await page.fill("#targetId", "bbbb-bbbb")
        await page.click("#connectBtn")
        connect = await page.evaluate("window.__connect")
        assert connect == "BBBB-BBBB", f"connect_to errado: {connect}"
        print("2. Botao conectar OK, enviou:", connect)

        # 3. pedido de controle recebido
        await page.evaluate("onIncoming('XXXX-XXXX')")
        incoming_visible = await page.is_visible("#incoming")
        assert incoming_visible, "prompt de controle deveria aparecer"
        from_id = await page.text_content("#fromId")
        assert from_id == "XXXX-XXXX", f"fromId errado: {from_id}"
        await page.click("#acceptBtn")
        respond = await page.evaluate("window.__respond")
        assert respond == [True, "XXXX-XXXX"], f"respond errado: {respond}"
        print("3. Prompt de controle OK, aceitou:", respond)

        # 4. sessao como controller: mostra canvas e toolbar
        await page.evaluate("onSessionStart({role:'controller', peer:'XXXX-XXXX'})")
        session_visible = await page.is_visible("#session")
        toolbar_visible = await page.is_visible("#toolbar")
        assert session_visible and toolbar_visible, "sessao deveria aparecer com toolbar"
        print("4. Sessao controller OK")

        # 5. frame renderiza no canvas
        await page.evaluate(f"onFrame('{FAKE_JPEG}')")
        await page.wait_for_timeout(300)
        pixels = await page.evaluate("""() => {
            const c = document.getElementById('screen');
            const ctx = c.getContext('2d');
            const d = ctx.getImageData(0, 0, c.width, c.height).data;
            let nonBlack = 0;
            for (let i = 0; i < d.length; i += 4) {
                if (d[i] > 10 || d[i+1] > 10 || d[i+2] > 10) nonBlack++;
            }
            return nonBlack;
        }""")
        assert pixels > 0, "canvas deveria ter pixels nao-pretos"
        print("5. Frame renderizado no canvas OK (pixels:", pixels, ")")

        # 6. input do mouse vai para api.send_input
        await page.mouse.move(500, 300)
        await page.wait_for_timeout(100)
        inputs = await page.evaluate("window.__inputs || []")
        moves = [i for i in inputs if i["type"] == "mouse_move"]
        assert len(moves) > 0, "deveria ter enviado mouse_move"
        print("6. Input de mouse enviado OK:", moves[0])

        # 7. teclado vai para api.send_input
        await page.keyboard.press("KeyA")
        await page.wait_for_timeout(100)
        inputs = await page.evaluate("window.__inputs || []")
        keys = [i for i in inputs if i["type"] == "key_down"]
        assert len(keys) > 0 and keys[0]["code"] == "KeyA", f"teclado errado: {keys}"
        print("7. Input de teclado enviado OK:", keys[0])

        # 8. sliders enviam config
        await page.eval_on_selector("#quality", "el => { el.value = 50; el.dispatchEvent(new Event('change')); }")
        await page.wait_for_timeout(100)
        cfg = await page.evaluate("window.__config")
        assert cfg == [50, 15], f"config errado: {cfg}"
        print("8. Sliders de config OK:", cfg)

        # 9. desconectar
        await page.click("#disconnectBtn")
        disc = await page.evaluate("window.__disconnect")
        assert disc is True, "disconnect nao chamado"
        await page.evaluate("onSessionEnd()")
        idle_visible = await page.is_visible("#idle")
        assert idle_visible, "deveria voltar para tela inicial"
        print("9. Desconectar e voltar ao idle OK")

        # 10. sessao como host: mostra banner, sem toolbar
        await page.evaluate("onSessionStart({role:'host', peer:'XXXX-XXXX'})")
        banner_visible = await page.is_visible("#hostBanner")
        toolbar_hidden = await page.is_visible("#toolbar") is False
        assert banner_visible and toolbar_hidden, "host deveria mostrar banner sem toolbar"
        print("10. Sessao host OK (banner visivel, toolbar oculta)")

        print("\nERROS JS:", errors if errors else "nenhum")
        print("\nTODOS OS TESTES DA INTERFACE PASSARAM")
        await browser.close()


asyncio.run(main())