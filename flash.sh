#!/usr/bin/env bash
# Grava macropad/claude-macropad.yaml no teclado com o ch57x-keyboard-tool (vendor/ch57x-keyboard-tool/).
# O macropad precisa estar ligado por cabo USB.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="v1.7.0"  # a versão que está em vendor/ch57x-keyboard-tool/ (ver o README de lá)
TOOL_DIR="$REPO/vendor/ch57x-keyboard-tool"
BIN="$TOOL_DIR/ch57x-keyboard-tool"
CONFIG="$REPO/macropad/claude-macropad.yaml"
RELEASES="https://github.com/kriomant/ch57x-keyboard-tool/releases/tag/$VERSION"

# O binário do repositório é para Linux x86_64. Em outra arquitetura, baixa o da mesma versão.
case "$(uname -m)" in
  x86_64) TARGET="x86_64-unknown-linux-gnu" ;;
  aarch64 | arm64) TARGET="aarch64-unknown-linux-gnu"; BIN="$TOOL_DIR/aarch64/ch57x-keyboard-tool" ;;
  *) echo "Arquitetura $(uname -m) sem binário pronto. Veja $RELEASES ou: cargo install ch57x-keyboard-tool" >&2; exit 1 ;;
esac

if [[ ! -x "$BIN" ]]; then
  echo "==> baixando ch57x-keyboard-tool $VERSION ($TARGET)"
  mkdir -p "$(dirname "$BIN")"
  curl -fsSL "https://github.com/kriomant/ch57x-keyboard-tool/releases/download/$VERSION/ch57x-keyboard-tool-$TARGET.tar.gz" \
    | tar xz -C "$(dirname "$BIN")" ch57x-keyboard-tool
fi

echo "==> validando"
"$BIN" validate "$CONFIG"

echo "==> gravando no macropad"
# Com a regra udev do install.sh não precisa de sudo; sem ela, cai para sudo.
"$BIN" upload "$CONFIG" || sudo "$BIN" upload "$CONFIG"
echo "Feito. Teste com: python3 daemon/macropad_claude.py --debug   (pare o serviço antes)"
