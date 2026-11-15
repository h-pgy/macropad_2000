#!/usr/bin/env bash
# Instala o daemon macropad-claude (udev + serviço systemd de usuário + atalho do Claude Code).
# Rode da raiz do repo: ./install.sh   (vai pedir sudo para a regra udev e o python3-evdev)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -eq 0 ]]; then
  echo "Não rode com sudo: o serviço é do seu usuário. Rode ./install.sh (ele pede sudo só onde precisa)." >&2
  exit 1
fi

echo "==> python3-evdev"
if ! python3 -c "import evdev" 2>/dev/null; then
  sudo apt install -y python3-evdev
fi

echo "==> regra udev (acesso ao macropad e ao uinput sem sudo)"
RULE=/etc/udev/rules.d/70-macropad-claude.rules
if cmp -s "$REPO/system/70-macropad-claude.rules" "$RULE"; then
  echo "    já instalada"
else
  sudo install -m 644 "$REPO/system/70-macropad-claude.rules" "$RULE"
  sudo udevadm control --reload
  sudo udevadm trigger
fi

echo "==> serviço systemd de usuário"
mkdir -p "$HOME/.config/systemd/user"
sed "s|@REPO@|$REPO|g" "$REPO/system/macropad-claude.service" \
  > "$HOME/.config/systemd/user/macropad-claude.service"
systemctl --user daemon-reload
systemctl --user enable --now macropad-claude.service
systemctl --user restart macropad-claude.service

echo "==> extensão do GNOME (perfis pela janela em foco)"
EXT_UUID="macropad-claude@local"
EXT_DST="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
EXT_NEW=0
[[ -d "$EXT_DST" ]] || EXT_NEW=1
if ! diff -rq "$REPO/gnome-extension/$EXT_UUID" "$EXT_DST" >/dev/null 2>&1; then
  mkdir -p "$EXT_DST"
  cp -r "$REPO/gnome-extension/$EXT_UUID/." "$EXT_DST/"
  EXT_CHANGED=1
else
  EXT_CHANGED=0
fi
if gnome-extensions enable "$EXT_UUID" 2>/dev/null; then
  echo "    ativada"
else
  echo "    copiada; o GNOME só enxerga extensões novas depois de sair da sessão e entrar de novo"
fi

echo "==> atalho da UI no menu de aplicativos"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/macropad-claude.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Macropad 2000
Comment=Configurar e testar o macropad do Claude Code
Exec=python3 "$REPO/ui/server.py"
Icon=input-keyboard
Terminal=false
Categories=Settings;HardwareSettings;
EOF

echo "==> atalho de voz no Claude Code (alt+k)"
KB="$HOME/.claude/keybindings.json"
if [[ ! -e "$KB" ]]; then
  install -D -m 644 "$REPO/claude/keybindings.json" "$KB"
  echo "    criado $KB"
elif grep -q '"voice:pushToTalk"' "$KB"; then
  echo "    $KB já tem voice:pushToTalk, não mexi"
else
  echo "    ATENÇÃO: $KB já existe. Adicione manualmente: \"alt+k\": \"voice:pushToTalk\" no contexto Chat"
fi

echo "==> emoji no título do terminal do Claude Code (hook UserPromptSubmit)"
python3 - "$HOME/.claude/settings.json" "$REPO/claude/title-hook.py" <<'PY'
import json, os, sys, tempfile
path, hook = sys.argv[1:]
cmd = f'python3 "{hook}"'
try:
    with open(path, encoding="utf-8") as f:
        settings = json.load(f)
except FileNotFoundError:
    settings = {}
except ValueError as e:
    print(f"    ATENÇÃO: {path} não é JSON válido ({e}), não mexi")
    sys.exit(0)
groups = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
before = json.dumps(groups)
# Tira versões antigas deste hook (ex.: repo clonado em outro caminho) e põe a atual.
for g in groups:
    g["hooks"] = [h for h in g.get("hooks", []) if "title-hook.py" not in h.get("command", "")]
groups[:] = [g for g in groups if g.get("hooks")]
groups.append({"hooks": [{"type": "command", "command": cmd}]})
if json.dumps(groups) == before:
    print("    já instalado")
    sys.exit(0)
os.makedirs(os.path.dirname(path), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings.")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")
if os.path.exists(path):
    os.chmod(tmp, os.stat(path).st_mode & 0o777)
os.replace(tmp, path)
print(f"    hook adicionado em {path}")
PY

if [[ $EXT_NEW -eq 1 || $EXT_CHANGED -eq 1 ]]; then
  cat <<EOF

*** A extensão do GNOME foi instalada/atualizada: saia da sessão e entre de novo.
    Depois, se a aba Saúde disser "desativada": gnome-extensions enable $EXT_UUID
EOF
fi

cat <<'EOF'

Pronto. Falta:
  1. No Claude Code: /voice hold   (modo segurar — o daemon cuida do toque liga/desliga)
  2. Gravar o macropad: ./flash.sh
  3. Abrir a UI: "Macropad 2000" no menu, ou  python3 ui/server.py
     (aba Saúde mostra se está tudo certo)
EOF
