#!/usr/bin/env python3
"""Testes das combinações (dois botões juntos), com teclado falso e relógio controlado.

    python3 -m unittest discover -s tests -v      # na raiz do repositório

Medido no macropad (2026-11-14): botões apertados "juntos" chegam com 8 ms entre os apertos e
cada soltura chega na hora certa; dois toques rápidos separados chegam com 16-72 ms.
"""

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from test_gestures import K, Pad, as_json, down, tapped, up  # também põe daemon/ e ui/ no sys.path

import gestures as gm  # noqa: E402
import macropad_claude as mc  # noqa: E402
import server  # noqa: E402

mc.KEY_DELAY = 0

CHORDS = """
[[profiles]]
name = "Claude"
match = [{ app = "term" }]
[profiles.keys]
F13 = { label = "a", action = "combo", combo = "a" }
F14 = { label = "b", action = "combo", combo = "b", long = { label = "esc", action = "combo", combo = "esc" } }
F15 = { label = "bksp", action = "combo", combo = "backspace", mode = "while_held" }
F16 = { label = "d", action = "combo", combo = "d" }
F17 = { label = "e", action = "combo", combo = "e" }
F18 = { label = "Global", action = "profile", profile = "Global" }
[profiles.chords]
"F15+F16" = { action = "none" }
"F17+F16" = { label = "fixa Global", action = "profile", profile = "Global" }

[[profiles]]
name = "Firefox"
match = [{ app = "firefox" }]
[profiles.keys]
F13 = { label = "nova aba", action = "combo", combo = "ctrl+t" }
F14 = { label = "nova janela", action = "combo", combo = "ctrl+n" }
[profiles.chords]
"F13+F14" = { label = "janela privada", action = "combo", combo = "ctrl+shift+p" }

[[profiles]]
name = "Global"
fallback = true
match = []
[profiles.keys]
F13 = { label = "g", action = "combo", combo = "g" }
F14 = { label = "h", action = "combo", combo = "h" }
F21 = { label = "janela", action = "combo", combo = "alt+tab", mode = "switcher" }
[profiles.chords]
"F13+F14" = { label = "x", action = "combo", combo = "x", shared = true }
"F15+F16" = { label = "y", action = "combo", combo = "y", shared = true }
"F13+F16" = { label = "shift", action = "combo", combo = "shift", mode = "latch", shared = true }
"F13+F17" = { label = "ciclo", action = "text", text = ["um", "dois"], mode = "cycle", shared = true }
"F14+F18" = { label = "voz", action = "combo", combo = "alt+k", mode = "toggle", shared = true }
"""


def chord(p: Pad, a: str, b: str, gap: float = 0.008, hold: float = 0.08):
    """Aperta a e b juntos (b `gap` s depois), segura e solta os dois."""
    p.press(a)
    p.press(b, gap)
    p.release(a, hold)
    p.release(b, 0.008)


class ChordParseTest(unittest.TestCase):
    def parse(self, chords: str, timing: str = ""):
        return mc.parse_config(tomllib.loads(
            f'{timing}\n[[profiles]]\nname = "Global"\nfallback = true\nmatch = []\n[profiles.chords]\n{chords}\n'))

    def error(self, chords: str, timing: str = "") -> str:
        with self.assertRaises(ValueError) as cm:
            self.parse(chords, timing)
        return str(cm.exception)

    def test_pairs_are_sorted_and_none_disables(self):
        conf = mc.parse_config(tomllib.loads(CHORDS))
        claude, firefox, glob = conf["profiles"]
        self.assertIsNone(claude["chords"][(K["F15"], K["F16"])])
        self.assertEqual(claude["chords"][(K["F16"], K["F17"])]["profile"], "Global")  # "F17+F16" -> F16+F17
        self.assertEqual(firefox["chords"][(K["F13"], K["F14"])]["label"], "janela privada")
        self.assertEqual(glob["chords"][(K["F13"], K["F17"])]["names"], ["um", "dois"])
        self.assertEqual(conf["chord_ms"], gm.CHORD_MS)

    def test_errors(self):
        combo = '{ action = "combo", combo = "a" }'
        self.assertIn("só os botões 1 a 6", self.error(f'"F13+F19" = {combo}'))
        self.assertIn("só os botões 1 a 6", self.error(f'"F20+F13" = {combo}'))
        self.assertIn("dois botões diferentes", self.error(f'"F13+F13" = {combo}'))
        self.assertIn("exatamente dois botões", self.error(f'"F13" = {combo}'))
        self.assertIn("exatamente dois botões", self.error(f'"F13+F14+F15" = {combo}'))
        self.assertIn("repetida", self.error(f'"F13+F14" = {combo}\n"F14+F13" = {combo}'))
        self.assertIn("não vale numa combinação",
                      self.error('"F13+F14" = { action = "combo", combo = "a", mode = "while_held" }'))
        self.assertIn("não vale numa combinação",
                      self.error('"F13+F14" = { action = "combo", combo = "alt+tab", mode = "switcher" }'))
        self.assertIn("nem 'toque duplo'",
                      self.error('"F13+F14" = { action = "combo", combo = "a", double = { action = "combo", combo = "b" } }'))
        self.assertIn("perfil inexistente", self.error('"F13+F14" = { action = "profile", profile = "Nada" }'))
        self.assertIn("atalho vazio", self.error('"F13+F14" = { action = "combo", combo = "" }'))
        self.assertIn("chord_ms", self.error(f'"F13+F14" = {combo}', "[timing]\nchord_ms = 5"))

    def test_shared_must_be_bool(self):
        self.assertIn("shared", self.error('"F13+F14" = { action = "combo", combo = "a", shared = "sim" }'))
        conf = self.parse('"F13+F14" = { action = "combo", combo = "a", shared = true }\n"F13+F15" = { action = "combo", combo = "b" }')
        self.assertEqual(conf["profiles"][0]["shared"], {(K["F13"], K["F14"])})

    def test_timing(self):
        self.assertEqual(self.parse('"F13+F14" = { action = "combo", combo = "a" }', "[timing]\nchord_ms = 80")["chord_ms"], 80)

    def test_gestures_helpers(self):
        self.assertEqual(gm.parse_chord("f14 + f13"), ("F13", "F14"))
        self.assertEqual(gm.chord_name("F18", "F15"), "F15+F18")
        self.assertEqual(gm.meta()["chord_keys"], ["F13", "F14", "F15", "F16", "F17", "F18"])
        self.assertEqual(gm.meta()["chord_modes"], ["once", "toggle", "latch", "cycle"])


class ChordTest(unittest.TestCase):
    def test_fires_on_second_press_and_swallows_both(self):
        p = Pad(CHORDS)
        p.press("F13")
        self.assertEqual(p.out(), [])          # espera o outro botão
        p.press("F14", 0.008)
        self.assertEqual(p.out(), tapped("X"))  # no 2º aperto; "a" e "b" não rodam
        p.release("F13", 0.08)
        p.release("F14", 0.008)
        p.advance(1)
        self.assertEqual(p.out(), [])

    def test_any_order(self):
        p = Pad(CHORDS)
        chord(p, "F14", "F13")
        self.assertEqual(p.out(), tapped("X"))

    def test_window_edge(self):
        p = Pad(CHORDS)
        chord(p, "F13", "F14", gap=0.05)       # no limite: ainda é combinação
        self.assertEqual(p.out(), tapped("X"))

    def test_profile_overrides_global(self):
        p = Pad(CHORDS, window="firefox")
        chord(p, "F13", "F14")
        self.assertEqual(p.out(), tapped("LEFTCTRL", "LEFTSHIFT", "P"))

    def test_inherited_from_global(self):
        p = Pad(CHORDS, window="outro")
        chord(p, "F13", "F14")
        self.assertEqual(p.out(), tapped("X"))

    def test_disabled_in_profile_and_buttons_without_chords_are_instant(self):
        p = Pad(CHORDS)
        p.press("F15")                         # F15+F16 desligada no Claude: nenhuma outra com o F15
        self.assertEqual(p.out(), down("BACKSPACE"))
        p.press("F16", 0.008)                  # sem combinação: o F16 (que tem outras) espera...
        self.assertEqual(p.out(), [])
        p.advance(0.051)                       # ...e roda sozinho
        self.assertEqual(p.out(), tapped("D"))
        p.release("F15")
        self.assertEqual(p.out(), up("BACKSPACE"))

    def test_partner_too_late_runs_both(self):
        p = Pad(CHORDS)
        p.press("F13")
        p.advance(0.049)
        self.assertEqual(p.out(), [])
        p.advance(0.002)                       # passou de 50 ms: o "a" roda
        self.assertEqual(p.out(), tapped("A"))
        p.press("F14", 0.01)                   # com o F13 ainda apertado: o F14 vale sozinho
        p.release("F14", 0.1)
        self.assertEqual(p.out(), tapped("B"))
        p.release("F13")
        self.assertEqual(p.out(), [])

    def test_released_before_partner_is_tap(self):
        p = Pad(CHORDS)
        p.press("F13")
        p.release("F13", 0.03)                 # toque curtíssimo: roda na hora de soltar
        self.assertEqual(p.out(), tapped("A"))
        p.press("F14", 0.01)                   # dentro dos 50 ms, mas o F13 já foi solto
        p.release("F14", 0.1)
        self.assertEqual(p.out(), tapped("B"))

    def test_other_button_flushes_the_waiting_one_first(self):
        p = Pad(CHORDS)
        p.press("F13")
        p.press("F15", 0.01)                   # F13+F15 não é combinação
        self.assertEqual(p.out(), tapped("A") + down("BACKSPACE"))

    def test_long_counts_from_the_original_press(self):
        p = Pad(CHORDS)
        p.press("F14")                         # b / segurar = esc; espera o par
        p.advance(0.06)
        self.assertEqual(p.out(), [])
        p.advance(0.28)                        # 340 ms do aperto
        self.assertEqual(p.out(), [])
        p.advance(0.02)                        # 360 ms: segurar
        self.assertEqual(p.out(), tapped("ESC"))
        p.release("F14", 0.1)
        self.assertEqual(p.out(), [])

    def test_latch_chord_same_chord_turns_off(self):
        p = Pad(CHORDS)
        chord(p, "F13", "F16")
        self.assertEqual(p.out(), down("LEFTSHIFT"))
        p.press("F17", 0.5)                    # outras ações rodam com o shift travado
        p.advance(0.06)
        self.assertEqual(p.out(), tapped("E"))
        p.release("F17", 0.05)
        chord(p, "F16", "F13")                 # a mesma combinação destrava
        self.assertEqual(p.out(), up("LEFTSHIFT"))

    def test_weak_toggle_chord_released_by_next_action(self):
        p = Pad(CHORDS)
        chord(p, "F14", "F18")
        self.assertEqual(p.out(), down("LEFTALT", "K"))
        p.press("F15", 0.5)                    # a próxima ação desliga o interruptor antes
        self.assertEqual(p.out(), up("K", "LEFTALT") + down("BACKSPACE"))

    def test_cycle_chord(self):
        p = Pad(CHORDS)
        chord(p, "F13", "F17")
        self.assertEqual(p.out(), tapped("U") + tapped("M"))
        chord(p, "F17", "F13")
        self.assertEqual(p.out(), tapped("D") + tapped("O") + tapped("I") + tapped("S"))

    def test_profile_lock_by_chord_unlocks_by_same_chord(self):
        p = Pad(CHORDS)
        chord(p, "F16", "F17")
        self.assertEqual(p.core.locked, "Global")
        self.assertEqual(p.core.lock_key, ((K["F16"], K["F17"]), "chord"))
        chord(p, "F13", "F14", hold=0.1)       # travado no Global: o F13+F14 do Global
        self.assertEqual(p.out(), tapped("X"))
        chord(p, "F17", "F16")                 # o Global não tem F16+F17, mas ela destrava
        self.assertIsNone(p.core.locked)
        self.assertEqual(p.profile, "Claude")
        self.assertEqual(p.out(), [])

    def test_global_chord_not_shared_only_in_global(self):
        """Sem shared = true, a combinação do Global vale só nele: os outros perfis não herdam."""
        text = CHORDS.replace('"F13+F14" = { label = "x", action = "combo", combo = "x", shared = true }',
                              '"F13+F14" = { label = "x", action = "combo", combo = "x" }')
        p = Pad(text, window="outro")          # Global: vale
        chord(p, "F13", "F14")
        self.assertEqual(p.out(), tapped("X"))
        p = Pad(text)                          # Claude: os botões fazem a ação de cada um
        chord(p, "F13", "F14")
        p.advance(0.1)
        self.assertEqual(p.out(), tapped("A") + tapped("B"))
        p = Pad(text, window="firefox")        # Firefox tem a sua: continua valendo
        chord(p, "F13", "F14")
        self.assertEqual(p.out(), tapped("LEFTCTRL", "LEFTSHIFT", "P"))

    def test_events(self):
        p = Pad(CHORDS)
        chord(p, "F13", "F14")
        ev = p.events[-1]
        self.assertEqual((ev["key"], ev["gesture"], ev["label"], ev["profile"]), ("F13+F14", "chord", "x", "Claude"))
        self.assertTrue(ev["executed"])

    def test_test_mode_recognizes_without_executing(self):
        p = Pad(CHORDS)
        p.core.set_test_mode(True)
        chord(p, "F13", "F14")
        self.assertEqual(p.out(), [])
        self.assertEqual([(ev["key"], ev["gesture"]) for ev in p.events], [("F13+F14", "chord")])

    def test_knob_fake_release_while_waiting(self):
        """Girar o knob com um botão apertado: o macropad manda "soltou" do botão e o giro juntos."""
        p = Pad(CHORDS)
        p.press("F13")
        p.advance(0.02)
        p.core.on_key(K["F13"], 0, p.t)        # o "soltou" falso...
        p.core.on_key(K["F21"], 1, p.t)        # ...e o giro, no mesmo instante
        self.assertEqual(p.out(), tapped("A") + down("LEFTALT") + tapped("TAB"))
        p.press("F14", 0.1)                    # durante o alternador, outro botão só confirma (sem esperar)
        self.assertEqual(p.out(), up("LEFTALT"))
        p.press("F13", 0.008)                  # o F14 foi engolido: não forma combinação
        p.advance(0.06)
        self.assertEqual(p.out(), tapped("A"))

    def test_timeout_and_reset(self):
        p = Pad(CHORDS)
        p.press("F13")
        self.assertAlmostEqual(p.core.timeout(), 0.05)
        p.core.reset()
        self.assertIsNone(p.core.chord_wait)
        p.advance(1)
        p.release("F13")
        self.assertEqual(p.out(), [])

    def test_reload_changes_window(self):
        p = Pad(CHORDS)
        p.core.reload(mc.parse_config(tomllib.loads("[timing]\nchord_ms = 100\n" + CHORDS)))
        chord(p, "F13", "F14", gap=0.09)
        self.assertEqual(p.out(), tapped("X"))


class ChordServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = server.CONFIG
        server.CONFIG = Path(self.tmp.name) / "config.json"
        server.CONFIG.write_text(as_json(CHORDS), encoding="utf-8")

    def tearDown(self):
        server.CONFIG = self.orig
        self.tmp.cleanup()

    def roundtrip(self, text: str) -> str:
        server.CONFIG.write_text(text, encoding="utf-8")
        return server.dump_config(server.read_config())

    @staticmethod
    def chords(text: str):
        return [(p["chords"], p["shared"]) for p in mc.parse_config(json.loads(text))["profiles"]]

    @staticmethod
    def written(out: str) -> list[dict]:
        """"chords" de cada perfil no JSON gravado."""
        return [p.get("chords", {}) for p in json.loads(out)["profiles"]]

    def test_roundtrip(self):
        out = self.roundtrip(as_json(CHORDS))
        server.validate(out)
        self.assertEqual(self.chords(out), self.chords(as_json(CHORDS)))  # mesmo significado
        self.assertEqual(self.roundtrip(out), out)                          # e estável
        claude, firefox, glob = self.written(out)
        self.assertEqual(firefox["F13+F14"], {"label": "janela privada", "action": "combo", "combo": "ctrl+shift+p"})
        self.assertEqual(claude["F15+F16"], {"action": "none"})      # desliga a do Global no Claude
        self.assertEqual(claude["F16+F17"], {"label": "fixa Global", "action": "profile", "profile": "Global"})
        self.assertEqual(glob["F13+F17"], {"label": "ciclo", "action": "text", "text": ["um", "dois"], "mode": "cycle",
                                           "shared": True})
        self.assertEqual(list(glob), ["F13+F14", "F13+F16", "F13+F17", "F14+F18", "F15+F16"])  # em ordem

    def test_ui_shape_and_meta(self):
        claude, firefox, glob = server.read_config()["profiles"]
        self.assertEqual(list(claude["chords"]), ["F15+F16", "F16+F17"])
        self.assertEqual(claude["chords"]["F16+F17"]["tap"]["profile"], "Global")
        self.assertEqual(claude["chords"]["F15+F16"]["tap"]["action"], "none")
        self.assertEqual(glob["chords"]["F13+F17"]["tap"]["items"], ["um", "dois"])
        self.assertFalse(firefox["chords"]["F13+F14"]["inherit"])
        self.assertEqual(gm.meta()["chord_ms"], 50)

    def test_none_only_written_when_it_disables_a_global_chord(self):
        cfg = server.read_config()
        nothing = {"inherit": False, "tap": dict(server.GESTURE_FIELDS, items=[]), "long": None, "double": None}
        cfg["profiles"][1]["chords"]["F15+F17"] = nothing      # o Global não tem: não escreve
        cfg["profiles"][2]["chords"]["F13+F18"] = nothing      # no Global: não escreve
        cfg["profiles"][1]["chords"]["F14+F18"] = nothing      # desliga a do Global no Firefox
        out = server.dump_config(cfg)
        self.assertNotIn("F15+F17", out)
        self.assertNotIn("F13+F18", out)
        self.assertEqual(self.written(out)[1]["F14+F18"], {"action": "none"})

    def test_shared_only_in_global(self):
        cfg = server.read_config()
        claude, firefox, glob = cfg["profiles"]
        self.assertTrue(glob["chords"]["F13+F14"]["shared"])
        self.assertFalse(firefox["chords"]["F13+F14"]["shared"])
        firefox["chords"]["F13+F14"]["shared"] = True                  # fora do Global: não escreve
        glob["chords"]["F15+F16"]["shared"] = False                    # só no Global agora...
        w_claude, w_firefox, w_glob = self.written(server.dump_config(cfg))
        self.assertEqual(w_firefox["F13+F14"], {"label": "janela privada", "action": "combo", "combo": "ctrl+shift+p"})
        self.assertEqual(w_glob["F15+F16"], {"label": "y", "action": "combo", "combo": "y"})
        self.assertNotIn("F15+F16", w_claude)                          # ...e o "Nada" do Claude não serve mais

    def test_invalid_mode_and_timing(self):
        cfg = server.read_config()
        cfg["timing"] = {"chord_ms": 70}
        self.assertEqual(json.loads(server.dump_config(cfg))["timing"], {"chord_ms": 70})
        cfg["profiles"][0]["chords"]["F16+F17"]["tap"] = dict(server.GESTURE_FIELDS, items=[], action="combo",
                                                              combo="a", mode="while_held")
        with self.assertRaises(ValueError) as cm:
            server.dump_config(cfg)
        self.assertIn("não vale numa combinação", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
