#!/usr/bin/env python3
"""UI web local do macropad-claude: perfis, teclas, teste ao vivo e saúde.

Só biblioteca padrão. Escuta em 127.0.0.1 e conversa com o daemon pelo socket Unix.

  python3 ui/server.py            # abre http://127.0.0.1:8765 no navegador
  python3 ui/server.py --no-browser --port 9000 --interval 30

Enquanto roda, refaz as verificações de saúde a cada --interval segundos e guarda o
histórico em SQLite (~/.local/state/macropad-claude/health.db, últimos 7 dias).
A aba Uso lê os contadores que o daemon grava em ~/.local/state/macropad-claude/usage.db.
"""

import argparse
import configparser
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config.json"
INDEX = Path(__file__).resolve().parent / "index.html"
SERVICE = "macropad-claude"
EXT_UUID = "macropad-claude@local"
EXT_DIR = Path.home() / ".local/share/gnome-shell/extensions" / EXT_UUID
SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "macropad-claude.sock"
VENDOR, PRODUCT = "1189", "8890"
CLAUDE_DIR = Path.home() / ".claude"

# Tabelas de gestos × ações × modos (sem dependências) e o parser do daemon para validar
# (este precisa do python3-evdev).
sys.path.insert(0, str(REPO / "daemon"))
import gestures as gm  # noqa: E402
try:
    import macropad_claude as mc
    MC_ERROR = None
except SystemExit as err:  # o módulo do daemon sai com mensagem se faltar o evdev
    mc, MC_ERROR = None, str(err)

KEYS = ["F13", "F14", "F15", "F16", "F17", "F18", "F19", "F20", "F21"]
PHYSICAL = {
    "F13": "Botão 1", "F14": "Botão 2", "F15": "Botão 3",
    "F16": "Botão 4", "F17": "Botão 5", "F18": "Botão 6",
    "F19": "Knob: girar para a esquerda", "F20": "Knob: apertar", "F21": "Knob: girar para a direita",
}
RULE_FIELDS = ("app", "wm_class", "title", "title_regex")

TIMING_KEYS = ("long_ms", "double_ms", "chord_ms")


# ---------------------------------------------------------------- config

GESTURE_FIELDS = {"label": "", "action": "none", "mode": "once", "text": "", "combo": "", "items": [],
                  "amount": 3, "app": "", "new_window": False, "url": "", "profile": ""}


def blank_key(fallback: bool) -> dict:
    """Tecla para a UI: {inherit, tap, long, double}; cada gesto com os campos de GESTURE_FIELDS."""
    return {"inherit": not fallback, "tap": dict(GESTURE_FIELDS, items=[]), "long": None, "double": None}


def ui_gesture(g: dict) -> dict:
    """Gesto canônico (gm.canonical) -> campos da UI. No cycle, a lista vai para `items`."""
    out = dict(GESTURE_FIELDS, items=[])
    out.update(label=g["label"], action=g["action"], mode=g["mode"])
    for field in gm.PAYLOAD.get(g["action"], ()):
        if field not in g:
            continue
        v = g[field]
        if isinstance(v, list):
            out["items"] = [str(x) for x in v]
        elif field in ("amount",):
            out[field] = int(v) if str(v).lstrip("-").isdigit() else v
        elif field == "new_window":
            out[field] = bool(v)
        else:
            out[field] = str(v)
    return out


def chord_order(name: str) -> tuple[int, int]:
    """'F13+F14' -> (0, 1): botão 1+2 antes de 1+3 antes de 2+3."""
    a, b = gm.parse_chord(name)
    return gm.CHORD_KEYS.index(a), gm.CHORD_KEYS.index(b)


def read_chords(raw: dict) -> dict:
    """[profiles.chords] -> {"F13+F14": {inherit, shared, tap, long, double}} (mesma forma de uma tecla na UI;
    shared = no Global, vale também nos outros perfis).
    Nome inválido é ignorado (o daemon já recusa a config inteira por ele)."""
    out = {}
    for name, spec in (raw or {}).items():
        try:
            key = gm.chord_name(*gm.parse_chord(name))
        except ValueError:
            continue
        if isinstance(spec, dict):
            out[key] = {"inherit": False, "shared": spec.get("shared") is True,
                        "tap": ui_gesture(gm.canonical(spec)), "long": None, "double": None}
    return dict(sorted(out.items(), key=lambda kv: chord_order(kv[0])))


def load_raw(text: str) -> dict:
    """Texto do config.json -> dicionário. ValueError (JSONDecodeError é um) se não for um objeto JSON."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("o config.json precisa ser um objeto JSON ({ ... })")
    return raw


def read_config() -> dict:
    raw = load_raw(CONFIG.read_text(encoding="utf-8"))
    raw_profiles = raw.get("profiles")
    if raw_profiles is None:  # formato antigo: só [keys]
        raw_profiles = [{"name": "Global", "fallback": True, "keys": raw.get("keys", {})}]
    if not any(p.get("fallback") for p in raw_profiles) and raw_profiles:
        raw_profiles[-1]["fallback"] = True
    profiles = []
    for p in raw_profiles:
        fb = bool(p.get("fallback"))
        keys = {}
        for k in KEYS:
            spec = p.get("keys", {}).get(k)
            item = blank_key(fb)
            gestures = gm.canonical_key(spec) if isinstance(spec, dict) else None
            if gestures is not None:  # o formato antigo vira o canônico aqui
                item = {"inherit": False, **{g: ui_gesture(a) if a else None for g, a in gestures.items()}}
            keys[k] = item
        profiles.append({
            "name": p.get("name", ""), "fallback": fb,
            "match": [{f: r.get(f, "") for f in RULE_FIELDS} for r in p.get("match", [])],
            "keys": keys, "chords": read_chords(p.get("chords")),
        })
    # o fallback fica sempre por último (ele não tem regras, a ordem não importa para ele)
    profiles.sort(key=lambda p: p["fallback"])
    return {"slash_key": raw.get("layout", {}).get("slash_key", "KEY_RO"), "timing": raw.get("timing", {}),
            "profiles": profiles}


def dump_gesture(g: dict, where: str, top: bool) -> dict:
    """Campos de um gesto (canônico) no config.json. O modo padrão da ação não é escrito."""
    action = g.get("action") or "none"
    if action not in gm.ACTIONS:
        raise ValueError(f"{where}: ação desconhecida {action!r}")
    mode = g.get("mode") or gm.ACTIONS[action][0]
    if mode not in gm.ACTIONS[action]:
        raise ValueError(f"{where}: o modo {mode!r} não vale para a ação {action!r}")
    label = str(g.get("label") or "")
    out = {"label": label} if top or label else {}
    out["action"] = action
    if mode == "cycle":
        field = gm.PAYLOAD[action][0]
        items = [str(x).strip() if action == "combo" else str(x) for x in g.get("items") or []]
        out[field] = [x for x in items if x.strip()]
    elif action == "text":
        out["text"] = str(g.get("text", ""))
    elif action == "combo":
        out["combo"] = str(g.get("combo", ""))
    elif action == "scroll":
        out["amount"] = int(g.get("amount") or 0)
    elif action == "launch":
        out["app"] = str(g.get("app", ""))
        if g.get("new_window"):
            out["new_window"] = True
    elif action == "url":
        out["url"] = str(g.get("url", "")).strip()
    elif action == "profile":
        out["profile"] = str(g.get("profile", "")).strip()
    if mode != gm.ACTIONS[action][0]:
        out["mode"] = mode
    return out


def dump_action(key: dict, k: str) -> dict | None:
    """Uma tecla: os campos do topo = toque; long/double = objetos dentro dela. None = herda do Global."""
    if key.get("inherit"):
        return None
    out = dump_gesture(key.get("tap") or GESTURE_FIELDS, k, top=True)
    for g in ("long", "double"):
        sub = key.get(g)
        if sub and (sub.get("action") or "none") != "none":
            out[g] = dump_gesture(sub, f"{k} ({gm.GESTURE_LABELS[g]})", top=False)
    return out


def dump_chords(p: dict, global_chords: set[str]) -> dict:
    """"chords" de um perfil. "Nada" só é escrito fora do Global e quando desliga uma combinação
    compartilhada do Global (nos outros casos é o mesmo que não ter a combinação). `global_chords` =
    as compartilhadas (shared), que valem nos outros perfis; shared só é escrito no Global."""
    out = {}
    chords = p.get("chords") or {}
    for name in sorted(chords, key=chord_order):
        ch = chords[name]
        if not ch or ch.get("inherit"):
            continue
        a, b = gm.parse_chord(name)
        g = ch.get("tap") or GESTURE_FIELDS
        where = f"combinação {a}+{b}"
        if (g.get("action") or "none") == "none":
            if p.get("fallback") or f"{a}+{b}" not in global_chords:
                continue
            fields = {"action": "none"}
        else:
            mode = g.get("mode") or gm.ACTIONS.get(g.get("action"), ("once",))[0]
            if mode not in gm.CHORD_MODES:
                raise ValueError(f"{where}: o modo {mode!r} ({gm.MODE_LABELS.get(mode, mode)}) não vale numa combinação")
            fields = dump_gesture(g, where, top=True)
            if p.get("fallback") and ch.get("shared"):
                fields["shared"] = True
        out[f"{a}+{b}"] = fields
    return out


def config_data(cfg: dict) -> dict:
    """Config da UI -> dicionário do config.json (sempre na mesma ordem, para o diff do git ficar limpo)."""
    profiles = cfg.get("profiles", [])
    if sum(1 for p in profiles if p.get("fallback")) != 1:
        raise ValueError("precisa existir exatamente um perfil padrão (Global)")
    names = [p.get("name", "").strip() for p in profiles]
    if any(not n for n in names):
        raise ValueError("todo perfil precisa de um nome")
    if len(set(n.lower() for n in names)) != len(names):
        raise ValueError("há perfis com o mesmo nome")
    data = {"layout": {"slash_key": str(cfg.get("slash_key", "KEY_RO"))}}
    timing = {k: int(v) for k, v in (cfg.get("timing") or {}).items() if k in TIMING_KEYS}
    if timing:
        data["timing"] = timing
    glob = next(p for p in profiles if p.get("fallback"))
    global_chords = {gm.chord_name(*gm.parse_chord(n)) for n, ch in (glob.get("chords") or {}).items()
                     if ch and not ch.get("inherit") and ch.get("shared")
                     and ((ch.get("tap") or {}).get("action") or "none") != "none"}
    out = []
    for p in sorted(profiles, key=lambda p: bool(p.get("fallback"))):
        entry = {"name": p["name"].strip()}
        if p.get("fallback"):
            entry["fallback"] = True
        rules = []
        if not p.get("fallback"):
            for r in p.get("match", []):
                rule = {f: str(r[f]).strip() for f in RULE_FIELDS if str(r.get(f, "")).strip()}
                if rule:
                    rules.append(rule)
        entry["match"] = rules
        keys = {}
        for k in KEYS:
            key = p.get("keys", {}).get(k) or blank_key(bool(p.get("fallback")))
            if p.get("fallback"):
                key = {**key, "inherit": False}  # o Global não herda de ninguém
            item = dump_action(key, k)
            if item:
                keys[k] = item
        entry["keys"] = keys
        chords = dump_chords(p, global_chords)
        if chords:
            entry["chords"] = chords
        out.append(entry)
    data["profiles"] = out
    return data


def dump_config(cfg: dict) -> str:
    return json.dumps(config_data(cfg), ensure_ascii=False, indent=2) + "\n"


def validate(text: str):
    raw = load_raw(text)
    if mc:
        mc.parse_config(raw)
        return
    for p in raw.get("profiles", []):  # sem o evdev: valida ao menos as combinações
        for k, spec in p.get("keys", {}).items():
            gestures = gm.canonical_key(spec)
            if gestures is not None:
                try:
                    gm.check_key(k, gestures)
                except ValueError as err:
                    raise ValueError(f"[{p.get('name')}] {k}: {err}") from None
        for name, spec in (p.get("chords") or {}).items():
            try:
                gm.parse_chord(name)
                gm.check_chord(gm.canonical(spec))
            except ValueError as err:
                raise ValueError(f"[{p.get('name')}] combinação {name}: {err}") from None


def save_config(cfg: dict) -> dict:
    text = dump_config(cfg)
    validate(text)
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, CONFIG)
    reply = daemon_request({"cmd": "reload"}, want="reload")
    if reply is None:
        return {"saved": True, "reloaded": False,
                "message": "Salvo, mas o daemon não está rodando — a mudança vale quando ele iniciar."}
    if not reply.get("ok"):
        return {"saved": True, "reloaded": False, "message": f"Salvo, mas o daemon recusou: {reply.get('error')}"}
    return {"saved": True, "reloaded": True, "message": "Salvo e aplicado."}


# ---------------------------------------------------------------- aplicativos

def list_apps() -> list[dict]:
    """Aplicativos visíveis no menu (arquivos .desktop), com id e nome em pt-BR se houver."""
    dirs = mc.desktop_dirs() if mc else [Path("/usr/share/applications"),
                                         Path.home() / ".local/share/applications"]
    apps: dict[str, dict] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.rglob("*.desktop"):
            app_id = str(f.relative_to(d)).replace("/", "-")
            if app_id in apps:  # o primeiro diretório (o do usuário) tem prioridade
                continue
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            cp.optionxform = str
            try:
                cp.read(f, encoding="utf-8")
                entry = cp["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError, OSError):
                continue
            if entry.get("Type") != "Application" or entry.get("NoDisplay") == "true" or entry.get("Hidden") == "true":
                apps[app_id] = None  # marca para não pegar uma cópia de outro diretório
                continue
            name = entry.get("Name[pt_BR]") or entry.get("Name[pt]") or entry.get("Name") or app_id
            apps[app_id] = {"id": app_id, "name": name}
    return sorted((a for a in apps.values() if a), key=lambda a: a["name"].lower())


# ---------------------------------------------------------------- daemon

def daemon_request(cmd: dict | None, want: str, timeout: float = 3.0) -> dict | None:
    """Manda um comando e espera a resposta do tipo `want`. None se o daemon não responde."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(SOCKET_PATH))
            if cmd:
                s.sendall((json.dumps(cmd) + "\n").encode())
            buf, deadline = b"", time.monotonic() + timeout
            while time.monotonic() < deadline:
                chunk = s.recv(65536)
                if not chunk:
                    return None
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    msg = json.loads(line)
                    if msg.get("type") == want:
                        return msg
    except (OSError, json.JSONDecodeError):
        return None
    return None


def run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=15)


def open_browser(url: str) -> None:
    """Abre a URL sem deixar zumbi: webbrowser.open() dispara o xdg-open e nunca dá wait()."""
    try:
        proc = subprocess.Popen(["xdg-open", url], start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        webbrowser.open(url)
        return
    threading.Thread(target=proc.wait, daemon=True).start()


# ---------------------------------------------------------------- saúde

def sysfs_read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def macropad_usb() -> bool:
    for dev in Path("/sys/bus/usb/devices").glob("*"):
        if sysfs_read(dev / "idVendor") == VENDOR and sysfs_read(dev / "idProduct") == PRODUCT:
            return True
    return False


def macropad_event_nodes() -> list[str]:
    nodes = []
    for ev in sorted(Path("/sys/class/input").glob("event*")):
        ids = ev / "device" / "id"
        if sysfs_read(ids / "vendor") == VENDOR and sysfs_read(ids / "product") == PRODUCT:
            nodes.append(f"/dev/input/{ev.name}")
    return nodes


def load_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def config_gestures(cfg: dict) -> list[dict]:
    """Todos os gestos configurados (todos os perfis, botões, gestos e combinações)."""
    return [g for p in cfg["profiles"] for key in [*p["keys"].values(), *p.get("chords", {}).values()]
            if not key.get("inherit") for g in (key["tap"], key["long"], key["double"]) if g]


def health() -> list[dict]:
    out = []

    def add(name, status, detail, hint=""):
        out.append({"name": name, "status": status, "detail": detail, "hint": hint})

    usb = macropad_usb()
    add("Macropad no USB", "ok" if usb else "fail",
        "Detectado (1189:8890)" if usb else "Não encontrado",
        "" if usb else "Confira o cabo. Para gravar a configuração ele precisa estar no cabo, não no Bluetooth.")

    nodes = macropad_event_nodes()
    readable = [n for n in nodes if os.access(n, os.R_OK)]
    if not nodes:
        add("Permissão de leitura", "fail" if usb else "warn", "Nenhuma entrada de teclado do macropad",
            "Replugue o macropad.")
    elif len(readable) == len(nodes):
        add("Permissão de leitura", "ok", ", ".join(nodes))
    else:
        add("Permissão de leitura", "fail", f"Sem acesso a {', '.join(set(nodes) - set(readable))}",
            "Rode ./install.sh (regra udev) e replugue o macropad.")

    uinput = os.access("/dev/uinput", os.W_OK)
    add("Teclado virtual (uinput)", "ok" if uinput else "fail",
        "/dev/uinput gravável" if uinput else "Sem permissão em /dev/uinput",
        "" if uinput else "Rode ./install.sh (regra udev) e faça logout/login.")

    add("python3-evdev", "ok" if mc else "fail", "Instalado" if mc else (MC_ERROR or "Ausente"),
        "" if mc else "sudo apt install python3-evdev")

    try:
        active = run("systemctl", "--user", "is-active", SERVICE).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        active = "desconhecido"
    add("Serviço", "ok" if active == "active" else "fail", f"{SERVICE}: {active}",
        "" if active == "active" else "Clique em Iniciar abaixo ou veja: journalctl --user -u macropad-claude")

    st = daemon_request(None, want="status", timeout=1.5)
    if st is None:
        add("Daemon", "fail", "Não respondeu no socket", "O serviço precisa estar rodando.")
    elif st.get("connected"):
        extra = " (modo teste ativo)" if st.get("test_mode") else ""
        add("Daemon", "ok", f"Capturando {', '.join(st['devices'])}{extra}")
    else:
        add("Daemon", "warn", "Rodando, mas sem o macropad", "Plugue o macropad; o daemon reconecta sozinho.")

    # extensão do GNOME (perfis automáticos)
    try:
        enabled = EXT_UUID in run("gnome-extensions", "list", "--enabled").stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        enabled = False
    if not (EXT_DIR / "extension.js").is_file():
        add("Extensão do GNOME", "fail", "Não instalada — os perfis não trocam sozinhos (fica no Global)",
            "Rode ./install.sh e depois saia da sessão e entre de novo.")
    elif st and st.get("extension"):
        win = st.get("window") or {}
        add("Extensão do GNOME", "ok", f"Conectada · perfil {st.get('profile')} · janela {win.get('app') or '—'}")
    elif not enabled:
        add("Extensão do GNOME", "fail", "Instalada, mas desativada (ou o GNOME ainda não a carregou)",
            f"Saia da sessão e entre de novo; depois: gnome-extensions enable {EXT_UUID}")
    else:
        add("Extensão do GNOME", "warn", "Ativada, mas não conectou ao daemon",
            "Ela reconecta a cada 3 s; confira se o serviço está rodando. Logs: journalctl --user -b | grep -i macropad")

    hold_combos = set()
    try:
        cfg = read_config()
        validate(CONFIG.read_text(encoding="utf-8"))
        add("config.json", "ok", f"{len(cfg['profiles'])} perfis · {CONFIG}")
        hold_combos = {g["combo"].lower() for g in config_gestures(cfg)
                       if g["action"] == "combo" and g["mode"] in ("toggle", "latch")}
    except (OSError, ValueError) as err:
        add("config.json", "fail", str(err), "Corrija na aba Botões e salve.")

    for combo in sorted(hold_combos):
        kb = load_json(CLAUDE_DIR / "keybindings.json") or {}
        bound = {key.lower() for block in kb.get("bindings", []) if block.get("context") == "Chat"
                 for key, act in block.get("bindings", {}).items() if act == "voice:pushToTalk"}
        ok = combo in bound
        add("Atalho de voz no Claude Code" + (f" ({combo})" if len(hold_combos) > 1 else ""), "ok" if ok else "fail",
            f"{combo} → voice:pushToTalk" if ok else f"{combo} não está ligado a voice:pushToTalk",
            "" if ok else f'Em ~/.claude/keybindings.json, contexto Chat: "{combo}": "voice:pushToTalk"')
    # effort pelo knob: shift+up/down são os atalhos padrão do Claude Code (chat:increaseEffort/decreaseEffort)
    effort_combos = set()
    try:
        effort_combos = {c.lower() for g in config_gestures(read_config()) if g["action"] == "combo"
                         for c in [g["combo"], *g["items"]] if c.lower() in ("shift+up", "shift+down")}
    except (OSError, ValueError):
        pass
    if effort_combos:
        kb = load_json(CLAUDE_DIR / "keybindings.json") or {}
        expected = {"shift+up": "chat:increaseEffort", "shift+down": "chat:decreaseEffort"}
        clash = [f"{k} → {act}" for block in kb.get("bindings", []) if block.get("context") == "Chat"
                 for k, act in block.get("bindings", {}).items()
                 if k.lower() in effort_combos and act != expected[k.lower()]]
        if clash:
            add("Effort pelo knob", "fail", "Atalho redefinido em ~/.claude/keybindings.json: " + ", ".join(clash),
                "Remova essas linhas para voltar ao padrão (shift+↑/↓ = effort).")
        else:
            add("Effort pelo knob", "ok", "shift+↑ / shift+↓ = aumentar / diminuir effort (padrão do Claude Code)")
    if hold_combos:
        settings = load_json(CLAUDE_DIR / "settings.json") or {}
        voice = settings.get("voice") or {}
        if not voice.get("enabled"):
            add("Voz do Claude Code", "fail", "Voz desativada", "No Claude Code: /voice hold")
        elif voice.get("mode", "hold") != "hold":
            add("Voz do Claude Code", "warn", f"Modo {voice.get('mode')!r}: o 2º toque vai enviar o prompt",
                "No Claude Code: /voice hold (o daemon faz o toque liga/desliga)")
        else:
            add("Voz do Claude Code", "ok", "Ativada, modo hold")
    return out


# ---------------------------------------------------------------- histórico da saúde (SQLite)

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "macropad-claude"
HISTORY_DB = STATE_DIR / "health.db"
KEEP_DAYS = 7
PAGE = 240  # rodadas por página (2 h com o intervalo padrão de 30 s)


class History:
    """Cada rodada de verificações vira uma linha em `runs` e uma por verificação em `results`."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS runs_ts ON runs (ts);
            CREATE TABLE IF NOT EXISTS results (
                run_id INTEGER NOT NULL, pos INTEGER NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, detail TEXT NOT NULL, hint TEXT NOT NULL,
                PRIMARY KEY (run_id, pos));
        """)

    def record(self, checks: list[dict]) -> None:
        now = time.time()
        old = now - KEEP_DAYS * 86400
        with self.lock, self.db:
            run_id = self.db.execute("INSERT INTO runs (ts) VALUES (?)", (now,)).lastrowid
            self.db.executemany("INSERT INTO results VALUES (?, ?, ?, ?, ?, ?)",
                                [(run_id, i, c["name"], c["status"], c["detail"], c["hint"])
                                 for i, c in enumerate(checks)])
            self.db.execute("DELETE FROM results WHERE run_id IN (SELECT id FROM runs WHERE ts < ?)", (old,))
            self.db.execute("DELETE FROM runs WHERE ts < ?", (old,))

    def page(self, after: int | None = None, before: int | None = None) -> dict:
        """As PAGE rodadas mais novas (depois de `after` / antes de `before`), da mais velha para a mais nova.

        more=True: com `after`, ficou um buraco (a página recomeça); senão, há rodadas mais antigas.
        """
        where, args = "", ()
        if after is not None:
            where, args = "WHERE id > ?", (after,)
        elif before is not None:
            where, args = "WHERE id < ?", (before,)
        with self.lock:
            rows = self.db.execute(f"SELECT id, ts FROM runs {where} ORDER BY id DESC LIMIT ?",
                                   (*args, PAGE + 1)).fetchall()
            more = len(rows) > PAGE
            rows = rows[:PAGE][::-1]
            runs = {rid: {"id": rid, "ts": ts, "checks": []} for rid, ts in rows}
            if rows:
                for rid, name, status, detail, hint in self.db.execute(
                        "SELECT run_id, name, status, detail, hint FROM results"
                        " WHERE run_id BETWEEN ? AND ? ORDER BY run_id, pos", (rows[0][0], rows[-1][0])):
                    if rid in runs:
                        runs[rid]["checks"].append([name, status, detail, hint])
        return {"runs": list(runs.values()), "more": more}


# ---------------------------------------------------------------- estatísticas de uso (SQLite do daemon)

USAGE_DB = STATE_DIR / "usage.db"  # escrito pelo daemon; aqui só lemos


def usage_stats(days: int) -> dict:
    """Somas do período (últimos `days` dias, contando hoje; 0 = tudo).

    macros: [perfil, botão, nome, ação, total]; daily: [dia, perfil, total]. O daemon grava a cada ~10 s.
    O gesto entra no nome quando não é o toque (ex.: "Fn (segurar)").
    """
    out = {"days": days, "macros": [], "daily": [], "first": None, "today": time.strftime("%Y-%m-%d")}
    if not USAGE_DB.exists():
        return out
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400)) if days > 0 else ""
    db = sqlite3.connect(f"file:{USAGE_DB}?mode=ro", uri=True, timeout=2)
    try:
        out["first"] = db.execute("SELECT MIN(day) FROM usage").fetchone()[0]
        cols = {r[1] for r in db.execute("PRAGMA table_info(usage)")}
        name = ("label || CASE gesture WHEN 'long' THEN ' (segurar)' WHEN 'double' THEN ' (toque duplo)' ELSE '' END"
                if "gesture" in cols else "label")  # tabela antiga: o daemon migra ao iniciar
        out["macros"] = db.execute(
            f"SELECT profile, key, {name} AS name, action, SUM(count) AS n FROM usage WHERE day >= ?"
            " GROUP BY profile, key, name, action ORDER BY n DESC", (since,)).fetchall()
        out["daily"] = db.execute(
            "SELECT day, profile, SUM(count) FROM usage WHERE day >= ? GROUP BY day, profile ORDER BY day",
            (since,)).fetchall()
    finally:
        db.close()
    return out


HISTORY: History | None = None
HISTORY_ERROR = ""
INTERVAL = 30


def check_and_record() -> list[dict]:
    checks = health()
    if HISTORY:
        try:
            HISTORY.record(checks)
        except sqlite3.Error as err:
            print(f"histórico: não consegui gravar: {err}", file=sys.stderr, flush=True)
    return checks


def monitor() -> None:
    """Thread de fundo: uma rodada de verificações a cada INTERVAL segundos."""
    while True:
        try:
            check_and_record()
        except Exception as err:  # noqa: BLE001 — o monitor não pode morrer
            print(f"verificação de saúde falhou: {err}", file=sys.stderr, flush=True)
        time.sleep(INTERVAL)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "macropad-claude-ui"

    def log_message(self, fmt, *args):  # silencioso
        pass

    def _allowed(self) -> bool:
        """Bloqueia DNS rebinding e POSTs vindos de outros sites."""
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in hosts}

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._allowed():
            return self.send_error(403)
        url = urlparse(self.path)
        if url.path == "/":
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/config":
            try:
                self._json({**read_config(), "meta": gm.meta()})
            except (OSError, ValueError) as err:
                self._json({"error": str(err)}, 500)
        elif url.path == "/api/apps":
            self._json({"apps": list_apps()})
        elif url.path == "/api/health":  # roda agora (botão "Verificar de novo") e grava no histórico
            self._json({"checks": check_and_record()})
        elif url.path == "/api/health/history":
            qs = parse_qs(url.query)
            num = lambda k: int(qs[k][0]) if qs.get(k, [""])[0].isdigit() else None  # noqa: E731
            if not HISTORY:
                return self._json({"runs": [], "more": False, "interval": INTERVAL, "error": HISTORY_ERROR})
            try:
                page = HISTORY.page(after=num("after"), before=num("before"))
            except sqlite3.Error as err:
                return self._json({"runs": [], "more": False, "interval": INTERVAL, "error": str(err)})
            self._json({**page, "interval": INTERVAL})
        elif url.path == "/api/usage":
            d = parse_qs(url.query).get("days", ["30"])[0]
            try:
                self._json(usage_stats(int(d) if d.isdigit() else 30))
            except sqlite3.Error as err:
                self._json({"error": f"não consegui ler {USAGE_DB}: {err}"}, 500)
        elif url.path == "/api/events":
            self._events(test=parse_qs(url.query).get("test") == ["1"])
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._allowed() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.send_error(403)
        url = urlparse(self.path)
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._json({"error": "JSON inválido"}, 400)
        if url.path == "/api/config":
            try:
                self._json(save_config(body))
            except (ValueError, KeyError, TypeError) as err:
                self._json({"saved": False, "message": str(err)}, 400)
            except OSError as err:
                self._json({"saved": False, "message": f"Não consegui gravar: {err}"}, 500)
        elif url.path == "/api/lock":
            reply = daemon_request({"cmd": "lock", "on": bool(body.get("on")), "profile": body.get("profile")},
                                   want="mode")
            self._json({"ok": reply is not None, "locked": bool(reply and reply.get("locked"))})
        elif url.path == "/api/capture":  # bloqueia até a extensão devolver o atalho (ela desiste em 10 s)
            reply = daemon_request({"cmd": "capture"}, want="captured", timeout=15)
            self._json(reply or {"ok": False, "error": "daemon"})
        elif url.path == "/api/service":
            action = body.get("action")
            if action not in ("start", "stop", "restart"):
                return self._json({"error": "ação inválida"}, 400)
            r = run("systemctl", "--user", action, SERVICE)
            self._json({"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip()})
        else:
            self.send_error(404)

    def _sse(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n")
        self.wfile.flush()

    def _events(self, test: bool):
        """Server-Sent Events: repassa as mensagens do daemon. Fechar a aba encerra o modo teste."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.settimeout(10)
                    s.connect(str(SOCKET_PATH))
                except OSError:
                    s.close()
                    self._sse({"type": "daemon_down"})
                    time.sleep(2)
                    continue
                with s:
                    if test:
                        s.sendall(b'{"cmd": "test_mode", "on": true}\n')
                    buf = b""
                    while True:
                        try:
                            chunk = s.recv(65536)
                        except socket.timeout:
                            self.wfile.write(b": ping\n\n")  # detecta aba fechada
                            self.wfile.flush()
                            continue
                        except OSError:
                            chunk = b""
                        if not chunk:
                            self._sse({"type": "daemon_down"})
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            self.wfile.write(b"data: " + line + b"\n\n")
                        self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--interval", type=int, default=30, help="segundos entre as verificações de saúde (padrão 30)")
    args = ap.parse_args()
    global HISTORY, HISTORY_ERROR, INTERVAL
    INTERVAL = max(5, args.interval)
    try:
        HISTORY = History(HISTORY_DB)
    except (OSError, sqlite3.Error) as err:
        HISTORY_ERROR = f"histórico desligado: {err}"
        print(HISTORY_ERROR, file=sys.stderr, flush=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}"
    print(f"macropad-claude UI em {url}  (Ctrl+C para sair)", flush=True)
    threading.Thread(target=monitor, daemon=True).start()
    if not args.no_browser:
        open_browser(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
