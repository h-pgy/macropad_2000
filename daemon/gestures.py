"""Modelo dos botões do macropad: gesto × ação × modo. Sem dependências (usado pelo daemon e pela UI).

    Botão ─▶ Gesto (tap | long | double) ─▶ Ação (o quê) + Modo (como roda no tempo)
    Dois botões juntos ─▶ Combinação (chord) ─▶ Ação + Modo

Este módulo só conhece nomes: traduz o formato antigo do config para o canônico e valida
as combinações permitidas. O payload (teclas, textos, apps) é validado pelo daemon.

Limite do hardware: o knob. Girar o knob com um botão segurado faz o macropad mandar "soltou" do
botão no mesmo instante (e não avisar mais nada dele). Entre botões não há esse problema: o macropad
informa os botões apertados ao mesmo tempo, e cada soltura na hora certa (medido em 2026-11-14).
Por isso as combinações (chords) são só de dois dos 6 botões, sem o knob.
Ver SPEC-gestos-modos.md e o README ("Gestos, ações e modos" e "Combinações").
"""

# Modos (na UI: "Funcionamento")
#   once:       executa uma vez
#   toggle:     interruptor fraco: liga; desliga com o mesmo gesto ou com a próxima ação
#   latch:      trava (interruptor forte): liga; só o mesmo gesto do mesmo botão desliga
#   while_held: ligado enquanto o botão estiver apertado
#   cycle:      cada vez, o próximo item da lista
#   switcher:   estilo Alt+Tab (segura o 1º modificador entre toques seguidos)
MODES = ("once", "toggle", "latch", "while_held", "cycle", "switcher")

# Ação -> modos que ela aceita (o 1º é o padrão, que não é escrito no config.json).
# Para criar uma ação: o nome aqui, o payload em PAYLOAD e a execução no daemon (Runner).
ACTIONS = {
    "combo": ("once", "toggle", "latch", "while_held", "cycle", "switcher"),
    "text": ("once", "cycle"),
    "profile": ("latch", "toggle"),
    "scroll": ("once",),
    "launch": ("once",),
    "url": ("once",),
    "none": ("once",),
}
# Campos de cada ação no config.json. No modo cycle, o 1º campo vira uma lista.
PAYLOAD = {
    "combo": ("combo",), "text": ("text",), "profile": ("profile",), "scroll": ("amount",),
    "launch": ("app", "new_window"), "url": ("url",), "none": (),
}

GESTURES = ("tap", "long", "double")
GESTURE_MODES = {
    "tap": MODES,
    "long": tuple(m for m in MODES if m != "switcher"),
    "double": tuple(m for m in MODES if m != "switcher"),
}
# No toque, estes modos só valem se o botão não tiver "segurar" nem "toque duplo": com eles, o
# toque só é decidido no soltar, e a tecla seria apertada com o botão já solto.
TAP_NEEDS_ALONE = ("while_held", "switcher")
# Girar o knob: só o toque, sem interruptores.
TURN_KEYS = ("F19", "F21")
TURN_MODES = ("once", "cycle", "switcher")

# Combinação (chord): dois botões diferentes apertados juntos. Só os 6 botões (o knob, girado com
# um botão segurado, manda "soltou" falso do botão). Sem "enquanto apertado" e sem alternador.
CHORD_KEYS = ("F13", "F14", "F15", "F16", "F17", "F18")
CHORD_MODES = ("once", "toggle", "latch", "cycle")

LONG_MS = 350    # segurar: tempo apertado para virar "long"
DOUBLE_MS = 250  # toque duplo: janela entre soltar o 1º toque e apertar o 2º
CHORD_MS = 50    # combinação: tempo máximo entre os dois apertos (juntos chegam a ~8 ms)

MODE_LABELS = {"once": "uma vez", "toggle": "interruptor", "latch": "trava",
               "while_held": "enquanto apertado", "cycle": "alternar", "switcher": "alternador de janelas"}
GESTURE_LABELS = {"tap": "ao tocar", "long": "ao segurar", "double": "toque duplo"}
CHORD_LABEL = "combinação"

# Formato antigo -> novo
LEGACY_BEHAVIORS = {"tap": "once", "hold": "while_held", "toggle": "toggle"}
LEGACY_ACTIONS = {"hold_toggle": ("combo", "toggle"), "cycle": ("combo", "cycle"), "switcher": ("combo", "switcher")}


def canonical(spec: dict) -> dict:
    """Um gesto do config (em qualquer formato antigo) -> {"action", "mode", "label", payload...}."""
    action = str(spec.get("action") or "none").strip().lower()
    mode = spec.get("mode")
    if action in LEGACY_ACTIONS:
        action, legacy_mode = LEGACY_ACTIONS[action]
        mode = mode or legacy_mode
    if not mode and spec.get("behavior"):
        behavior = str(spec["behavior"]).strip().lower()
        mode = LEGACY_BEHAVIORS.get(behavior, behavior)
    mode = str(mode).strip().lower() if mode else ACTIONS.get(action, ("once",))[0]
    if action == "profile" and mode == "while_held":  # a camada saiu (limite do hardware): vira "por um toque"
        mode = "toggle"
    out = {"action": action, "mode": mode, "label": str(spec.get("label") or "")}
    for field in PAYLOAD.get(action, ()):
        if field in spec:
            out[field] = spec[field]
    if action == "combo" and "combo" not in spec and "combos" in spec:  # cycle antigo
        out["combo"] = list(spec["combos"])
    return out


def canonical_key(spec: dict) -> dict | None:
    """Tecla do config -> {"tap": g, "long": g|None, "double": g|None}. None = usa a do Global."""
    if str(spec.get("action") or "").strip().lower() == "inherit":
        return None
    out = {"tap": canonical({k: v for k, v in spec.items() if k not in ("long", "double")})}
    for gesture in ("long", "double"):
        sub = spec.get(gesture)
        g = canonical(sub) if isinstance(sub, dict) else None
        out[gesture] = g if g and g["action"] != "none" else None
    return out


def check_key(key: str, gestures: dict) -> None:
    """Valida ação × modo × gesto × controle de uma tecla canônica. ValueError legível."""
    for gesture in GESTURES:
        g = gestures.get(gesture)
        if g is None:
            continue
        where = "" if gesture == "tap" else f"{GESTURE_LABELS[gesture]}: "
        action, mode = g["action"], g["mode"]
        if action not in ACTIONS:
            raise ValueError(f"{where}ação desconhecida: {action!r}")
        if mode not in MODES:
            raise ValueError(f"{where}modo desconhecido: {mode!r} (use: {', '.join(MODES)})")
        if mode not in ACTIONS[action]:
            raise ValueError(f"{where}o modo {mode!r} ({MODE_LABELS[mode]}) não vale para a ação {action!r}"
                             f" (use: {', '.join(ACTIONS[action])})")
        if key in TURN_KEYS:
            if gesture != "tap":
                raise ValueError("girar o knob só aceita o toque (sem 'segurar' nem 'toque duplo')")
            if mode not in TURN_MODES:
                raise ValueError(f"girar o knob não aceita o modo {mode!r} ({MODE_LABELS[mode]});"
                                 f" use: {', '.join(TURN_MODES)}")
        if mode not in GESTURE_MODES[gesture]:
            raise ValueError(f"{where}o modo {mode!r} ({MODE_LABELS[mode]}) só vale no toque")
    tap = gestures.get("tap")
    if tap and tap["mode"] in TAP_NEEDS_ALONE and (gestures.get("long") or gestures.get("double")):
        raise ValueError(f"o modo {tap['mode']!r} ({MODE_LABELS[tap['mode']]}) no toque não combina com"
                         " 'segurar' nem 'toque duplo' no mesmo botão")


def parse_chord(name: str) -> tuple[str, str]:
    """'F14+F13' -> ('F13', 'F14'): dois botões diferentes, em ordem. ValueError legível."""
    parts = [p.strip().upper() for p in str(name).split("+")]
    if len(parts) != 2 or not all(parts):
        raise ValueError("uma combinação tem exatamente dois botões (ex.: \"F13+F14\")")
    for p in parts:
        if p not in CHORD_KEYS:
            raise ValueError(f"{p} não entra em combinação: só os botões 1 a 6 ({CHORD_KEYS[0]} a {CHORD_KEYS[-1]})")
    if parts[0] == parts[1]:
        raise ValueError("uma combinação precisa de dois botões diferentes")
    a, b = sorted(parts, key=CHORD_KEYS.index)
    return a, b


def chord_name(a: str, b: str) -> str:
    """Nome canônico no config.json: 'F13+F14' (o botão de número menor primeiro)."""
    return "+".join(parse_chord(f"{a}+{b}"))


def check_chord(g: dict) -> None:
    """Valida ação × modo de uma combinação (gesto canônico). ValueError legível."""
    action, mode = g["action"], g["mode"]
    if action not in ACTIONS:
        raise ValueError(f"ação desconhecida: {action!r}")
    if mode not in MODES:
        raise ValueError(f"modo desconhecido: {mode!r} (use: {', '.join(MODES)})")
    if mode not in ACTIONS[action]:
        raise ValueError(f"o modo {mode!r} ({MODE_LABELS[mode]}) não vale para a ação {action!r}"
                         f" (use: {', '.join(ACTIONS[action])})")
    if mode not in CHORD_MODES:
        raise ValueError(f"o modo {mode!r} ({MODE_LABELS[mode]}) não vale numa combinação"
                         f" (use: {', '.join(m for m in CHORD_MODES if m in ACTIONS[action])})")


def meta() -> dict:
    """As tabelas acima, para a UI filtrar as opções."""
    return {"modes": list(MODES), "actions": {k: list(v) for k, v in ACTIONS.items()},
            "gestures": list(GESTURES), "gesture_modes": {k: list(v) for k, v in GESTURE_MODES.items()},
            "tap_needs_alone": list(TAP_NEEDS_ALONE), "turn_keys": list(TURN_KEYS), "turn_modes": list(TURN_MODES),
            "chord_keys": list(CHORD_KEYS), "chord_modes": list(CHORD_MODES),
            "mode_labels": MODE_LABELS, "gesture_labels": GESTURE_LABELS, "chord_label": CHORD_LABEL,
            "long_ms": LONG_MS, "double_ms": DOUBLE_MS, "chord_ms": CHORD_MS}
