# Macropad 2000

Usa um macropad genérico CH57x (6 botões + 1 knob à esquerda, USB `1189:8890`) como controle do [Claude Code](https://code.claude.com) e do desktop no Linux (GNOME). O que cada botão faz **muda conforme a janela em foco**.

## Perfis de fábrica

| Controle | Claude (terminal) | Firefox | Global (o resto) |
|---|---|---|---|
| Botão 1 | `/clear` (sem Enter) | nova aba | abre Terminal |
| Botão 2 | voz: toque grava, toque para (sem enviar) | nova janela | abre Firefox |
| Botão 3 | trocar de modelo (`Alt+P`) | reabrir aba fechada | abre VS Code |
| Botão 4 | trocar de modo (`Shift+Tab`) | aba anterior | abre Arquivos |
| Botão 5 | `/usage` | próxima aba | abre Spotify |
| Botão 6 | **aciona o Global** (fixa ↔ automático) | **aciona o Global** | copiar / colar (alterna) |
| Knob, girar | `←` / `→` | rola a página | troca de janela (Alt+Tab segurado) |
| Knob, apertar | `/effort` | topo da página | visão geral (`super`) |

"↳ Global" = o botão usa a ação do perfil Global. Tudo é editável pela UI.

## Perfil fixo × automático (ação "Acionar perfil", botão 6)

A ação **Acionar perfil** (`profile`), no funcionamento **Trava** (o padrão), trava o macropad num perfil, ignorando a janela em foco. Repetir **o mesmo gesto do mesmo botão** volta ao automático, mesmo que o perfil travado tenha outra ação nele (ex.: travado no Global, o botão 6 destrava em vez de copiar/colar). O perfil padrão da ação é o Global, mas pode ser qualquer perfil salvo. **Todo perfil novo já vem com o botão 6 = acionar o Global.** A mesma ação também tem o **perfil por um toque** (ver [Gestos, ações e modos](#gestos-ações-e-modos)).

| Modo | Faz | Badge |
|---|---|---|
| **Automático** | o perfil segue a janela em foco | tecladinho, azul |
| **Perfil fixo** | trava no perfil escolhido (ex.: **GLOBAL**) | tecladinho + cadeado, amarelo |

Ao trocar, aparece um aviso no centro da tela com a cor do modo. O modo atual fica sempre visível num badge colorido (**AUTO** / nome do perfil fixo) no canto superior esquerdo da barra do GNOME (clicar nele dá a opção de fixar no Global ou voltar) e no canto superior direito da UI (clicar alterna). O modo não é salvo: reiniciar o daemon volta ao automático. Renomear ou excluir um perfil na UI atualiza as teclas que apontavam para ele (excluir → passam a acionar o Global).

## Como funciona

```
macropad ──F13..F21──▶ daemon (python-evdev) ──teclas/rolagem──▶ teclado+mouse virtual (uinput) ──▶ janela em foco
                         ▲            ▲
   extensão do GNOME ────┘            └──── UI web local (ui/server.py + ui/index.html)
   (janela em foco, abrir apps)            (socket Unix: eventos, reload, modo teste)
```

- **O macropad** só manda F13–F21 (`macropad/claude-macropad.yaml`), gravados uma vez. Ele aceita no máximo 5 teclas por sequência, por isso a lógica fica no PC. Quem grava é o `ch57x-keyboard-tool` (de terceiros, licença MIT), que fica em `vendor/ch57x-keyboard-tool/`.
- **O daemon** (`daemon/macropad_claude.py`) captura o macropad, escolhe o perfil pela janela em foco e executa a ação. Configuração em `config.json` (na raiz, versionado no git). Pela UI, salvar já aplica.
- **A extensão do GNOME** (`gnome-extension/`, ~390 linhas de JavaScript, sem compilação) informa a janela em foco, abre ou foca aplicativos, abre sites, mostra o modo (badge na barra e aviso ao trocar) e faz a captura de atalho da UI (pegando o teclado, para que atalhos do sistema como Ctrl+Alt+T sejam capturados em vez de executados). Ela é necessária porque, no Wayland, programas comuns não conseguem saber qual janela está ativa. Sem ela, vale sempre o perfil Global.
- **A voz com toque liga/desliga:** o tap mode do Claude Code envia o prompt sozinho no 2º toque. Para revisar antes de enviar, o Claude Code fica em modo *hold* e o daemon "segura" `Alt+K` entre os toques.
- **Emoji no título do terminal:** um hook `UserPromptSubmit` do Claude Code (`claude/title-hook.py`, registrado pelo `install.sh` em `~/.claude/settings.json`) pega o título-resumo que o Claude gera e renomeia a sessão para `🤖 título`. O resultado no terminal é `✳ 🤖 Capital do Chile`. O hook só pode renomear quando você manda um prompt, então o emoji aparece a partir do **2º prompt** (o resumo fica pronto durante a 1ª resposta). Um nome dado com `/rename` também ganha o emoji. Para trocar o emoji, edite `EMOJI` no script: vale na hora, sem reinstalar.

### Gestos, ações e modos

Cada botão reconhece até três **gestos**, e cada gesto tem a sua **ação** (o quê) e o seu **funcionamento** (`mode`, como roda no tempo). As tabelas ficam em `daemon/gestures.py`; a UI só oferece as combinações válidas.

```
Botão ─▶ Gesto (tocar | segurar | toque duplo) ─▶ Ação + Funcionamento
```

| Gesto | Na UI | Quando |
|---|---|---|
| `tap` | Ao tocar | apertar e soltar. Se o botão só tem o toque, dispara **no aperto**, sem atraso. Com segurar ou toque duplo, dispara ao soltar (ou 250 ms depois). |
| `long` | Ao segurar | ficar 350 ms apertado. |
| `double` | Toque duplo | 2º aperto até 250 ms depois de soltar o 1º; dispara no 2º aperto. |

| Ação | Faz | Funcionamentos (o 1º é o padrão) |
|---|---|---|
| `combo` | aperta um atalho (`alt+p`, `super`, `playpause`, `shift`…) | uma vez, interruptor, trava, enquanto apertado, alternar, alternador |
| `text` | digita um texto (`/clear`; `\n` = Enter) | uma vez, alternar |
| `profile` | aciona um perfil | trava, interruptor (por um toque) |
| `launch` | abre um aplicativo; se já estiver aberto, foca (ou sempre nova janela) | uma vez |
| `url` | abre um site no navegador padrão, de qualquer janela | uma vez |
| `scroll` | rola a roda do mouse | uma vez |
| `none` / herdar | nada / usa o botão do Global (com todos os gestos) | — |

| Funcionamento | `mode` | Faz |
|---|---|---|
| Uma vez | `once` | executa uma vez |
| Interruptor | `toggle` | **fraco**: liga; desliga com o mesmo gesto **ou com a próxima ação** do macropad. Atalho: segura as teclas e solta antes da outra ação (é o da voz, `alt+k`). Perfil: **perfil por um toque**, a próxima ação roda nele e ele volta sozinho. |
| Trava | `latch` | **forte**: liga; só o mesmo gesto do mesmo botão desliga. Atalho: modificador travado, como o Caps Lock (`shift`). Perfil: perfil fixo. |
| Enquanto apertado | `while_held` | ligado enquanto o botão estiver apertado. O GNOME repete, como num teclado (`backspace`). |
| Alternar | `cycle` | cada vez, o próximo item da lista (`ctrl+c` → `ctrl+v`); pede pelo menos 2 itens |
| Alternador de janelas | `switcher` | estilo Alt+Tab: segura o 1º modificador entre toques seguidos e solta após uma pausa; outro botão só confirma |

Restrições:
- **Knob e botão não combinam.** Com um botão segurado, girar o knob faz o macropad mandar "soltou" desse botão (e não avisar mais nada dele): o botão segurado vale como um toque. Entre botões isso não acontece: veja [Combinações](#combinações-dois-botões-juntos).
- "Enquanto apertado" e "alternador" no toque só valem se o botão não tiver segurar nem toque duplo (o toque seria decidido com o botão já solto). O alternador só vale no toque.
- Girar o knob: só o toque, com uma vez, alternar ou alternador.
- Só um interruptor fica ligado por vez. Travas podem coexistir; de perfil, uma só (travar outro substitui).
- Reload, desconexão e modo teste soltam tudo (a trava de perfil continua). No modo teste, o gesto é reconhecido e aparece na UI, mas nada é executado.

No `config.json`, cada perfil tem `name`, `match` (regras), `keys` e, opcionalmente, `chords`; o Global tem `"fallback": true`. Tecla ausente num perfil = usa a do Global. Os campos do topo da tecla são o toque; `long` e `double` são objetos dentro dela. O modo padrão da ação não é escrito. Botão só com `long`/`double`: `"action": "none"` no topo:

```json
"keys": {
  "F13": { "label": "voz", "action": "combo", "combo": "alt+k", "mode": "toggle" },
  "F15": { "label": "backspace", "action": "combo", "combo": "backspace", "mode": "while_held" },
  "F18": { "label": "copiar/colar", "action": "combo", "combo": ["ctrl+c", "ctrl+v"], "mode": "cycle" },
  "F14": {
    "label": "enter", "action": "combo", "combo": "enter",
    "long": { "label": "Global", "action": "profile", "profile": "Global", "mode": "toggle" },
    "double": { "label": "clear", "action": "text", "text": "/clear\n" }
  }
}
```

A UI grava o arquivo sempre com a mesma indentação e a mesma ordem de campos, para o diff do git mostrar só o que mudou. Editou à mão? Rode `systemctl --user restart macropad-claude` (ou salve qualquer coisa pela UI).

Tempos opcionais: `"timing": { "long_ms": 350, "double_ms": 250, "chord_ms": 50 }` (o último é das combinações). Tecla do `/`: `"layout": { "slash_key": "KEY_RO" }`. O formato antigo continua valendo e a UI grava no novo ao salvar: `"behavior": "hold"`/`"toggle"` → `"mode": "while_held"`/`"toggle"`; `"action": "hold_toggle"`, `"cycle"` (`"combos": [...]`) e `"switcher"` viram `combo` com o modo correspondente; perfil com `"mode": "while_held"` (a antiga camada) vira `toggle` (por um toque).

### Combinações (dois botões juntos)

Apertar **dois botões juntos** (só os botões 1 a 6, sem o knob) roda uma ação própria, no lugar da ação de cada um. Ex.: no Firefox, 1 = nova aba, 2 = nova janela e **1 + 2 = janela privada**.

- **Juntos** quer dizer o 2º botão apertado até **50 ms** depois do 1º (`timing.chord_ms`). A combinação dispara no 2º aperto; soltar os botões não faz nada.
- Um botão que faz parte de alguma combinação no perfil ativo espera até 50 ms antes de disparar o toque, para ver se o outro vem. Os outros botões continuam instantâneos, e o "segurar" conta a partir do aperto, sem atraso extra.
- As ações e os funcionamentos são os do toque, menos "enquanto apertado" e "alternador". Interruptor e trava desligam com a mesma combinação.
- Valem por perfil. Diferente dos botões, uma combinação do Global só vale nos outros perfis se for **compartilhada** (`"shared": true`, a caixa **Herdar nos outros perfis** na UI); sem isso, ela só vale no Global. Um perfil pode ter a sua no mesmo par ou desligar a compartilhada com `"action": "none"` (**Nada** na UI).
- Só pares (1 + 2), não trios.

Medido neste macropad: dois botões apertados "juntos" chegam com 8 ms de diferença, e dois toques separados bem rápidos chegam com 16 a 72 ms. Os mais rápidos (abaixo de 50 ms) contam como combinação; se isso atrapalhar, diminua o `chord_ms`.

```json
"chords": {
  "F13+F14": { "label": "janela privada", "action": "combo", "combo": "ctrl+shift+p" },
  "F14+F15": { "label": "terminal", "action": "combo", "combo": "ctrl+alt+t", "shared": true }
}
```

(`F13+F14` = botões 1 + 2. `shared` só vale no Global: a combinação passa a valer também nos outros perfis.)

### Regras dos perfis

O primeiro perfil (em ordem de prioridade) com uma regra que bate com a janela em foco vence. Se nenhum bater, vale o Global. Uma regra combina `app` (id `.desktop`, exato), `wm_class` (exato), `title` (contém) e/ou `title_regex` (expressão regular buscada no título, sem diferenciar maiúsculas). Todos os campos preenchidos precisam bater.

O perfil Claude usa `"title_regex": "^[◐◑✳🤖]"`: o Claude Code começa o título da janela com ◐/◑ (trabalhando) ou ✳ (parado), ou só com o 🤖 do title-hook (sessão já renomeada); então só os terminais com o Claude ativam o perfil. Um shell comum (`usuario@host: ~`) cai no Global.

## UI

```bash
python3 ui/server.py      # abre http://127.0.0.1:8765 (ou "Macropad 2000" no menu de aplicativos)
```

- **Botões:** escolha o perfil, clique num controle do desenho e defina a ação e o funcionamento de cada gesto (abas **Ao tocar**, **+ Ao segurar**, **+ Toque duplo**). Combinações que não valem aparecem desabilitadas, com o motivo. Há lista de aplicativos com filtro, captura de atalho e sugestões prontas. Arraste um botão sobre outro para trocar os dois de lugar, com todos os gestos e as combinações deles (um botão herdado do Global vira ação própria do perfil); **Desfazer** na barra de salvar volta a troca. Em **Combinações**, embaixo do desenho, **+ Nova** escolhe os dois botões, e a combinação usa o mesmo editor. No Global, a caixa **Herdar nos outros perfis** decide se ela vale nos outros perfis; neles, as herdadas aparecem numa linha à parte (**Do Global**), separadas das do perfil. A aba **Perfis** mostra as combinações do perfil embaixo do mini teclado.
- **Perfis:** crie, renomeie, reordene e exclua perfis e edite as regras. **Janela em foco agora** e **Janelas recentes** mostram app, wm_class e título de cada janela, com botões para copiar o título, criar uma regra ou criar um perfil a partir dela. A própria UI é ignorada nessa lista: clique na janela que quer configurar e volte para a UI.
- **Modo teste:** os botões físicos acendem e aparecem no log com o gesto reconhecido (tocar, segurar, toque duplo, combinação), mas nada é executado. Desliga sozinho ao fechar a aba.
- **Saúde:** USB, permissões, evdev, serviço, daemon, extensão, config, atalho e modo de voz do Claude Code, cada um com a dica de correção. Cada verificação tem um gráfico com o histórico: um ponto a cada 30 s (verde em cima, amarelo no meio, vermelho embaixo), o mais novo à direita. Role para a esquerda para ver o passado. Passe o mouse num ponto para ver hora e detalhe. O histórico só é gravado enquanto o `ui/server.py` está rodando: uma linha tracejada marca o período em que ele ficou fechado. Os dados ficam em SQLite (`~/.local/state/macropad-claude/health.db`, últimos 7 dias). Para mudar o intervalo: `--interval 60`.
- **Uso:** quantas vezes cada macro rodou, com o nome do macro (e o gesto, quando não é o toque: "Global (segurar)"): perfis mais usados, macros mais usados (todos os perfis), macros por dia e um gráfico por perfil com os botões mais usados. Períodos: hoje, 7, 30, 90 dias ou tudo. Quem conta é o daemon (então conta mesmo com a UI fechada): só os totais por dia, perfil e botão, guardados para sempre em `~/.local/state/macropad-claude/usage.db` (gravados a cada 10 s). Não conta o modo teste, nem botões sem ação; a voz (segurar/soltar) e o acionar perfil (fixar/voltar) contam uma vez por uso. Renomear um macro começa uma contagem nova com o nome novo.

Só biblioteca padrão. Escuta apenas em `127.0.0.1` e recusa requisições vindas de outros sites.

> **Por que não Docker?** O projeto precisa de `/dev/input`, `/dev/uinput`, regras udev, do `systemctl --user`, do GNOME Shell e de `~/.claude/`. Tudo isso fica do lado de fora de um container. Para levar para outro PC: `git clone` + `./install.sh`.

## Instalação

Requisitos: Linux com GNOME 45+ (Wayland ou X11), Python ≥ 3.11 e Claude Code logado com conta claude.ai (exigido pela voz).

```bash
./install.sh     # python3-evdev, regra udev, serviço, extensão do GNOME, atalho alt+k e emoji no título do Claude Code
./flash.sh       # grava a configuração no macropad (usa o vendor/ch57x-keyboard-tool/)
```

- Não rode com `sudo`. O script pede a senha só onde precisa. A senha não pode ser digitada pelo `!` do Claude Code: rode em um terminal normal.
- Depois da primeira instalação (ou de mudar a extensão), **saia da sessão e entre de novo**. Se a aba Saúde disser "desativada", rode `gnome-extensions enable macropad-claude@local`.
- No Claude Code, rode `/voice hold`.

### Layout do teclado

Os textos são digitados como teclas físicas, então a `/` depende do layout. O padrão é ABNT2 (`KEY_RO`). Para layout US, troque na UI.

## Diagnóstico

```bash
journalctl --user -u macropad-claude -f          # log do serviço (perfil ativo, conexões)
journalctl --user -b | grep -i macropad          # erros da extensão aparecem no log do GNOME Shell
systemctl --user stop macropad-claude
python3 daemon/macropad_claude.py --debug        # roda à mão, mostrando cada tecla e gesto
python3 -m unittest discover -s tests            # testes (teclado falso: nada vai para a tela)
```

## Limitações

- A tecla vai para a janela em foco.
- Comandos com `/` só funcionam com a caixa de texto do Claude Code vazia.
- Reconhecer o Claude por título depende do título que o Claude Code põe no terminal. O perfil de fábrica usa só o app (Terminal); refine com a lista de janelas recentes.
- `ctrl+c` no terminal interrompe o Claude Code. Para copiar e colar lá, use `ctrl+shift+c/v`.
- A voz não funciona via SSH. Sem microfone local também não.
