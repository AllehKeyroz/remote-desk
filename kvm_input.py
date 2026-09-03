"""Captura de input no SERVER (compartilha teclado/mouse) - modelo Barrier.

- Thread de polling (60Hz): posiciona o cursor, detecta travessia de borda
  (jump zone) e, quando em modo "client", captura deltas do mouse com snap-back,
  alem dos botoes (GetAsyncKeyState).
- Hook WH_KEYBOARD_LL: em modo client encaminha as teclas pro client (engolindo
  as locais) e detecta a hotkey de escape (Ctrl+Alt+K).
- Hook WH_MOUSE_LL: apenas roda do mouse (wheel) em modo client.
- Esconde/mostra o cursor local ao atravessar.
"""

import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WH_MOUSE_LL_MSGS = {WM_MOUSEWHEEL, WM_MOUSEHWHEEL}
LLKHF_INJECTED = 0x10
VK_MENU = 0x12
VK_CONTROL = 0x11
VK_K = 0x4B
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wt.DWORD]
user32.CallNextHookEx.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int


def get_cursor() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def set_cursor(x: int, y: int) -> None:
    user32.SetCursorPos(x, y)


def hide_cursor() -> None:
    while user32.ShowCursor(0) >= 0:
        pass


def show_cursor() -> None:
    while user32.ShowCursor(1) < 0:
        pass


class KeyboardHook:
    """Hook de teclado de baixo nivel. Em modo client: engole e encaminha
    as teclas; detecta a hotkey de escape (Ctrl+Alt+K)."""

    def __init__(self, on_key=None, on_escape=None):
        self._on_key = on_key
        self._on_escape = on_escape
        self._enabled = False
        self._hook = ctypes.c_void_p()
        self._proc = None

    @staticmethod
    def _is_alt():
        return bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)

    @staticmethod
    def _is_ctrl():
        return bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)

    def _cb(self, nCode, wParam, lParam):
        try:
            if nCode >= 0:
                data = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                vk = data.vkCode
                injected = bool(data.flags & LLKHF_INJECTED)
                is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self._on_escape and is_down and vk == VK_K and self._is_alt() and self._is_ctrl():
                    self._on_escape()
                    return 1
                if not injected and self._on_key:
                    self._on_key(vk, is_down)
                if self._enabled and not injected:
                    return 1
            return int(user32.CallNextHookEx(self._hook, nCode, wParam, lParam) or 0)
        except Exception:
            return 0

    def _run(self):
        try:
            self._proc = HOOKPROC(self._cb)
            self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
            msg = wt.MSG()
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r in (0, -1) or r is None:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass

    def start(self, enabled=False):
        self._enabled = enabled
        threading.Thread(target=self._run, daemon=True).start()

    def set_enabled(self, v: bool):
        self._enabled = v

    def shutdown(self):
        self._enabled = False
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)



class MouseWheelHook:
    """Apenas roda do mouse - engole e reporta (em modo client)."""

    def __init__(self, on_wheel=None):
        self._on_wheel = on_wheel
        self._enabled = False
        self._hook = ctypes.c_void_p()
        self._proc = None

    def _cb(self, nCode, wParam, lParam):
        try:
            if nCode >= 0 and wParam in WH_MOUSE_LL_MSGS:
                data = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short(data.mouseData >> 16).value
                if self._enabled and self._on_wheel:
                    self._on_wheel(delta)
                return 1  # engole (nao rola apps locais)
            return int(user32.CallNextHookEx(self._hook, nCode, wParam, lParam) or 0)
        except Exception:
            return 0

    def _run(self):
        try:
            self._proc = HOOKPROC(self._cb)
            self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
            msg = wt.MSG()
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r in (0, -1) or r is None:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass

    def start(self, enabled=False):
        self._enabled = enabled
        threading.Thread(target=self._run, daemon=True).start()

    def set_enabled(self, v):
        self._enabled = v

    def shutdown(self):
        self._enabled = False
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)


class MouseCapture:
    """Thread de polling do cursor (60Hz).

    mode = "local"  : servidor controla o proprio PC; detecta travessia de borda.
    mode = "client" : mouse do servidor vira deltas pro client (snap-back no anchor).

    callbacks:
        on_cross_out(zona) -> quando o cursor entra na jump zone (0=left,1=right)
        on_first(anchor_x, anchor_y) -> ao entrar em modo client (posiciona cursor do client)
        on_rel(dx, dy) -> delta relativo
        on_button(button, down) -> botao (0=left,1=right,2=middle)
    """

    HZ = 60
    ZONE = 4  # jump zone em px (fino)

    def __init__(self, on_cross_out=None, on_first=None, on_rel=None, on_button=None):
        self._on_cross_out = on_cross_out
        self._on_first = on_first
        self._on_rel = on_rel
        self._on_button = on_button
        self.mode = "local"
        self.sw = 0
        self.sh = 0
        self._anchor = (0, 0)
        self._last_buttons = (False, False, False)
        self._running = True

    def start(self):
        self.sw = user32.GetSystemMetrics(0)
        self.sh = user32.GetSystemMetrics(1)
        threading.Thread(target=self._poll, daemon=True).start()

    def stop(self):
        self._running = False

    def shutdown(self):
        self._running = False
        if self.mode == "client":
            self.cross_to_local()

    def _poll(self):
        while self._running:
            x, y = get_cursor()
            if self.mode == "local":
                # deteccao de traversia: borda direita (1) ou esquerda (0)
                crossing = 0 if x <= self.ZONE else (1 if x >= self.sw - self.ZONE else None)
                if crossing is not None and self._on_cross_out:
                    self._on_cross_out(crossing)
            else:
                # modo client: delta + snap-back
                ax, ay = self._anchor
                dx, dy = x - ax, y - ay
                if (dx or dy) and self._on_rel:
                    self._on_rel(dx, dy)
                if dx or dy:
                    set_cursor(ax, ay)  # snap-back (cursor fica no anchor, escondido)
            # botoes
            b = (bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000),
                 bool(user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000),
                 bool(user32.GetAsyncKeyState(VK_MBUTTON) & 0x8000))
            for i in range(3):
                if b[i] != self._last_buttons[i] and self._on_button:
                    self._on_button(i, b[i])
            self._last_buttons = b
            time.sleep(1.0 / self.HZ)

    def cross_to_client(self):
        """Entra em modo client: trava o cursor no ponto atual, esconde, reporta first."""
        x, y = get_cursor()
        self._anchor = (x, y)
        self._last_buttons = (False, False, False)
        self.mode = "client"
        hide_cursor()
        if self._on_first:
            self._on_first(*self._anchor)

    def cross_to_local(self):
        """Sai do modo client: mostra cursor e volta a controlar o server."""
        self.mode = "local"
        show_cursor()
        # coloca o cursor na borda correspondente do server (o client ja posicionou)
        self._on_cross_in_x = 0

    def set_mode_local(self):
        self.mode = "local"

    def get_mode(self):
        return self.mode

