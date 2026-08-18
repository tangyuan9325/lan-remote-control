"""
Input simulation using pynput.
Translates normalized coordinates (0.0-1.0) to absolute screen coordinates.
"""

import platform
from pynput.mouse import Controller as MouseController, Button
from pynput.keyboard import Controller as KeyboardController, Key


class InputSimulator:
    def __init__(self, screen_width: int, screen_height: int):
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.screen_width = screen_width
        self.screen_height = screen_height

    def _to_abs(self, nx: float, ny: float):
        x = int(max(0.0, min(1.0, nx)) * (self.screen_width - 1))
        y = int(max(0.0, min(1.0, ny)) * (self.screen_height - 1))
        return x, y

    def _button(self, name: str):
        return {
            "left": Button.left,
            "right": Button.right,
            "middle": Button.middle,
        }.get(name, Button.left)

    def mouse_move(self, nx: float, ny: float):
        self.mouse.position = self._to_abs(nx, ny)

    def mouse_down(self, nx: float, ny: float, button: str = "left"):
        self.mouse.position = self._to_abs(nx, ny)
        self.mouse.press(self._button(button))

    def mouse_up(self, nx: float, ny: float, button: str = "left"):
        self.mouse.position = self._to_abs(nx, ny)
        self.mouse.release(self._button(button))

    def mouse_click(self, nx: float, ny: float, button: str = "left"):
        self.mouse.position = self._to_abs(nx, ny)
        self.mouse.click(self._button(button), 1)

    def mouse_double(self, nx: float, ny: float, button: str = "left"):
        self.mouse.position = self._to_abs(nx, ny)
        self.mouse.click(self._button(button), 2)

    def mouse_scroll(self, dx: int, dy: int):
        # pynput scroll: positive dy = up, but clients usually send negative for down
        self.mouse.scroll(int(dx), int(dy))

    def key_down(self, key: str):
        self.keyboard.press(self._resolve_key(key))

    def key_up(self, key: str):
        self.keyboard.release(self._resolve_key(key))

    def key_type(self, text: str):
        self.keyboard.type(text)

    @staticmethod
    def _resolve_key(key: str):
        """Map a string key name to a pynput Key or char."""
        special = {
            "ctrl": Key.ctrl, "control": Key.ctrl,
            "alt": Key.alt, "option": Key.alt,
            "shift": Key.shift,
            "cmd": Key.cmd, "win": Key.cmd, "super": Key.cmd,
            "enter": Key.enter, "return": Key.enter,
            "esc": Key.esc, "escape": Key.esc,
            "tab": Key.tab,
            "space": Key.space, " ": Key.space,
            "backspace": Key.backspace,
            "delete": Key.delete, "del": Key.delete,
            "up": Key.up, "down": Key.down,
            "left": Key.left, "right": Key.right,
            "home": Key.home, "end": Key.end,
            "pageup": Key.page_up, "pagedown": Key.page_down,
            "capslock": Key.caps_lock,
            "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
            "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
            "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
        }
        if key.lower() in special:
            return special[key.lower()]
        if len(key) == 1:
            return key
        return key  # pynput will try to type it as a char
