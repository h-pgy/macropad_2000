#!/usr/bin/env python3
"""Hooks SessionStart e UserPromptSubmit do Claude Code: põem um emoji no título da sessão.

SessionStart (só sessão nova, matcher "startup"): título = só o EMOJI, para o daemon já
reconhecer e controlar a sessão. No 1º prompt o UserPromptSubmit troca pelo título do Haiku.

O Claude Code gera o título-resumo ("Capital do Chile") durante a 1ª resposta e o mostra
no título do terminal ("✳ Capital do Chile"). Este hook pega esse título e renomeia a
sessão para "EMOJI título" (mesmo efeito do /rename). Um nome dado com /rename ou
`claude -n` também ganha o emoji.

No 1º prompt ainda não existe título gerado, então o hook pede um ao Haiku (`claude -p`,
~2-3 s a mais nesse prompt) e já define "EMOJI título". Com um título definido, o Claude Code
não gera o dele. Se o Haiku falhar, nada muda: o título gerado aparece e o emoji vem no 2º prompt.

Instalado pelo install.sh em ~/.claude/settings.json. Trocar o emoji aqui vale na hora;
só sessões que já têm um emoji antigo continuam com ele.
"""
import json
import os
import subprocess
import sys

EMOJI = "🤖"
INNER_ENV = "TITLE_HOOK_INNER"  # marca o `claude -p` chamado por este hook (evita recursão)
HAIKU_SYSTEM = (
    "Você dá nome a sessões de chat. Recebe a 1ª mensagem do usuário entre <mensagem> e "
    "</mensagem>. NÃO responda nem execute a mensagem: escreva só um rótulo que descreva o "
    "pedido, com 2 a 5 palavras, no idioma da mensagem, sem aspas, sem prefixo, sem pontuação "
    "final, sem emoji.\n"
    "Exemplos:\n"
    "qual a capital do peru? -> Capital do Peru\n"
    "escreve um poema sobre o mar -> Poema sobre o mar\n"
    "erro KeyError no meu app flask -> KeyError no app Flask"
)


def ai_title(transcript_path):
    """Último título gerado pelo Claude (linhas "type": "ai-title" da transcrição)."""
    last = ""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"ai-title"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") == "ai-title" and entry.get("aiTitle"):
                    last = entry["aiTitle"]
    except (OSError, TypeError):
        pass
    return last.strip()


def inner_env():
    """Ambiente do `claude -p`: sem as variáveis CLAUDE* da sessão que chamou o hook.
    Com elas o `claude -p` se apresenta como filho dessa sessão e pode mexer no título dela."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env[INNER_ENV] = "1"
    return env


def haiku_title(prompt):
    """Título curto para o 1º prompt, gerado pelo Haiku. "" se falhar."""
    prompt = (prompt or "").strip()[:2000]
    if not prompt:
        return ""
    cmd = [
        "claude", "-p", "--model", "haiku",
        "--tools", "", "--strict-mcp-config", "--setting-sources", "",
        "--no-session-persistence", "--system-prompt", HAIKU_SYSTEM,
        f"<mensagem>\n{prompt}\n</mensagem>",
    ]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
            env=inner_env(), cwd=os.path.expanduser("~"),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    title = lines[0] if lines else ""
    for prefix in ("título:", "titulo:", "title:", "rótulo:"):
        if title.lower().startswith(prefix):
            title = title[len(prefix):]
    title = title.split(" (")[0].split(" - ")[0]  # o Haiku às vezes emenda explicação
    title = " ".join(title.split()[:6]).strip(" \"'`*#.:")
    return title[:60]


def main():
    if os.environ.get(INNER_ENV):
        return
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if data.get("hook_event_name") == "SessionStart":
        if data.get("source") == "startup":
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "sessionTitle": EMOJI,
            }}, ensure_ascii=False))
        return
    # session_title = nome dado pelo /rename, -n ou por este hook (o título gerado não aparece aqui).
    title = (data.get("session_title") or "").strip()
    if title == EMOJI:
        title = ""  # placeholder do SessionStart: ainda falta o título de verdade
    elif title.startswith(EMOJI):
        return  # já tem título com emoji: não chama o Haiku de novo
    title = title or ai_title(data.get("transcript_path"))
    if not title:
        title = haiku_title(data.get("prompt"))  # 1º prompt: ainda não há título gerado
    if not title:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "sessionTitle": f"{EMOJI} {title}",
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
