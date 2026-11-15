#!/usr/bin/env python3
"""Testes de gestos × ações × modos (daemon e UI), com teclado falso e relógio controlado.

    python3 -m unittest discover -s tests -v      # na raiz do repositório

Não manda tecla nenhuma para a tela: o uinput é trocado por um teclado falso.
"""

import json
import sqlite3
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "daemon"))
sys.path.insert(0, str(REPO / "ui"))

import gestures as gm  # noqa: E402
import macropad_claude as mc  # noqa: E402
from evdev import ecodes as e  # noqa: E402

mc.KEY_DELAY = 0  # sem pausa entre teclas nos testes

CONFIG = """
[[profiles]]
name = "Claude"
match = [{ app = "term" }]
[profiles.keys]
F13 = { label = "voz", action = "combo", combo = "alt+k", mode = "toggle" }
F14 = { label = "enter", action = "combo", combo = "enter", long = { label = "esc", action = "combo", combo = "esc" } }
F15 = { label = "bksp", action = "combo", combo = "backspace", mode = "while_held" }
F16 = { label = "c", action = "text", text = "c", double = { label = "x", action = "text", text = "x" } }
F17 = { label = "a", action = "combo", combo = "a", long = { label = "b", action = "combo", combo = "b" }, double = { label = "d", action = "combo", combo = "d" } }
F18 = { label = "Global", action = "profile", profile = "Global" }
F20 = { label = "fx", action = "profile", profile = "Firefox", mode = "toggle" }

[[profiles]]
name = "Firefox"
match = [{ app = "firefox" }]
[profiles.keys]
F13 = { label = "nova aba", action = "combo", combo = "ctrl+t" }
F18 = { label = "Global", action = "profile", profile = "Global" }

[[profiles]]
name = "Global"
fallback = true
match = []
[profiles.keys]
F13 = { label = "g", action = "combo", combo = "g" }
F14 = { label = "h", action = "combo", combo = "h" }
F17 = { label = "shift", action = "combo", combo = "shift", mode = "latch" }
F18 = { label = "copiar/colar", action = "combo", combo = ["ctrl+c", "ctrl+v"], mode = "cycle" }
F21 = { label = "janela", action = "combo", combo = "alt+tab", mode = "switcher" }
"""

K = {f"F{n}": getattr(e, f"KEY_F{n}") for n in range(13, 22)}


class FakeDev:
    def __init__(self):
        self.log = []

    def write(self, type_, code, value):
        if type_ == e.EV_KEY:
            self.log.append((code, value))

    def syn(self):
        pass

    def write_event(self, ev):
        pass

    def close(self):
        pass


def down(*names):
    return [(getattr(e, f"KEY_{n}"), 1) for n in names]


def up(*names):
    return [(getattr(e, f"KEY_{n}"), 0) for n in names]


def tapped(*names):
    return down(*names) + up(*reversed(names))


class Pad:
    """Core + Runner com teclado falso e relógio manual."""

    def __init__(self, toml: str = CONFIG, window: str | None = "term"):
        self.t = 1000.0
        conf = mc.parse_config(tomllib.loads(toml))
        self.kbd = FakeDev()
        self.runner = mc.Runner(kbd=self.kbd, mouse=FakeDev())
        self.core = mc.Core(conf, self.runner, clock=lambda: self.t)
        self.events, self.modes = [], []
        self.core.on_event = self.events.append
        self.core.on_mode = lambda msg, ext: self.modes.append((msg, ext))
        self.core.log = lambda msg: None
        if window:
            self.core.set_window({"app": window})

    def press(self, key, dt=0.0):
        self.advance(dt)
        self.core.on_key(K[key], 1, self.t)

    def release(self, key, dt=0.0):
        self.advance(dt)
        self.core.on_key(K[key], 0, self.t)

    def tap(self, key, hold=0.05):
        self.press(key)
        self.release(key, hold)

    def advance(self, dt):
        self.t += dt
        self.core.tick()

    def out(self):
        log, self.kbd.log = self.kbd.log, []
        return log

    @property
    def profile(self):
        return self.core.profile_name


# ---------------------------------------------------------------- parse


def conf_with(keys: str) -> dict:
    return tomllib.loads(f'[[profiles]]\nname = "Global"\nfallback = true\nmatch = []\n[profiles.keys]\n{keys}\n')


PAYLOADS = {"combo": 'combo = "ctrl+a"', "text": 'text = "oi"', "profile": 'profile = "Global"',
            "scroll": "amount = 3", "launch": 'app = "x.desktop"', "url": 'url = "a.com"', "none": ""}
LIST_PAYLOADS = {"combo": 'combo = ["ctrl+a", "ctrl+b"]', "text": 'text = ["a", "b"]'}


def gesture_spec(action, mode):
    payload = LIST_PAYLOADS.get(action, "") if mode == "cycle" else PAYLOADS[action]
    return ", ".join(x for x in (f'label = "x"', f'action = "{action}"', payload, f'mode = "{mode}"') if x)


class ParseTest(unittest.TestCase):
    def parse(self, keys):
        return mc.parse_config(conf_with(keys))

    def assertInvalid(self, keys, *fragments):
        with self.assertRaises(ValueError) as cm:
            self.parse(keys)
        for f in fragments:
            self.assertIn(f, str(cm.exception))

    def test_matrix(self):
        """Toda ação × modo × gesto: vale exatamente o que as tabelas permitem."""
        for action, payload in PAYLOADS.items():
            if action == "none":
                continue  # none em long/double = gesto ausente
            for mode in gm.MODES:
                if action == "profile" and mode == "while_held":
                    continue  # camada antiga: convertida para toggle (test_profile_layer_becomes_oneshot)
                for gesture in gm.GESTURES:
                    spec = gesture_spec(action, mode)
                    if action == "combo" and mode == "switcher":
                        spec = spec.replace("ctrl+a", "alt+tab")
                    keys = (f"F13 = {{ {spec} }}" if gesture == "tap"
                            else f'F13 = {{ action = "none", {gesture} = {{ {spec} }} }}')
                    ok = mode in gm.ACTIONS[action] and mode in gm.GESTURE_MODES[gesture]
                    with self.subTest(action=action, mode=mode, gesture=gesture):
                        if ok:
                            b = self.parse(keys)["profiles"][0]["buttons"][e.KEY_F13]
                            self.assertEqual((b[gesture]["kind"], b[gesture]["mode"]), (action, mode))
                        else:
                            self.assertRaises(ValueError, self.parse, keys)

    def test_cross_rules(self):
        self.assertInvalid('F13 = { action = "combo", combo = "a", mode = "while_held", long = { action = "text", text = "x" } }',
                           "F13", "não combina")
        self.assertInvalid('F13 = { action = "combo", combo = "a", mode = "while_held", double = { action = "text", text = "x" } }',
                           "não combina")
        self.assertInvalid('F13 = { action = "combo", combo = "alt+tab", mode = "switcher", double = { action = "text", text = "x" } }',
                           "não combina")
        self.parse('F13 = { action = "combo", combo = "a", mode = "toggle", long = { action = "text", text = "x" } }')

    def test_knob_turn(self):
        self.parse('F21 = { action = "combo", combo = "alt+tab", mode = "switcher" }\n'
                   'F19 = { action = "combo", combo = ["a", "b"], mode = "cycle" }')
        self.assertInvalid('F19 = { action = "combo", combo = "a", long = { action = "text", text = "x" } }', "girar o knob")
        self.assertInvalid('F21 = { action = "combo", combo = "a", mode = "toggle" }', "girar o knob", "interruptor")
        self.assertInvalid('F21 = { action = "combo", combo = "a", mode = "while_held" }', "girar o knob")

    def test_payload_errors(self):
        self.assertInvalid('F13 = { action = "combo", combo = ["a"], mode = "cycle" }', "pelo menos 2")
        self.assertInvalid('F13 = { action = "combo", combo = ["a", "b"] }', "só no modo alternar")
        self.assertInvalid('F13 = { action = "combo", combo = "a", mode = "switcher" }', "modificador")
        self.assertInvalid('F13 = { action = "combo", combo = "a", mode = "voar" }', "modo desconhecido")
        self.assertInvalid('F13 = { action = "voar" }', "ação desconhecida")
        self.assertInvalid('F13 = { action = "none", long = { action = "profile", profile = "Nada" } }',
                           "F13 (ao segurar)", "perfil inexistente")
        with self.assertRaises(ValueError):
            mc.parse_config({**conf_with(""), "timing": {"long_ms": 10}})

    def test_legacy(self):
        b = self.parse('F13 = { action = "combo", combo = "a", behavior = "hold" }\n'
                       'F14 = { action = "hold_toggle", combo = "alt+k" }\n'
                       'F15 = { action = "cycle", combos = ["ctrl+c", "ctrl+v"] }\n'
                       'F16 = { action = "switcher", combo = "alt+tab" }\n'
                       'F17 = { action = "profile", profile = "Global" }\n'
                       'F18 = { action = "combo", combo = "a", behavior = "tap" }')["profiles"][0]["buttons"]
        got = {mc.key_name(k): (v["tap"]["kind"], v["tap"]["mode"]) for k, v in b.items()}
        self.assertEqual(got, {"F13": ("combo", "while_held"), "F14": ("combo", "toggle"), "F15": ("combo", "cycle"),
                               "F16": ("combo", "switcher"), "F17": ("profile", "latch"), "F18": ("combo", "once")})
        self.assertEqual(b[e.KEY_F15]["tap"]["names"], ["ctrl+c", "ctrl+v"])

    def test_profile_layer_becomes_oneshot(self):
        """A camada (perfil enquanto apertado) saiu: config antigo vira perfil por um toque."""
        b = self.parse('F13 = { action = "none", long = { action = "profile", profile = "Global", mode = "while_held" } }')
        self.assertEqual(b["profiles"][0]["buttons"][e.KEY_F13]["long"]["mode"], "toggle")

    def test_inherit_and_timing(self):
        conf = mc.parse_config(tomllib.loads(
            '[timing]\nlong_ms = 500\ndouble_ms = 200\n'
            '[[profiles]]\nname = "A"\nmatch = []\n[profiles.keys]\nF13 = { action = "inherit" }\n'
            '[[profiles]]\nname = "Global"\nfallback = true\nmatch = []\n[profiles.keys]\nF13 = { action = "combo", combo = "a" }\n'))
        self.assertNotIn(e.KEY_F13, conf["profiles"][0]["buttons"])
        self.assertEqual((conf["long_ms"], conf["double_ms"]), (500, 200))


# ---------------------------------------------------------------- gestos


class GestureTest(unittest.TestCase):
    def test_tap_only_fires_on_press(self):
        p = Pad()
        p.press("F13")
        self.assertEqual(p.out(), down("LEFTALT", "K"))  # toggle: imediato, sem esperar o soltar
        p.release("F13", 0.1)
        p.press("F18", 0.1)  # Claude F18 = profile latch (só toque)
        self.assertEqual(p.core.locked, "Global")

    def test_long_before_and_after(self):
        p = Pad()
        p.press("F17")
        p.advance(0.2)
        self.assertEqual(p.out(), [])  # pendente
        p.release("F17", 0.05)          # soltou antes de 350 ms: espera o double
        self.assertEqual(p.out(), [])
        p.advance(0.3)                  # janela do double venceu: tap
        self.assertEqual(p.out(), tapped("A"))
        p.press("F17", 1)
        p.advance(0.4)                  # bateu o tempo ainda apertado: long
        self.assertEqual(p.out(), tapped("B"))
        p.release("F17", 0.5)
        p.advance(1)
        self.assertEqual(p.out(), [])
        self.assertEqual([ev["gesture"] for ev in p.events if ev["key"] == "F17"], ["tap", "long"])

    def test_long_detected_late_by_timestamp(self):
        p = Pad()
        p.press("F17")
        p.t += 0.5  # o loop ficou ocupado: o soltar chega sem o tick ter rodado
        p.core.on_key(K["F17"], 0, p.t)
        self.assertEqual(p.out(), tapped("B"))

    def test_double_inside_and_outside_window(self):
        p = Pad()
        p.tap("F16")
        p.press("F16", 0.1)  # 2º aperto dentro da janela: double, no aperto
        self.assertEqual(p.out(), tapped("X"))
        p.release("F16", 0.05)
        p.advance(1)
        self.assertEqual(p.out(), [])
        p.tap("F16")
        p.advance(0.3)  # janela venceu
        self.assertEqual(p.out(), tapped("C"))

    def test_long_and_double(self):
        p = Pad()
        p.tap("F17")
        p.press("F17", 0.1)
        self.assertEqual(p.out(), tapped("D"))
        p.release("F17", 0.6)  # o 2º toque segurado não vira long
        p.advance(1)
        self.assertEqual(p.out(), [])

    def test_hardware_knob_turn_with_button_held(self):
        """Girando o knob com o F14 segurado, o macropad manda "soltou F14" e o giro no mesmo instante
        (entre botões isso não acontece: ver test_chords.py)."""
        p = Pad()
        p.press("F14")            # enter / segurar = esc
        p.advance(0.1)
        p.core.on_key(K["F14"], 0, p.t)  # o "soltou" falso
        p.core.on_key(K["F21"], 1, p.t)  # alternador de janelas (Global)
        self.assertEqual(p.out(), tapped("ENTER") + down("LEFTALT") + tapped("TAB"))
        p.release("F21", 0.01)
        p.advance(1)              # o F14 físico ainda apertado não manda mais nada
        self.assertEqual(p.out(), [])

    def test_other_button_resolves_other_long_as_tap(self):
        p = Pad()
        p.press("F17")            # a / segurar = b (once): outro botão -> tap
        p.press("F18", 0.05)
        self.assertEqual(p.out(), tapped("A"))
        self.assertEqual(p.core.locked, "Global")
        p.release("F17")
        p.advance(1)
        self.assertEqual(p.out(), [])

    def test_other_button_during_double_window_is_tap(self):
        p = Pad()
        p.tap("F16")
        p.press("F15", 0.05)
        self.assertEqual(p.out(), tapped("C") + down("BACKSPACE"))

    def test_repeat_is_ignored(self):
        p = Pad()
        p.press("F15")
        p.core.on_key(K["F15"], 2, p.t)
        p.release("F15", 0.5)
        self.assertEqual(p.out(), down("BACKSPACE") + up("BACKSPACE"))

    def test_unmapped_key_is_forwarded(self):
        p = Pad()
        p.press("F19")
        p.core.on_key(K["F19"], 2, p.t)
        p.release("F19")
        self.assertEqual(p.out(), [(K["F19"], 1), (K["F19"], 2), (K["F19"], 0)])


# ---------------------------------------------------------------- modos


class ModeTest(unittest.TestCase):
    def test_weak_toggle_released_before_other_action(self):
        p = Pad()
        p.tap("F13")
        p.out()
        p.tap("F15")  # backspace enquanto apertado: o alt+k solta antes
        self.assertEqual(p.out(), up("K", "LEFTALT") + down("BACKSPACE") + up("BACKSPACE"))
        self.assertFalse(p.runner.active)
        released = [ev for ev in p.events if ev["key"] == "F13" and ev.get("hold") == "released"]
        self.assertEqual(len(released), 1)

    def test_weak_toggle_same_gesture_turns_off_even_in_other_profile(self):
        p = Pad()
        p.tap("F13")
        p.core.set_window({"app": "firefox"})
        p.out()
        p.tap("F13")  # no Firefox o F13 é ctrl+t, mas o mesmo gesto desliga a voz
        self.assertEqual(p.out(), up("K", "LEFTALT"))

    def test_latch_survives_other_actions(self):
        p = Pad(window=None)  # Global
        p.tap("F17")          # shift travado
        p.tap("F13")
        self.assertEqual(p.out(), down("LEFTSHIFT") + tapped("G"))
        p.tap("F17")
        self.assertEqual(p.out(), up("LEFTSHIFT"))

    def test_cycle(self):
        p = Pad(window=None)
        p.tap("F18")
        p.tap("F18")
        p.tap("F18")
        self.assertEqual(p.out(), tapped("LEFTCTRL", "C") + tapped("LEFTCTRL", "V") + tapped("LEFTCTRL", "C"))
        self.assertEqual([ev.get("step") for ev in p.events if ev["key"] == "F18"], ["ctrl+c", "ctrl+v", "ctrl+c"])

    def test_while_held_release_after_profile_change(self):
        p = Pad()
        p.press("F15")
        p.core.set_window({"app": "firefox"})
        p.release("F15", 0.3)
        self.assertEqual(p.out(), down("BACKSPACE") + up("BACKSPACE"))

    def test_switcher_and_confirm(self):
        p = Pad(window=None)
        p.tap("F21")
        p.tap("F21")
        self.assertEqual(p.out(), down("LEFTALT") + tapped("TAB") + tapped("TAB"))
        p.press("F13")  # outro botão só confirma (e o gesto inteiro é engolido)
        p.release("F13")
        self.assertEqual(p.out(), up("LEFTALT"))
        self.assertTrue(p.events[-1].get("confirm"))

    def test_events_have_gesture_and_mode(self):
        p = Pad()
        p.tap("F13")
        ev = p.events[-1]
        self.assertEqual((ev["key"], ev["gesture"], ev["action"], ev["mode"], ev["hold"]),
                         ("F13", "tap", "combo", "toggle", "holding"))


class ProfileTest(unittest.TestCase):
    def test_latch_not_undone_by_other_buttons(self):
        p = Pad()
        p.tap("F18")
        self.assertEqual((p.core.locked, p.profile), ("Global", "Global"))
        p.tap("F13")
        p.tap("F14")
        p.advance(1)
        self.assertEqual(p.core.locked, "Global")
        self.assertEqual(p.out(), tapped("G") + tapped("H"))
        p.tap("F18")  # no Global o F18 é copiar/colar, mas o mesmo gesto destrava
        self.assertEqual((p.core.locked, p.profile), (None, "Claude"))
        self.assertEqual(p.out(), [])

    def test_latch_by_long_unlocks_by_long_in_profile_without_long(self):
        toml = CONFIG.replace(
            'F17 = { label = "a", action = "combo", combo = "a", long = { label = "b", action = "combo", combo = "b" }',
            'F17 = { label = "a", action = "combo", combo = "a", long = { label = "trava", action = "profile", profile = "Global" }')
        p = Pad(toml)
        p.press("F17")
        p.advance(0.4)
        self.assertEqual(p.core.locked, "Global")
        p.release("F17", 0.1)
        p.press("F17", 1)  # no Global, F17 = shift (latch, só toque): o segurar continua destravando
        p.advance(0.4)
        self.assertIsNone(p.core.locked)
        p.release("F17")

    def test_unlock_gesture_does_not_leave_while_held_stuck(self):
        toml = CONFIG.replace(
            'F14 = { label = "enter", action = "combo", combo = "enter", long = { label = "esc", action = "combo", combo = "esc" } }',
            'F14 = { label = "enter", action = "combo", combo = "enter", long = { label = "trava", action = "profile", profile = "Global" } }'
        ).replace('F14 = { label = "h", action = "combo", combo = "h" }',
                  'F14 = { label = "bksp", action = "combo", combo = "backspace", mode = "while_held" }')
        p = Pad(toml)
        p.press("F14")
        p.advance(0.4)
        p.release("F14")
        self.assertEqual(p.core.locked, "Global")
        p.out()
        p.tap("F14")  # no Global: toque = backspace enquanto apertado, mas o segurar (destravar) foi injetado
        self.assertEqual(p.out(), down("BACKSPACE") + up("BACKSPACE"))
        self.assertEqual(p.core.locked, "Global")

    def test_oneshot_profile(self):
        p = Pad()
        p.tap("F20")  # perfil por um toque: Firefox
        self.assertEqual(p.profile, "Firefox")
        p.tap("F13")  # roda no Firefox e volta
        self.assertEqual(p.out(), tapped("LEFTCTRL", "T"))
        self.assertEqual(p.profile, "Claude")
        p.tap("F20")
        p.tap("F20")  # o mesmo gesto desliga
        self.assertEqual(p.profile, "Claude")
        self.assertEqual(p.out(), [])

    def test_oneshot_then_while_held_in_that_profile(self):
        p = Pad()
        p.tap("F20")
        p.press("F14")  # no Firefox o F14 vem do Global (h, só toque)
        self.assertEqual(p.out(), tapped("H"))
        self.assertEqual(p.profile, "Claude")
        p.release("F14")

    def test_lock_from_ui_unlocks_with_default_key(self):
        p = Pad(window=None)
        p.core.set_lock("Global", "ui")
        self.assertEqual(p.core.lock_key, (K["F18"], "tap"))
        p.tap("F18")
        self.assertIsNone(p.core.locked)


class ResetTest(unittest.TestCase):
    def test_reload_releases_and_cancels(self):
        p = Pad()
        p.tap("F13")
        p.press("F17")  # pendente
        p.out()
        p.core.reload(mc.parse_config(tomllib.loads(CONFIG)))
        self.assertEqual(p.out(), up("K", "LEFTALT"))
        p.release("F17", 0.1)
        p.advance(1)
        self.assertEqual(p.out(), [])

    def test_reload_keeps_lock(self):
        p = Pad()
        p.tap("F18")
        p.core.reload(mc.parse_config(tomllib.loads(CONFIG)))
        self.assertEqual(p.core.locked, "Global")

    def test_test_mode_resolves_but_does_not_execute(self):
        p = Pad()
        p.core.set_test_mode(True)
        p.press("F17")
        p.advance(0.4)
        p.release("F17")
        p.tap("F18")
        self.assertEqual(p.out(), [])
        self.assertIsNone(p.core.locked)
        self.assertEqual([(ev["key"], ev["gesture"], ev["test_mode"]) for ev in p.events[-2:]],
                         [("F17", "long", True), ("F18", "tap", True)])


class UsageTest(unittest.TestCase):
    def test_migration(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.db"
            db = sqlite3.connect(path)
            db.executescript("""
                CREATE TABLE usage (day TEXT NOT NULL, profile TEXT NOT NULL, key TEXT NOT NULL,
                    label TEXT NOT NULL, action TEXT NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY (day, profile, key, label, action));
                INSERT INTO usage VALUES ('2026-11-01', 'Global', 'F18', 'cc', 'cycle', 2);
                INSERT INTO usage VALUES ('2026-11-01', 'Global', 'F18', 'cc', 'combo', 3);
                INSERT INTO usage VALUES ('2026-11-01', 'Claude', 'F13', 'voz', 'hold_toggle', 4);
            """)
            db.commit()
            db.close()
            u = mc.Usage(path)
            u.count({"executed": True, "profile": "Global", "key": "F18", "gesture": "long", "label": "cc", "action": "combo"})
            u.count({"executed": True, "hold": "released", "profile": "Global", "key": "F18", "label": "cc", "action": "combo"})
            u.close()
            rows = sorted(sqlite3.connect(path).execute("SELECT profile, key, gesture, label, action, count FROM usage"))
            self.assertEqual(rows[:2], [("Claude", "F13", "tap", "voz", "combo", 4), ("Global", "F18", "long", "cc", "combo", 1)])
            self.assertEqual(rows[2], ("Global", "F18", "tap", "cc", "combo", 5))
            mc.Usage(path).close()  # 2ª abertura: já migrada, não mexe


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- server (config da UI)

import server  # noqa: E402


LEGACY_TOML = """
[layout]
slash_key = "KEY_RO"

[[profiles]]
name = "Claude"
match = [{ app = "term" }]

[profiles.keys]
F13 = { label = "voz", action = "hold_toggle", combo = "alt+k" }
F14 = { label = "bksp", action = "combo", combo = "backspace", behavior = "hold" }
F15 = { label = "cc", action = "cycle", combos = ["ctrl+c", "ctrl+v"] }
F16 = { label = "enter", action = "combo", combo = "enter", long = { label = "Fn", action = "profile", profile = "Global", mode = "while_held" }, double = { action = "text", text = "/clear\\n" } }
F18 = { label = "Global", action = "profile", profile = "Global" }
F21 = { label = "janela", action = "switcher", combo = "alt+tab" }

[[profiles]]
name = "Global"
fallback = true
match = []

[profiles.keys]
F13 = { label = "g", action = "combo", combo = "g", behavior = "tap" }
"""


def as_json(toml: str) -> str:
    """Os fixtures são escritos em TOML (mais curto); o config.json tem a mesma estrutura."""
    return json.dumps(tomllib.loads(toml), ensure_ascii=False)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = server.CONFIG
        server.CONFIG = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        server.CONFIG = self.orig
        self.tmp.cleanup()

    def roundtrip(self, text: str) -> str:
        server.CONFIG.write_text(text, encoding="utf-8")
        return server.dump_config(server.read_config())

    @staticmethod
    def buttons(text: str):
        """Botões de cada perfil, sem os vazios (o server grava as teclas vazias do Global como none)."""
        empty = lambda b: b["tap"]["kind"] == "none" and not b["long"] and not b["double"]  # noqa: E731
        return [{k: b for k, b in p["buttons"].items() if not empty(b)}
                for p in mc.parse_config(json.loads(text))["profiles"]]

    def test_current_config_roundtrip(self):
        text = self.orig.read_text(encoding="utf-8")
        out = self.roundtrip(text)
        server.validate(out)
        self.assertEqual(self.buttons(out), self.buttons(text))  # mesmo significado
        self.assertEqual(self.roundtrip(out), out)               # e estável
        self.assertEqual(out, text)                              # o arquivo do repo já está no formato da UI

    def test_legacy_written_canonical(self):
        legacy = as_json(LEGACY_TOML)
        out = self.roundtrip(legacy)
        server.validate(out)
        self.assertNotIn("behavior", out)
        self.assertNotIn("hold_toggle", out)
        keys = json.loads(out)["profiles"][0]["keys"]
        self.assertEqual(keys["F13"], {"label": "voz", "action": "combo", "combo": "alt+k", "mode": "toggle"})
        self.assertEqual(keys["F14"], {"label": "bksp", "action": "combo", "combo": "backspace", "mode": "while_held"})
        self.assertEqual(keys["F15"], {"label": "cc", "action": "combo", "combo": ["ctrl+c", "ctrl+v"], "mode": "cycle"})
        self.assertEqual(keys["F16"], {"label": "enter", "action": "combo", "combo": "enter",
                                       "long": {"label": "Fn", "action": "profile", "profile": "Global", "mode": "toggle"},
                                       "double": {"action": "text", "text": "/clear\n"}})
        self.assertEqual(keys["F18"], {"label": "Global", "action": "profile", "profile": "Global"})  # latch = padrão
        self.assertEqual(keys["F21"], {"label": "janela", "action": "combo", "combo": "alt+tab", "mode": "switcher"})
        self.assertEqual(self.buttons(out), self.buttons(legacy))

    def test_rejects_non_object(self):
        server.CONFIG.write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError):
            server.read_config()

    def test_ui_shape_and_meta(self):
        server.CONFIG.write_text(as_json(LEGACY_TOML), encoding="utf-8")
        cfg = server.read_config()
        claude, glob = cfg["profiles"]
        self.assertEqual(claude["keys"]["F15"]["tap"]["items"], ["ctrl+c", "ctrl+v"])
        self.assertEqual(claude["keys"]["F16"]["long"]["mode"], "toggle")  # camada antiga -> por um toque
        self.assertTrue(claude["keys"]["F17"]["inherit"])
        self.assertFalse(glob["keys"]["F17"]["inherit"])
        self.assertEqual(glob["keys"]["F17"]["tap"]["action"], "none")
        self.assertEqual(gm.meta()["actions"]["profile"], ["latch", "toggle"])

    def test_invalid_combination_is_refused(self):
        server.CONFIG.write_text(as_json(LEGACY_TOML), encoding="utf-8")
        cfg = server.read_config()
        cfg["profiles"][0]["keys"]["F14"]["long"] = dict(server.GESTURE_FIELDS, action="text", text="x")
        with self.assertRaises(ValueError) as cm:
            server.validate(server.dump_config(cfg))
        self.assertIn("não combina", str(cm.exception))
