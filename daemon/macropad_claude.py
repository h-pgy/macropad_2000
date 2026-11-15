#!/usr/bin/env python3
"""macropad-claude: transforma as teclas F13-F21 do macropad em ações, por perfil.

O macropad é programado para mandar só F13..F21. Este daemon:
  * captura (grab) o macropad, para que F13+ não cheguem ao GNOME/aplicativos;
  * escolhe o perfil pela janela em foco (informada pela extensão do GNOME);
  * reconhece o gesto de cada botão e executa a ação daquele gesto, do jeito do modo dela,
    pelo teclado/mouse virtual (uinput). Tabelas e regras em gestures.py:
      gestos: tap (tocar), long (segurar), double (toque duplo)
      ações:  text, combo, profile, scroll, launch, url, none
      modos:  once, toggle (interruptor fraco: a próxima ação desliga), latch (trava: só o mesmo
              gesto desliga), while_held (enquanto apertado), cycle (lista), switcher (Alt+Tab)
    Botão ausente num perfil usa o do perfil fallback (Global), com todos os gestos;
  * perfis: automático (pela janela em foco), fixo (profile + latch; o mesmo gesto do mesmo botão
    destrava) e por um toque (profile + toggle: a próxima ação roda nele);
  * combinações (chords): dois botões apertados juntos (até chord_ms entre os apertos) têm ação
    própria, por perfil; as do Global com shared = true valem também nos outros perfis. Só botões: girar o knob com um botão segurado chega como
    "soltou" desse botão (no mesmo instante), mas botão + botão chega certo;
  * repassa qualquer outra tecla do macropad sem alteração;
  * reconecta sozinho se o macropad for desplugado;
  * conta os macros executados (só números, por dia/perfil/botão/gesto) em
    ~/.local/state/macropad-claude/usage.db, para a aba Uso da UI;
  * abre um socket Unix (JSON por linha) usado pela UI e pela extensão:
      daemon -> clientes: status, key, focus, reload, mode, captured,
                          launch / open_url / capture / capture_cancel (só para a extensão)
      clientes -> daemon: reload, test_mode, status, lock, capture, capture_cancel,
                          hello (extensão), focus (extensão), captured (extensão)

Uso:
  macropad_claude.py [--config config.json] [--debug]
"""

import argparse
import json
import os
import re
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import evdev
    from evdev import ecodes as e
except ImportError:
    sys.exit("python-evdev não encontrado. Instale com: sudo apt install python3-evdev")

VENDOR_ID = 0x1189
PRODUCT_ID = 0x8890
KEY_DELAY = 0.008  # segundos entre teclas digitadas (terminais não gostam de rajadas)
RESCAN_SECONDS = 2
SWITCHER_RELEASE_MS = 900
MAX_RECENTS = 12
# Estatísticas de uso (aba Uso da UI): contadores por dia, perfil e botão, guardados para sempre.
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "macropad-claude"
USAGE_DB = STATE_DIR / "usage.db"
USAGE_FLUSH_SECONDS = 10
UI_TITLE_PREFIX = "Macropad 2000"  # janelas da própria UI não entram nas recentes
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "macropad-claude.sock"

# Gestos × ações × modos: tabelas e validação (sem evdev; a UI também usa).
import gestures as gm  # noqa: E402

MODIFIERS = {
    "ctrl": e.KEY_LEFTCTRL,
    "shift": e.KEY_LEFTSHIFT,
    "alt": e.KEY_LEFTALT,
    "super": e.KEY_LEFTMETA,
}
MODIFIER_CODES = set(MODIFIERS.values())

NAMED_KEYS = {
    "enter": e.KEY_ENTER, "esc": e.KEY_ESC, "escape": e.KEY_ESC, "tab": e.KEY_TAB,
    "space": e.KEY_SPACE, "backspace": e.KEY_BACKSPACE, "delete": e.KEY_DELETE,
    "left": e.KEY_LEFT, "right": e.KEY_RIGHT, "up": e.KEY_UP, "down": e.KEY_DOWN,
    "home": e.KEY_HOME, "end": e.KEY_END, "pageup": e.KEY_PAGEUP, "pagedown": e.KEY_PAGEDOWN,
    "minus": e.KEY_MINUS, "grave": e.KEY_GRAVE, "super": e.KEY_LEFTMETA,
    "ctrl": e.KEY_LEFTCTRL, "shift": e.KEY_LEFTSHIFT, "alt": e.KEY_LEFTALT,  # sozinhos (ex.: trava de shift)
    "playpause": e.KEY_PLAYPAUSE, "nextsong": e.KEY_NEXTSONG, "previoussong": e.KEY_PREVIOUSSONG,
    "volumeup": e.KEY_VOLUMEUP, "volumedown": e.KEY_VOLUMEDOWN, "mute": e.KEY_MUTE,
}

# ---------------------------------------------------------------- parsing


def key_name(code: int) -> str:
    """keycode -> 'F13' (sem o prefixo KEY_)."""
    name = e.KEY.get(code, str(code))
    if isinstance(name, list):
        name = name[0]
    return name.removeprefix("KEY_")


def ctl_name(code: int | tuple) -> str:
    """Botão (keycode) ou combinação (par de keycodes) -> 'F13' ou 'F13+F14'."""
    return "+".join(key_name(c) for c in code) if isinstance(code, tuple) else key_name(code)


def key_code(name: str) -> int:
    """Nome de tecla -> keycode evdev. Aceita 'a', '1', 'tab', 'f5' ou 'KEY_XXX'."""
    n = name.strip()
    if n.upper().startswith("KEY_") and hasattr(e, n.upper()):
        return getattr(e, n.upper())
    low = n.lower()
    if low in NAMED_KEYS:
        return NAMED_KEYS[low]
    if len(low) == 1 and (low.isalpha() or low.isdigit()):
        return getattr(e, f"KEY_{low.upper()}")
    if low.startswith("f") and low[1:].isdigit() and hasattr(e, f"KEY_F{low[1:]}"):
        return getattr(e, f"KEY_F{low[1:]}")
    raise ValueError(f"tecla desconhecida: {name!r}")


COMBO_NAMES = {code: name for name, code in reversed(NAMED_KEYS.items())}  # "esc", não "escape"


def combo_key_name(code: int) -> str:
    """keycode -> nome aceito por key_code(): 'a', '1', 'f5', 'esc' ou 'KEY_COMMA'."""
    if code in COMBO_NAMES and code not in MODIFIER_CODES:
        return COMBO_NAMES[code]
    name = key_name(code)
    if len(name) == 1 and name.isalnum():
        return name.lower()
    if re.fullmatch(r"F\d{1,2}", name):
        return name.lower()
    return f"KEY_{name}"


def parse_combo(combo: str) -> list[int]:
    """'alt+p' -> [KEY_LEFTALT, KEY_P] (modificadores primeiro). Modificador sozinho vale ('shift')."""
    parts = [p.strip() for p in combo.lower().split("+") if p.strip()]
    if not parts:
        raise ValueError("atalho vazio")
    *mods, key = parts
    try:
        codes = [MODIFIERS[m] for m in mods]
    except KeyError as err:
        raise ValueError(f"modificador desconhecido em {combo!r}: {err}") from None
    return codes + [key_code(key)]


def parse_text(text: str, slash_key: int) -> list[list[int]]:
    """Texto -> lista de combos. Só suporta o que é igual em US e ABNT2 (+ a barra)."""
    if not text:
        raise ValueError("texto vazio")
    out = []
    for ch in text:
        if ch == "/":
            out.append([slash_key])
        elif ch == "\n":
            out.append([e.KEY_ENTER])
        elif ch == " ":
            out.append([e.KEY_SPACE])
        elif ch == "-":
            out.append([e.KEY_MINUS])
        elif ch.isascii() and ch.isalpha():
            code = getattr(e, f"KEY_{ch.upper()}")
            out.append([e.KEY_LEFTSHIFT, code] if ch.isupper() else [code])
        elif ch.isascii() and ch.isdigit():
            out.append([getattr(e, f"KEY_{ch}")])
        else:
            raise ValueError(f"caractere não suportado no texto: {ch!r} (use letras sem acento, números, espaço, / e -)")
    return out


def normalize_url(url: str) -> str:
    """'youtube.com' -> 'https://youtube.com'. Só http(s): nada de file://, javascript: etc."""
    url = url.strip()
    if not url:
        raise ValueError("digite o endereço do site")
    if any(c.isspace() for c in url):
        raise ValueError(f"endereço com espaço: {url!r}")
    if "://" not in url:
        url = "https://" + url
    scheme, _, rest = url.partition("://")
    if scheme.lower() not in ("http", "https") or not rest:
        raise ValueError(f"o endereço precisa ser http:// ou https:// ({url!r})")
    return url


def parse_gesture(spec: dict, slash_key: int) -> dict:
    """Gesto canônico (gm.canonical) -> ação pronta para executar. Levanta ValueError legível.

    Saída: {"kind", "mode", "label"} + combo (teclas) | seq (texto) | items/names (cycle) |
    hold/tap (switcher) | amount | app/new_window | url | profile.
    """
    kind, mode = spec["action"], spec["mode"]
    a = {"kind": kind, "mode": mode, "label": spec["label"]}
    if mode == "cycle":
        field = gm.PAYLOAD[kind][0]
        raw = spec.get(field)
        items = [str(x) for x in raw] if isinstance(raw, list) else []
        items = [x.strip() if kind == "combo" else x for x in items if str(x).strip()]
        if len(items) < 2:
            raise ValueError("o modo alternar (cycle) precisa de uma lista com pelo menos 2 itens")
        a["names"] = items
        # cada item vira uma sequência de combos: o atalho sozinho ou as teclas do texto
        a["items"] = [[parse_combo(x)] if kind == "combo" else parse_text(x, slash_key) for x in items]
        return a
    if gm.PAYLOAD[kind] and isinstance(spec.get(gm.PAYLOAD[kind][0]), list):
        raise ValueError(f"lista em {gm.PAYLOAD[kind][0]!r} só no modo alternar (cycle)")
    if kind == "text":
        a["seq"] = parse_text(str(spec.get("text", "")), slash_key)
    elif kind == "combo":
        a["combo"] = parse_combo(str(spec.get("combo", "")))
        if mode == "switcher":
            if a["combo"][0] not in MODIFIER_CODES or len(a["combo"]) < 2:
                raise ValueError("o atalho do alternador precisa começar com um modificador (ex.: alt+tab)")
            a["hold"] = a["combo"][0]
            a["tap"] = a["combo"][1:]
    elif kind == "scroll":
        amount = int(spec.get("amount", 0))
        if amount == 0:
            raise ValueError("rolar precisa de uma quantidade diferente de 0")
        a["amount"] = amount
    elif kind == "launch":
        app = str(spec.get("app", "")).strip()
        if not app.endswith(".desktop"):
            raise ValueError("escolha um aplicativo")
        a["app"] = app
        a["new_window"] = bool(spec.get("new_window", False))
    elif kind == "url":
        a["url"] = normalize_url(str(spec.get("url", "")))
    elif kind == "profile":
        a["profile"] = str(spec.get("profile") or "").strip()
        if not a["profile"]:
            raise ValueError("escolha o perfil a acionar")
    return a


def gesture_where(code: int | tuple, gesture: str) -> str:
    """'F14', 'F14 (ao segurar)' ou 'F13+F14' (combinação), para mensagens."""
    return ctl_name(code) + ("" if gesture in ("tap", "chord") else f" ({gm.GESTURE_LABELS[gesture]})")



RULE_FIELDS = ("app", "wm_class", "title", "title_regex")


def parse_rule(rule: dict) -> dict:
    r = {k: str(rule[k]).strip() for k in RULE_FIELDS if str(rule.get(k, "")).strip()}
    if not r:
        raise ValueError("regra vazia (preencha app, wm_class, título ou regex do título)")
    if "title_regex" in r:
        try:
            r["title_re"] = re.compile(r["title_regex"], re.IGNORECASE)
        except re.error as err:
            raise ValueError(f"regex do título inválida ({r['title_regex']!r}): {err}") from None
    return r


def parse_chords(p: dict, name: str, slash_key: int) -> tuple[dict, set]:
    """[profiles.chords] -> ({(keycode, keycode): ação | None}, {pares com shared = true}).
    None = desligada neste perfil (action = "none": tira a combinação herdada do Global).
    shared = true só tem efeito no Global: a combinação vale também nos outros perfis."""
    chords, shared = {}, set()
    for cname, spec in (p.get("chords") or {}).items():
        try:
            if not isinstance(spec, dict):
                raise ValueError("esperava uma tabela { action = ... }")
            pair = tuple(key_code(k) for k in gm.parse_chord(cname))
            if pair in chords:
                raise ValueError("combinação repetida")
            if "long" in spec or "double" in spec:
                raise ValueError("combinação não tem 'segurar' nem 'toque duplo'")
            if not isinstance(spec.get("shared", False), bool):
                raise ValueError("shared é true ou false")
            if spec.get("shared"):
                shared.add(pair)
            g = gm.canonical(spec)
            gm.check_chord(g)
            chords[pair] = None if g["action"] == "none" else parse_gesture(g, slash_key)
        except ValueError as err:
            raise ValueError(f"[{name}] combinação {cname}: {err}") from None
    return chords, shared


def parse_config(cfg: dict) -> dict:
    """Dicionário do config.json -> {"profiles", "fallback", "long_ms", "double_ms", "chord_ms"}. ValueError legível.

    Cada perfil: {"name", "rules", "buttons": {keycode: {"tap": ação|None, "long": ..., "double": ...}},
    "chords": {(keycode, keycode): ação|None}, "shared": {pares do Global que valem nos outros perfis}}.
    Aceita também o formato antigo (uma tabela [keys] sem perfis) como perfil único.
    """
    slash_key = key_code(cfg.get("layout", {}).get("slash_key", "KEY_RO"))
    timing = cfg.get("timing", {})
    try:
        long_ms = int(timing.get("long_ms", gm.LONG_MS))
        double_ms = int(timing.get("double_ms", gm.DOUBLE_MS))
        chord_ms = int(timing.get("chord_ms", gm.CHORD_MS))
    except (TypeError, ValueError):
        raise ValueError("[timing] long_ms, double_ms e chord_ms precisam ser números (milissegundos)") from None
    if not (100 <= long_ms <= 3000 and 50 <= double_ms <= 1000):
        raise ValueError("[timing] use long_ms entre 100 e 3000 e double_ms entre 50 e 1000")
    if not 10 <= chord_ms <= 300:
        raise ValueError("[timing] use chord_ms entre 10 e 300")
    raw_profiles = cfg.get("profiles")
    if raw_profiles is None:
        raw_profiles = [{"name": "Global", "fallback": True, "keys": cfg.get("keys", {})}]
    if not raw_profiles:
        raise ValueError("nenhum perfil definido")
    profiles, fallback = [], None
    for i, p in enumerate(raw_profiles):
        name = str(p.get("name") or f"Perfil {i + 1}")
        if p.get("fallback"):
            if fallback is not None:
                raise ValueError("só um perfil pode ser o padrão (fallback)")
            fallback = i
        buttons = {}
        for fkey, spec in p.get("keys", {}).items():
            try:
                if not isinstance(spec, dict):
                    raise ValueError("esperava uma tabela { action = ... }")
                code = key_code(fkey)
                gestures = gm.canonical_key(spec)
                if gestures is None:  # inherit
                    continue
                gm.check_key(key_name(code), gestures)
                buttons[code] = {g: parse_gesture(a, slash_key) if a else None for g, a in gestures.items()}
            except ValueError as err:
                raise ValueError(f"[{name}] {fkey}: {err}") from None
        chords, shared = parse_chords(p, name, slash_key)
        try:
            rules = [parse_rule(r) for r in p.get("match", [])]
        except ValueError as err:
            raise ValueError(f"[{name}] {err}") from None
        profiles.append({"name": name, "rules": rules, "buttons": buttons, "chords": chords, "shared": shared})
    if fallback is None:
        fallback = len(profiles) - 1
    names = {p["name"] for p in profiles}
    for p in profiles:
        acts = [(code, g, a) for code, b in p["buttons"].items() for g, a in b.items()]
        acts += [(pair, "chord", a) for pair, a in p["chords"].items()]
        for code, g, a in acts:
            if a and a["kind"] == "profile" and a["profile"] not in names:
                where = f"combinação {ctl_name(code)}" if g == "chord" else gesture_where(code, g)
                raise ValueError(f"[{p['name']}] {where}: perfil inexistente {a['profile']!r}")
    return {"profiles": profiles, "fallback": fallback, "long_ms": long_ms, "double_ms": double_ms,
            "chord_ms": chord_ms}


def load_config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("o config precisa ser um objeto JSON ({ ... })")
    return parse_config(raw)


def rule_matches(rule: dict, win: dict) -> bool:
    if "app" in rule and (win.get("app") or "").lower() != rule["app"].lower():
        return False
    if "wm_class" in rule and (win.get("wm_class") or "").lower() != rule["wm_class"].lower():
        return False
    if "title" in rule and rule["title"].lower() not in (win.get("title") or "").lower():
        return False
    if "title_re" in rule and not rule["title_re"].search(win.get("title") or ""):
        return False
    return True


def pick_profile(conf: dict, win: dict | None) -> int:
    if win and (win.get("app") or win.get("wm_class") or win.get("title")):
        for i, p in enumerate(conf["profiles"]):
            if i != conf["fallback"] and any(rule_matches(r, win) for r in p["rules"]):
                return i
    return conf["fallback"]


def normalize_title(title: str) -> str:
    """Tira o prefixo animado (spinner/✳) que o Claude Code põe no título."""
    return re.sub(r"^[^\w]+", "", title or "").strip()


# ---------------------------------------------------------------- apps


def desktop_dirs() -> list[Path]:
    data = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")]
    dirs += [Path(d) for d in data.split(":") if d]
    dirs += [Path("/var/lib/snapd/desktop"), Path("/var/lib/flatpak/exports/share"),
             Path.home() / ".local/share/flatpak/exports/share"]
    seen, out = set(), []
    for d in dirs:
        p = d / "applications"
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def find_desktop_file(app_id: str) -> Path | None:
    for d in desktop_dirs():
        p = d / app_id
        if p.is_file():
            return p
        # ids com "-" podem vir de subpastas (ex.: kde4-foo.desktop -> kde4/foo.desktop)
        alt = d / app_id.replace("-", "/", 1)
        if alt.is_file():
            return alt
    return None


# ---------------------------------------------------------------- execução


class Runner:
    """Executa as ações no teclado/mouse virtual, do jeito do modo de cada uma.

    Estados ligados:
      active[(botão, gesto)]: toggle/latch ligados. O mesmo gesto do mesmo botão desliga (o Core
                              põe essa ação no lugar da do perfil ao montar os gestos do botão).
      weak:                   o (botão, gesto) do interruptor fraco ligado (no máximo um).
      pressed[botão]:         while_held: o soltar do botão chama, mesmo se o perfil mudou.
      sticky:                 modificador segurado pelo switcher.
    A ação "profile" não mexe no teclado: chama os ganchos do Core (self.hooks).
    """

    def __init__(self, debug: bool = False, kbd=None, mouse=None):
        self.debug = debug
        self.test_mode = False
        self.kbd = kbd if kbd is not None else evdev.UInput(name="macropad-claude virtual keyboard")
        self.mouse = mouse if mouse is not None else evdev.UInput(
            {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
             e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_WHEEL_HI_RES]},
            name="macropad-claude virtual mouse")
        self.active: dict[tuple, dict] = {}   # (botão, gesto) -> {"act", "stop", "after", "event"}
        self.weak: tuple | None = None
        self.pressed: dict[int, callable] = {}  # botão -> desliga o while_held e devolve o evento
        self.cycle_pos: dict[tuple, int] = {}  # (perfil, botão, gesto) -> próximo item do cycle
        self.sticky: int | None = None
        self.sticky_deadline = 0.0
        self.launcher = None                 # callable(app, new_window) -> bool (extensão)
        self.url_opener = None               # callable(url) -> bool (extensão)
        self.hooks = None                    # Core: latch, oneshot_on, oneshot_off
        self.emit = lambda event: None       # eventos extras para a UI (ex.: fraco desligado)

    def log(self, msg: str):
        if self.debug:
            print(msg, flush=True)

    def _emit(self, code: int, value: int):
        self.kbd.write(e.EV_KEY, code, value)
        self.kbd.syn()

    def tap(self, combo: list[int]):
        for c in combo:
            self._emit(c, 1)
        time.sleep(KEY_DELAY)
        for c in reversed(combo):
            self._emit(c, 0)
        time.sleep(KEY_DELAY)

    def type_seq(self, seq: list[list[int]]):
        for combo in seq:
            self.tap(combo)

    def forward_key(self, code: int, value: int):
        """Tecla não mapeada: repassa como veio."""
        if not self.test_mode:
            self._emit(code, value)

    def release_sticky(self):
        if self.sticky is not None:
            self._emit(self.sticky, 0)
            self.log("  switcher: solto")
            self.sticky = None

    def stop(self, key: tuple, event: dict | None = None):
        """Desliga um toggle/latch. Com `event`, marca nele; sem, manda um evento próprio."""
        entry = self.active.pop(key, None)
        if entry is None:
            return
        if self.weak == key:
            self.weak = None
        entry["stop"]()
        self.log(f"  {gesture_where(*key)}: desligado")
        done = {"hold": "released"}
        if entry["act"]["kind"] == "profile":
            done["profile_op"] = "oneshot_off"
        if event is not None:
            event.update(done)
        else:
            self.emit({**entry["event"], **done, "time": time.time()})

    def release_all(self):
        """Solta tudo (reload, desconexão, modo teste). A trava de perfil fica (é do Core)."""
        self.release_sticky()
        for key in list(self.active):
            self.stop(key)
        for code in list(self.pressed):
            self.emit(self.pressed.pop(code)())

    def timeout(self) -> float | None:
        """Segundos até o switcher soltar sozinho (para o select)."""
        if self.sticky is None:
            return None
        return max(0.0, self.sticky_deadline - time.monotonic())

    def tick(self):
        if self.sticky is not None and time.monotonic() >= self.sticky_deadline:
            self.release_sticky()

    def scroll(self, amount: int):
        # REL_WHEEL > 0 sobe; nossa convenção é amount > 0 desce
        step = -1 if amount > 0 else 1
        for _ in range(abs(amount)):
            self.mouse.write(e.EV_REL, e.REL_WHEEL, step)
            self.mouse.write(e.EV_REL, e.REL_WHEEL_HI_RES, step * 120)
            self.mouse.syn()
            time.sleep(0.004)

    def launch(self, app: str, new_window: bool) -> str:
        if self.launcher and self.launcher(app, new_window):
            return "extensão"
        path = find_desktop_file(app)
        if not path:
            self.log(f"  launch: {app} não encontrado")
            return "não encontrado"
        proc = subprocess.Popen(["gio", "launch", str(path)], start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=proc.wait, daemon=True).start()  # recolhe o filho: sem zumbi
        return "gio"

    def open_url(self, url: str) -> str:
        # pela extensão, o navegador recebe o foco; o gio é o plano B (pode abrir sem focar)
        if self.url_opener and self.url_opener(url):
            return "extensão"
        proc = subprocess.Popen(["gio", "open", url], start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=proc.wait, daemon=True).start()
        return "gio"

    # ---- ações: fire (uma vez) e start (liga; devolve a função que desliga)

    def fire(self, act: dict, event: dict):
        kind = act["kind"]
        if kind == "combo":
            self.tap(act["combo"])
        elif kind == "text":
            self.type_seq(act["seq"])
        elif kind == "scroll":
            self.scroll(act["amount"])
        elif kind == "launch":
            event["via"] = self.launch(act["app"], act["new_window"])
        elif kind == "url":
            event["via"] = self.open_url(act["url"])

    def start(self, act: dict) -> callable:
        if act["kind"] == "combo":
            combo = act["combo"]
            for c in combo:
                self._emit(c, 1)

            def stop():
                for c in reversed(combo):
                    self._emit(c, 0)
            return stop
        token = self.hooks.oneshot_on(act["profile"])  # profile + toggle: perfil por um toque
        return lambda: self.hooks.oneshot_off(token)

    # ---- modos

    def run(self, code: int, gesture: str, act: dict, prof: str, event: dict):
        """Executa a ação de um gesto já decidido e preenche o evento da UI."""
        key = (code, gesture)
        kind, mode = act["kind"], act["mode"]
        self.log(f"{gesture_where(code, gesture)} -> [{prof}] {act['label']} ({kind}, {mode})")
        weak_after = None
        if self.weak is not None and self.weak != key:
            # outra ação desliga o interruptor fraco: o de atalho antes (não mistura teclas), o de
            # perfil depois (a ação roda nele). Ligar outro fraco desliga o atual antes.
            if self.active[self.weak]["after"] and mode != "toggle":
                weak_after = self.weak
            else:
                self.stop(self.weak)
        if mode == "switcher":
            self._switcher(act)
        else:
            self.release_sticky()  # durante o alternador, outra ação confirma a escolha
            if mode == "once":
                self.fire(act, event)
            elif mode == "cycle":
                k = (prof, code, gesture)
                i = self.cycle_pos.get(k, 0) % len(act["items"])
                self.type_seq(act["items"][i])
                self.cycle_pos[k] = i + 1
                event["step"] = act["names"][i]
            elif kind == "profile" and mode == "latch":
                on = self.hooks.latch(act["profile"], key, act["label"])
                event["profile_op"] = "lock" if on else "unlock"
                event["target"] = act["profile"] if on else None
                if not on:
                    event["hold"] = "released"  # destravar não conta como uso
            elif mode in ("toggle", "latch"):
                if key in self.active:
                    self.stop(key, event)
                else:
                    self.active[key] = {"act": act, "stop": self.start(act), "after": kind == "profile",
                                        "event": event}
                    if mode == "toggle":
                        self.weak = key
                    event["hold"] = "holding"
                    if kind == "profile":
                        event["profile_op"] = "oneshot"
                        event["target"] = act["profile"]
                    self.log("  ligado")
            elif mode == "while_held":
                self._while_held(code, act, event)
        event["executed"] = kind != "none"
        if weak_after is not None and self.weak == weak_after:
            self.stop(weak_after)

    def _switcher(self, act: dict):
        if self.sticky is not None and self.sticky != act["hold"]:
            self.release_sticky()
        if self.sticky is None:
            self.sticky = act["hold"]
            self._emit(self.sticky, 1)
        self.tap(act["tap"])
        self.sticky_deadline = time.monotonic() + SWITCHER_RELEASE_MS / 1000

    def _while_held(self, code: int, act: dict, event: dict):
        if code in self.pressed:  # não deveria acontecer: solta o anterior
            self.emit(self.pressed.pop(code)())
        stop = self.start(act)
        event["hold"] = "holding"
        self.log("  segurando")

        def release() -> dict:
            stop()
            self.log("  solto")
            return {**event, "hold": "released", "time": time.time()}

        self.pressed[code] = release

    def forward(self, ev):
        if not self.test_mode:
            self.kbd.write_event(ev)
            self.kbd.syn()

    def close(self):
        self.release_all()
        self.kbd.close()
        self.mouse.close()


class Resolver:
    """Decide o gesto de cada botão: tap (tocar), long (segurar) ou double (toque duplo).

    Tempo em segundos, na mesma base dos timestamps dos eventos (time.time). Cada aperto guarda
    um snapshot dos gestos do botão (o perfil daquele momento). Devolve as decisões como
    [(botão, gesto, estado)], com estado["snap"] (gestos) e estado["prof"] (nome do perfil).
    """

    def __init__(self, long_ms: int = gm.LONG_MS, double_ms: int = gm.DOUBLE_MS):
        self.set_timing(long_ms, double_ms)
        self.pending: dict[int, dict] = {}

    def set_timing(self, long_ms: int, double_ms: int):
        self.long = long_ms / 1000
        self.double = double_ms / 1000

    def press(self, code: int, ts: float, snap: dict, prof: str) -> list[tuple]:
        out = []
        st = self.pending.pop(code, None)
        if st is not None and st["phase"] == "up":
            if ts - st["t_up"] <= self.double:
                return [(code, "double", st)]  # disparado no 2º aperto
            out.append((code, "tap", st))      # a janela venceu (o loop atrasou)
        elif st is not None:  # aperto sem soltar (não deveria acontecer): vale o toque anterior
            out.append((code, "tap", st))
        st = {"snap": snap, "prof": prof}
        if not (snap.get("long") or snap.get("double")):
            return out + [(code, "tap", st)]   # só toque: na hora, sem latência
        st.update(phase="down", t_down=ts, deadline=ts + self.long if snap.get("long") else None)
        self.pending[code] = st
        return out

    def release(self, code: int, ts: float) -> list[tuple]:
        st = self.pending.get(code)
        if st is None or st["phase"] != "down":
            return []
        snap = st["snap"]
        if snap.get("long") and ts - st["t_down"] >= self.long:
            del self.pending[code]  # segurou o bastante, mas o timer não chegou a rodar
            return [(code, "long", st)]
        if snap.get("double"):
            st.update(phase="up", t_up=ts, deadline=ts + self.double)
            return []
        del self.pending[code]
        return [(code, "tap", st)]

    def interrupt(self, other: int) -> list[tuple]:
        """Outro controle apertado: os pendentes viram toque (não esperam o segurar nem o 2º toque).
        Girando o knob com um botão segurado, o macropad ainda manda o "soltou" dele antes."""
        out = []
        for code, st in list(self.pending.items()):
            if code != other:
                del self.pending[code]
                out.append((code, "tap", st))
        return out

    def due(self, now: float) -> list[tuple]:
        out = []
        for code, st in list(self.pending.items()):
            if st["deadline"] is not None and now >= st["deadline"]:
                del self.pending[code]
                out.append((code, "long" if st["phase"] == "down" else "tap", st))
        return out

    def timeout(self, now: float) -> float | None:
        deadlines = [st["deadline"] for st in self.pending.values() if st["deadline"] is not None]
        return max(0.0, min(deadlines) - now) if deadlines else None

    def cancel(self):
        self.pending.clear()


class Core:
    """Perfil ativo + gestos + combinações + execução das teclas do macropad.

    Perfil ativo (choose): perfil por um toque > perfil fixo > janela em foco.
    Combinação: o 1º aperto de um botão que faz parte de alguma combinação espera até chord_ms pelo
    outro botão (chord_wait). Se ele vem, dispara a combinação no 2º aperto e as solturas dos dois
    são engolidas; se não vem (tempo, soltou, outro botão), o aperto segue normal, com o horário
    original (o "segurar" não atrasa).
    O main() liga os callbacks: on_event (evento de tecla para a UI e a contagem) e on_mode
    (mudança de perfil/modo; ext=True também avisa a extensão, com o popup).
    """

    def __init__(self, conf: dict, runner: Runner, clock=time.time):
        self.conf = conf
        self.runner = runner
        self.clock = clock
        self.resolver = Resolver(conf["long_ms"], conf["double_ms"])
        self.chord_s = conf["chord_ms"] / 1000
        self.chord_wait: dict | None = None  # 1º aperto de uma possível combinação: {"code", "ts", "deadline"}
        self.window: dict | None = None
        self.current = conf["fallback"]
        self.locked: str | None = None       # perfil fixo (nome)
        self.lock_key: tuple | None = None   # (botão, gesto) que destrava
        self.lock_label = ""
        self.oneshot: str | None = None      # perfil por um toque
        self.oneshot_token = None
        self.swallowed: set[int] = set()     # apertos já usados (confirmaram o alternador, combinação)
        self.forwarded: set[int] = set()     # teclas não mapeadas apertadas (repassadas)
        self.down: set[int] = set()          # botões fisicamente apertados agora
        self.on_event = lambda event: None
        self.on_mode = lambda msg, ext: None
        self.log = lambda msg: print(msg, flush=True)
        runner.hooks = self
        runner.emit = lambda event: self.on_event(event)

    # ---- perfis

    def profile_index(self, name: str | None) -> int | None:
        return next((i for i, p in enumerate(self.conf["profiles"]) if p["name"] == name), None)

    @property
    def profile_name(self) -> str:
        return self.conf["profiles"][self.current]["name"]

    def choose(self) -> int:
        for name in (self.oneshot, self.locked):
            i = self.profile_index(name)
            if i is not None:
                return i
        return pick_profile(self.conf, self.window)

    def refresh(self) -> bool:
        """Recalcula o perfil ativo. True se mudou."""
        new = self.choose()
        if new == self.current:
            return False
        self.runner.release_sticky()  # ex.: Alt do alternador segurado: confirma a troca
        self.current = new
        return True

    def set_window(self, win: dict | None) -> bool:
        self.window = win
        return self.refresh()

    def triggers(self) -> set[int]:
        return ({code for p in self.conf["profiles"] for code in p["buttons"]}
                | {code for p in self.conf["profiles"] for pair in p["chords"] for code in pair})

    def mode_msg(self, notify: bool) -> dict:
        return {"type": "mode", "locked": self.locked is not None, "locked_profile": self.locked,
                "profile": self.profile_name, "notify": notify, "oneshot": self.oneshot}

    def default_lock_key(self, target: str | None) -> tuple | None:
        """Trava vinda da UI/barra: destrava pelo (botão, gesto) que aciona esse perfil com trava."""
        for code in sorted(self.triggers()):
            for g in gm.GESTURES:
                for p in self.conf["profiles"]:
                    a = p["buttons"].get(code, {}).get(g)
                    if a and a["kind"] == "profile" and a["mode"] == "latch" and a["profile"] == target:
                        return (code, g)
        for p in self.conf["profiles"]:
            for pair, a in sorted(p["chords"].items()):
                if a and a["kind"] == "profile" and a["mode"] == "latch" and a["profile"] == target:
                    return (pair, "chord")
        return None

    def set_lock(self, target: str | None, source: str, key: tuple | None = None, label: str = ""):
        """Trava no perfil `target` (None = volta ao automático). Avisa a UI e a extensão (popup)."""
        if target is not None and self.profile_index(target) is None:
            return
        self.locked = target
        self.lock_key = (key or self.default_lock_key(target)) if target is not None else None
        self.lock_label = label if target is not None else ""
        self.runner.release_sticky()
        self.refresh()
        self.log(f"modo: {f'{target} fixo' if target else 'automático'} · perfil {self.profile_name} ({source})")
        self.on_mode(self.mode_msg(True), True)

    # ganchos do Runner (ação profile)

    def latch(self, target: str, key: tuple, label: str) -> bool:
        """profile + latch: trava; o mesmo gesto (ou acionar o perfil já travado) destrava."""
        if self.locked is not None and (key == self.lock_key or target == self.locked):
            self.set_lock(None, "tecla")
            return False
        self.set_lock(target, "tecla", key, label)
        return True

    def oneshot_on(self, target: str):
        token = object()
        self.oneshot, self.oneshot_token = target, token
        self._temp_changed()
        return token

    def oneshot_off(self, token):
        if self.oneshot_token is token:
            self.oneshot = self.oneshot_token = None
            self._temp_changed()

    def _temp_changed(self):
        # perfil por um toque: sem popup e sem mexer no ícone da extensão
        self.refresh()
        self.on_mode(self.mode_msg(False), False)

    # ---- teclas

    def snapshot(self, code: int) -> tuple[dict | None, str]:
        """Gestos do botão no perfil ativo (ou no Global). Um (botão, gesto) ligado sobrepõe a ação
        do perfil, para que o mesmo gesto sempre desligue (inclusive a trava de perfil)."""
        prof = self.conf["profiles"][self.current]
        b = prof["buttons"].get(code) or self.conf["profiles"][self.conf["fallback"]]["buttons"].get(code)
        overrides = {g: entry["act"] for (c, g), entry in self.runner.active.items() if c == code}
        if self.locked is not None and self.lock_key and self.lock_key[0] == code:
            overrides[self.lock_key[1]] = {"kind": "profile", "mode": "latch", "profile": self.locked,
                                           "label": self.lock_label or self.locked}
        if not overrides:
            return (dict(b) if b else None), prof["name"]
        return {"tap": None, "long": None, "double": None, **(b or {}), **overrides}, prof["name"]

    def chords(self) -> tuple[dict, str]:
        """Combinações do perfil ativo: as do Global marcadas shared (herdadas) com as do perfil por cima
        (None = desligada no perfil); no próprio Global, todas as dele. Uma combinação ligada
        (interruptor/trava) ou que destrava o perfil fixo sobrepõe a do perfil, para que a mesma
        combinação sempre desligue."""
        prof, glob = self.conf["profiles"][self.current], self.conf["profiles"][self.conf["fallback"]]
        inherited = {} if prof is glob else {pair: a for pair, a in glob["chords"].items() if pair in glob["shared"]}
        table = {**inherited, **prof["chords"]}
        table = {pair: act for pair, act in table.items() if act is not None}
        for (c, g), entry in self.runner.active.items():
            if g == "chord":
                table[c] = entry["act"]
        if self.locked is not None and self.lock_key and self.lock_key[1] == "chord":
            table[self.lock_key[0]] = {"kind": "profile", "mode": "latch", "profile": self.locked,
                                       "label": self.lock_label or self.locked}
        return table, prof["name"]

    def event(self, code: int | tuple, gesture: str, act: dict | None, prof: str) -> dict:
        return {"type": "key", "key": ctl_name(code), "gesture": gesture, "mapped": act is not None,
                "label": act["label"] if act else None, "action": act["kind"] if act else None,
                "mode": act["mode"] if act else None, "profile": prof, "executed": False,
                "test_mode": self.runner.test_mode, "time": time.time()}

    def execute(self, code: int, gesture: str, st: dict):
        act = st["snap"].get(gesture)
        if act is None:
            return
        ev = self.event(code, gesture, act, st["prof"])
        if self.runner.test_mode:
            self.runner.log(f"[teste] {gesture_where(code, gesture)} ({st['prof']})")
        else:
            self.runner.run(code, gesture, act, st["prof"], ev)
        self.on_event(ev)
        if code not in self.down and code in self.runner.pressed:
            # decidido depois do soltar (ex.: o gesto que destrava pôs um "segurar" no botão):
            # o while_held aperta e solta na hora, em vez de ficar preso
            self.on_event(self.runner.pressed.pop(code)())

    def on_key(self, code: int, value: int, ts: float):
        """Evento EV_KEY do macropad (value 1 = apertou, 0 = soltou, 2 = repetição)."""
        if value == 1:
            self.down.add(code)
            wait, self.chord_wait = self.chord_wait, None
            if wait is not None:
                pair = tuple(sorted((wait["code"], code)))
                table, prof = self.chords()
                if pair in table and ts - wait["ts"] <= self.chord_s:
                    for r in self.resolver.interrupt(code):
                        self.execute(*r)
                    self.swallowed.update(pair)  # as solturas dos dois não fazem nada
                    self.execute(pair, "chord", {"snap": {"chord": table[pair]}, "prof": prof})
                    return
                self.press(wait["code"], wait["ts"])  # não era combinação: o 1º aperto segue normal
            for r in self.resolver.interrupt(code):
                self.execute(*r)
            if self.runner.sticky is None and any(code in pair for pair in self.chords()[0]):
                # pode ser o 1º botão de uma combinação: espera o outro por até chord_ms
                self.chord_wait = {"code": code, "ts": ts, "deadline": ts + self.chord_s}
                return
            self.press(code, ts)
        elif value == 0:
            if self.chord_wait is not None and self.chord_wait["code"] == code:
                wait, self.chord_wait = self.chord_wait, None  # soltou antes do outro: toque normal
                self.press(code, wait["ts"])
            self.down.discard(code)
            if code in self.swallowed:
                self.swallowed.discard(code)
            elif code in self.forwarded:
                self.forwarded.discard(code)
                self.runner.forward_key(code, 0)
            else:
                for r in self.resolver.release(code, ts):
                    self.execute(*r)
                if code in self.runner.pressed:  # while_held: solta, mesmo se o perfil mudou
                    self.on_event(self.runner.pressed.pop(code)())
        elif code in self.forwarded:  # repetição: só nas teclas não mapeadas
            self.runner.forward_key(code, value)

    def press(self, code: int, ts: float):
        """Aperto de um botão que não é (ou não virou) combinação: gesto ou tecla repassada."""
        snap, prof = self.snapshot(code)
        if snap is None and code not in self.resolver.pending:
            self.forwarded.add(code)
            self.runner.forward_key(code, 1)
            self.on_event(self.event(code, "tap", None, prof))
            return
        tap = (snap or {}).get("tap")
        if self.runner.sticky is not None and not (tap and tap["mode"] == "switcher"):
            # durante o alternador, outro botão só confirma a escolha (o gesto inteiro é engolido)
            self.runner.release_sticky()
            self.swallowed.add(code)
            ev = self.event(code, "tap", tap, prof)
            ev["confirm"] = True
            self.on_event(ev)
            return
        for r in self.resolver.press(code, ts, snap, prof):
            self.execute(*r)

    def tick(self):
        wait = self.chord_wait
        if wait is not None and self.clock() > wait["deadline"]:  # no limite exato ainda vale
            self.chord_wait = None  # o outro botão não veio: aperto normal, com o horário original
            self.press(wait["code"], wait["ts"])
        for r in self.resolver.due(self.clock()):
            self.execute(*r)
        self.runner.tick()

    def timeout(self) -> float | None:
        now = self.clock()
        chord = max(0.0, self.chord_wait["deadline"] - now) if self.chord_wait else None
        ts = [t for t in (self.resolver.timeout(now), self.runner.timeout(), chord) if t is not None]
        return min(ts) if ts else None

    def reset(self):
        """Reload, desconexão, modo teste: cancela gestos pendentes e solta tudo (a trava fica)."""
        self.chord_wait = None
        self.resolver.cancel()
        self.runner.release_all()
        self.oneshot = self.oneshot_token = None
        self.swallowed.clear()
        for code in self.forwarded:
            self.runner.forward_key(code, 0)
        self.forwarded.clear()
        self.down.clear()
        self.current = self.choose()

    def set_test_mode(self, on: bool):
        self.reset()
        self.runner.test_mode = on

    def reload(self, conf: dict):
        self.reset()
        self.runner.cycle_pos.clear()
        self.conf = conf
        self.resolver.set_timing(conf["long_ms"], conf["double_ms"])
        self.chord_s = conf["chord_ms"] / 1000
        if self.profile_index(self.locked) is None:  # perfil fixo foi renomeado/excluído
            self.locked = self.lock_key = None
        elif self.lock_key is None:
            self.lock_key = self.default_lock_key(self.locked)
        self.current = self.choose()


# ---------------------------------------------------------------- estatísticas de uso

USAGE_SCHEMA = """CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL, profile TEXT NOT NULL, key TEXT NOT NULL, gesture TEXT NOT NULL DEFAULT 'tap',
    label TEXT NOT NULL, action TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY (day, profile, key, gesture, label, action))"""


class Usage:
    """Conta os macros executados. Só guarda números: uma linha por (dia, perfil, botão, gesto, nome, ação).

    Os toques ficam somados na memória e vão para o SQLite a cada USAGE_FLUSH_SECONDS (e ao sair),
    para não escrever em disco a cada tecla. Falha no SQLite só desliga a contagem, nunca o daemon.
    """

    def __init__(self, path: Path):
        self.path = path
        self.pending: dict[tuple, int] = {}
        self.last_flush = time.monotonic()
        self.db = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path)
            self.db.execute("PRAGMA journal_mode = WAL")
            self.migrate()
        except (OSError, sqlite3.Error) as err:
            print(f"estatísticas de uso desligadas: {err}", flush=True)
            self.db = None

    def migrate(self):
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(usage)")]
        if not cols or "gesture" in cols:
            self.db.execute(USAGE_SCHEMA)
            self.db.commit()
            return
        # tabela antiga, sem gesto: tudo vira "tap"; ações antigas viram combo (agora são modos)
        try:
            self.db.executescript(f"""
                BEGIN;
                ALTER TABLE usage RENAME TO usage_old;
                {USAGE_SCHEMA};
                INSERT INTO usage SELECT day, profile, key, 'tap', label,
                    CASE WHEN action IN ('hold_toggle', 'cycle', 'switcher') THEN 'combo' ELSE action END,
                    count FROM usage_old WHERE true
                    ON CONFLICT DO UPDATE SET count = count + excluded.count;
                DROP TABLE usage_old;
                COMMIT;
            """)
        except sqlite3.Error:
            self.db.rollback()
            raise
        print("estatísticas de uso: tabela migrada (coluna gesture)", flush=True)

    def count(self, event: dict):
        """Recebe o evento mandado para a UI; conta só o que de fato executou um macro."""
        if self.db is None or not event.get("executed") or event.get("test_mode") or event.get("system"):
            return
        if event.get("hold") == "released":  # toggle/latch/while_held: ligar + desligar = um uso
            return
        k = (time.strftime("%Y-%m-%d"), event.get("profile") or "", event.get("key") or "",
             event.get("gesture") or "tap", event.get("label") or "", event.get("action") or "")
        self.pending[k] = self.pending.get(k, 0) + 1

    def maybe_flush(self):
        if self.pending and time.monotonic() - self.last_flush >= USAGE_FLUSH_SECONDS:
            self.flush()

    def flush(self):
        self.last_flush = time.monotonic()
        if self.db is None or not self.pending:
            return
        rows, self.pending = self.pending, {}
        try:
            with self.db:
                self.db.executemany(
                    "INSERT INTO usage (day, profile, key, gesture, label, action, count)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT DO UPDATE SET count = count + excluded.count",
                    [(*k, n) for k, n in rows.items()])
        except sqlite3.Error as err:
            print(f"estatísticas de uso: não consegui gravar: {err}", flush=True)

    def close(self):
        self.flush()
        if self.db is not None:
            self.db.close()


# ---------------------------------------------------------------- socket


class Hub:
    """Socket Unix para a UI e a extensão: eventos JSON (uma linha cada) e comandos."""

    def __init__(self, path: Path):
        self.path = path
        path.unlink(missing_ok=True)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        os.chmod(path, 0o600)
        self.srv.listen(8)
        self.clients: dict[int, dict] = {}  # fd -> {"sock", "buf", "test", "role"}

    def fds(self) -> list[int]:
        return [self.srv.fileno(), *self.clients]

    def any_test(self) -> bool:
        return any(c["test"] for c in self.clients.values())

    def extension_fds(self) -> list[int]:
        return [fd for fd, c in self.clients.items() if c["role"] == "extension"]

    def send(self, fd: int, obj: dict):
        c = self.clients.get(fd)
        if not c:
            return
        try:
            c["sock"].sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        except OSError:
            self.drop(fd)

    def broadcast(self, obj: dict):
        for fd, c in list(self.clients.items()):
            if c["role"] != "extension":  # a extensão só recebe "launch"
                self.send(fd, obj)

    def drop(self, fd: int):
        c = self.clients.pop(fd, None)
        if c:
            c["sock"].close()

    def readable(self, fd: int) -> list[tuple[int, dict]]:
        """Trata um fd pronto. Devolve [(fd do cliente, comando)]."""
        if fd == self.srv.fileno():
            sock, _ = self.srv.accept()
            sock.settimeout(0.5)
            self.clients[sock.fileno()] = {"sock": sock, "buf": b"", "test": False, "role": "ui"}
            return [(sock.fileno(), {"cmd": "status"})]
        c = self.clients.get(fd)
        if c is None:  # caiu durante um broadcast neste mesmo ciclo
            return []
        try:
            chunk = c["sock"].recv(65536)
        except OSError:
            chunk = b""
        if not chunk:
            role = c["role"]
            self.drop(fd)
            return [(fd, {"cmd": "_disconnected", "role": role})]
        c["buf"] += chunk
        cmds = []
        while b"\n" in c["buf"]:
            line, c["buf"] = c["buf"].split(b"\n", 1)
            try:
                cmds.append((fd, json.loads(line)))
            except json.JSONDecodeError:
                pass
        return cmds

    def close(self):
        for fd in list(self.clients):
            self.drop(fd)
        self.srv.close()
        self.path.unlink(missing_ok=True)


def find_devices(triggers: set[int]) -> list:
    """Interfaces do macropad que conseguem mandar alguma das teclas mapeadas."""
    found = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if dev.info.vendor == VENDOR_ID and dev.info.product == PRODUCT_ID:
            keys = set(dev.capabilities().get(e.EV_KEY, []))
            if keys & triggers:
                found.append(dev)
                continue
        dev.close()
    return found


# ---------------------------------------------------------------- main


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=here.parent / "config.json")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    try:
        conf = load_config(args.config)
    except (OSError, ValueError, KeyError) as err:
        sys.exit(f"erro em {args.config}: {err}")

    runner = Runner(args.debug)
    core = Core(conf, runner)
    hub = Hub(SOCKET_PATH)
    usage = Usage(USAGE_DB)
    devices: list = []
    last_scan = 0.0
    recents: list[dict] = []
    capture_fd: int | None = None  # UI esperando o resultado de uma captura de atalho

    def emit(event: dict):
        usage.count(event)
        hub.broadcast(event)

    def send_mode_to_extension(msg: dict):
        for fd in hub.extension_fds():
            hub.send(fd, msg)

    def on_mode(msg: dict, ext: bool):
        hub.broadcast(msg)
        if ext:
            send_mode_to_extension(msg)

    core.on_event = emit
    core.on_mode = on_mode

    def ext_launch(app: str, new_window: bool) -> bool:
        fds = hub.extension_fds()
        for fd in fds:
            hub.send(fd, {"type": "launch", "app": app, "new_window": new_window})
        return bool(fds)

    runner.launcher = ext_launch

    def ext_open_url(url: str) -> bool:
        fds = [fd for fd in hub.extension_fds() if hub.clients[fd].get("open_url")]
        for fd in fds:  # extensão antiga (sem logout) não sabe abrir URL: cai no gio
            hub.send(fd, {"type": "open_url", "url": url})
        return bool(fds)

    runner.url_opener = ext_open_url

    def status() -> dict:
        return {"type": "status", "connected": bool(devices),
                "devices": [d.path for d in devices], "test_mode": runner.test_mode,
                "holding": bool(runner.active), "config": str(args.config.resolve()),
                "profiles": [p["name"] for p in core.conf["profiles"]],
                "profile": core.profile_name, "locked": core.locked is not None,
                "locked_profile": core.locked, "oneshot": core.oneshot, "window": core.window,
                "extension": bool(hub.extension_fds()), "recents": recents}

    def set_focus(win: dict | None):
        is_ui = (win or {}).get("title", "") and win["title"].startswith(UI_TITLE_PREFIX)
        if win and (win.get("app") or win.get("title")) and not is_ui:
            key = (win.get("app"), win.get("wm_class"), normalize_title(win.get("title")))
            recents[:] = [r for r in recents if (r["app"], r["wm_class"], normalize_title(r["title"])) != key]
            recents.insert(0, {**win, "time": time.time()})
            del recents[MAX_RECENTS:]
        changed = core.set_window(win)
        if changed:
            print(f"perfil: {core.profile_name}", flush=True)
        hub.broadcast({"type": "focus", "window": win, "profile": core.profile_name,
                       "locked": core.locked is not None, "locked_profile": core.locked, "changed": changed,
                       "recents": recents})

    def disconnect():
        nonlocal devices
        core.reset()
        for d in devices:
            try:
                d.close()
            except OSError:
                pass
        devices = []
        print("macropad desconectado, aguardando…", flush=True)
        hub.broadcast(status())

    def command(fd: int, cmd: dict):
        nonlocal devices, last_scan, capture_fd
        name = cmd.get("cmd")
        if name == "status":
            hub.send(fd, status())
        elif name == "hello" and fd in hub.clients:
            hub.clients[fd]["role"] = cmd.get("role", "ui")
            hub.clients[fd]["capture"] = bool(cmd.get("capture"))  # extensão antiga não sabe capturar
            hub.clients[fd]["open_url"] = bool(cmd.get("open_url"))
            print(f"cliente conectado: {hub.clients[fd]['role']}", flush=True)
            if hub.clients[fd]["role"] == "extension":  # restaura o ícone da barra, sem popup
                hub.send(fd, core.mode_msg(False))
            hub.broadcast(status())
        elif name == "lock":  # clique no ícone da barra ou na UI (sem "profile": o Global)
            fallback = core.conf["profiles"][core.conf["fallback"]]["name"]
            target = (cmd.get("profile") or fallback) if cmd.get("on") else None
            core.set_lock(target, hub.clients.get(fd, {}).get("role", "?"))
        elif name == "focus":
            set_focus({k: cmd.get(k) for k in ("app", "app_name", "wm_class", "title")})
        elif name == "reload":
            try:
                new_conf = load_config(args.config)
            except (OSError, ValueError, KeyError) as err:
                hub.send(fd, {"type": "reload", "ok": False, "error": str(err)})
            else:
                old_triggers = core.triggers()
                core.reload(new_conf)
                on_mode(core.mode_msg(False), True)
                if core.triggers() - old_triggers:  # teclas novas: recaptura o macropad
                    for d in devices:
                        try:
                            d.ungrab()
                            d.close()
                        except OSError:
                            pass
                    devices, last_scan = [], 0.0
                print(f"config recarregada: {len(core.conf['profiles'])} perfis", flush=True)
                hub.send(fd, {"type": "reload", "ok": True, "profiles": len(core.conf["profiles"])})
                hub.broadcast(status())
        elif name == "test_mode" and fd in hub.clients:
            hub.clients[fd]["test"] = bool(cmd.get("on"))
        elif name == "capture":  # UI: captura de atalho pela extensão (vê Ctrl+Alt+T etc.)
            ext = [efd for efd in hub.extension_fds() if hub.clients[efd].get("capture")]
            if not ext:
                hub.send(fd, {"type": "captured", "ok": False, "error": "no_extension"})
            else:
                capture_fd = fd
                for efd in ext:
                    hub.send(efd, {"type": "capture"})
        elif name == "capture_cancel":
            if capture_fd == fd:
                capture_fd = None
                for efd in hub.extension_fds():
                    hub.send(efd, {"type": "capture_cancel"})
        elif name == "captured":  # resposta da extensão
            reply = {"type": "captured", "ok": bool(cmd.get("ok"))}
            for k in ("cancelled", "timeout", "error"):
                if cmd.get(k):
                    reply[k] = cmd[k]
            if reply["ok"]:
                mods = [m for m in MODIFIERS if m in (cmd.get("mods") or [])]
                reply["combo"] = "+".join([*mods, combo_key_name(int(cmd.get("code", 0)))])
            if capture_fd is not None:
                hub.send(capture_fd, reply)
            capture_fd = None
        elif name == "_disconnected" and fd == capture_fd:  # a UI desistiu (aba fechada, timeout)
            capture_fd = None
            for efd in hub.extension_fds():
                hub.send(efd, {"type": "capture_cancel"})
        elif name == "_disconnected" and cmd.get("role") == "extension":
            print("extensão desconectada", flush=True)
            if capture_fd is not None and not hub.extension_fds():
                hub.send(capture_fd, {"type": "captured", "ok": False, "error": "no_extension"})
                capture_fd = None
            hub.broadcast(status())
        # test_mode e _disconnected podem mudar o modo teste
        test = hub.any_test()
        if test != runner.test_mode:
            core.set_test_mode(test)
            hub.broadcast(status())

    # systemctl stop manda SIGTERM: sai pelo finally (solta teclas, apaga o socket)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    print(f"macropad-claude: {len(conf['profiles'])} perfis, socket {SOCKET_PATH}", flush=True)
    try:
        while True:
            if not devices and time.monotonic() - last_scan > RESCAN_SECONDS:
                last_scan = time.monotonic()
                devices = find_devices(core.triggers())
                if devices:
                    try:
                        for d in devices:
                            d.grab()
                    except OSError as err:
                        print(f"não consegui capturar o macropad: {err}", flush=True)
                        disconnect()
                    else:
                        print("conectado: " + ", ".join(d.path for d in devices), flush=True)
                        hub.broadcast(status())
            devmap = {d.fd: d for d in devices}
            wait = RESCAN_SECONDS
            t = core.timeout()
            if t is not None:
                wait = min(wait, t)
            ready, _, _ = select.select([*devmap, *hub.fds()], [], [], wait)
            for fd in ready:
                if fd in devmap:
                    try:
                        events = list(devmap[fd].read())
                    except OSError:
                        disconnect()
                        break
                    for ev in events:
                        if ev.type == e.EV_KEY:
                            # o tempo do gesto vem do evento (o loop pode ter atrasado digitando um
                            # texto); timestamp em outra base de tempo -> usa a hora atual
                            now, ts = time.time(), ev.timestamp()
                            runner.log(f"evdev {key_name(ev.code)} {('solta', 'aperta', 'repete')[ev.value]} {ts:.3f}")
                            core.on_key(ev.code, ev.value, ts if 0 <= now - ts < 5 else now)
                        elif ev.type not in (e.EV_SYN, e.EV_MSC):
                            runner.forward(ev)
                else:
                    for cfd, cmd in hub.readable(fd):
                        command(cfd, cmd)
            core.tick()
            usage.maybe_flush()
    except KeyboardInterrupt:
        pass
    finally:
        usage.close()
        runner.close()
        hub.close()


if __name__ == "__main__":
    main()
