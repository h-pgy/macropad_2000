# Macropad 2000

Usa um macropad genérico CH57x (6 botões + 1 knob) como controle do [Claude Code](https://code.claude.com) e do desktop. O que cada botão faz **muda conforme a janela em foco** (perfis), e tudo se configura por uma UI web local.

> **Funciona só no Ubuntu e só com este macropad.** Veja [Compatibilidade](#compatibilidade) antes de instalar.

## Compatibilidade

O projeto foi feito e testado em um único ambiente. Fora dele não há suporte:

| | Testado | Fora disso |
|---|---|---|
| **Sistema** | Ubuntu 25.04 (Plucky Puffin), GNOME Shell 48, sessão Wayland, Python 3.13 do sistema | Outras distribuições não funcionam: o `install.sh` usa `apt`, e a troca de perfil depende de uma extensão do GNOME Shell. Outras versões do Ubuntu e a sessão X11 não foram testadas. |
| **Macropad** | CH57x genérico de **6 botões + 1 knob**, USB `1189:8890` | Outros modelos CH57x (3 ou 12 botões, 2 ou 3 knobs, `1189:8840`, `1189:8842`) não funcionam: o daemon só procura `1189:8890`, e a UI e o `config.json` pressupõem 6 botões + 1 knob. |
| **Claude Code** | 2.1, logado com conta claude.ai (exigido pela voz) | A voz, o atalho `alt+k` e o emoji no título dependem de recursos do Claude Code que podem mudar. |

> **Por que não Docker?** O projeto precisa de `/dev/input`, `/dev/uinput`, regras udev, do `systemctl --user`, do GNOME Shell e de `~/.claude/`. Tudo isso fica do lado de fora de um container.

## Instalação

```bash
git clone <este repositório> && cd macropad_2000
./install.sh     # python3-evdev, regra udev, serviço, extensão do GNOME, atalho na UI, alt+k e emoji no título do Claude Code
./flash.sh       # grava o macropad (uma vez só; usa o vendor/ch57x-keyboard-tool/)
```

- Não rode com `sudo`: o script pede a senha só onde precisa. A senha não pode ser digitada pelo `!` do Claude Code, então rode num terminal normal.
- Depois da primeira instalação (ou de mudar a extensão), **saia da sessão e entre de novo**. Se a aba Saúde disser "desativada", rode `gnome-extensions enable macropad-claude@local`.
- No Claude Code, rode `/voice hold` (o daemon cuida do toque liga/desliga).
- Abra a UI: **Macropad 2000** no menu de aplicativos, ou `python3 ui/server.py`. A aba **Saúde** mostra se está tudo certo.
- O `flash.sh` precisa do macropad no cabo USB. Mudar as funções pela UI **não** exige gravar de novo.

### O que fica fora do repositório

O `install.sh` grava o caminho do repositório em alguns desses arquivos. **Mudou o repositório de pasta? Rode `./install.sh` de novo no lugar novo**: ele reescreve o serviço, o atalho e os hooks. Se a UI antiga ainda estiver aberta, feche-a (ela segura a porta 8765).

| Onde | O quê | Tem o caminho do repo? |
|---|---|---|
| `/etc/udev/rules.d/70-macropad-claude.rules` | acesso ao macropad e ao `/dev/uinput` sem `sudo` | não |
| `~/.config/systemd/user/macropad-claude.service` | o daemon, como serviço do usuário | sim |
| `~/.local/share/gnome-shell/extensions/macropad-claude@local/` | cópia da extensão do GNOME | não |
| `~/.local/share/applications/macropad-claude.desktop` | "Macropad 2000" no menu de aplicativos | sim |
| `~/.claude/keybindings.json` | `alt+k` → `voice:pushToTalk` (só criado se o arquivo não existir) | não |
| `~/.claude/settings.json` | hooks `SessionStart` e `UserPromptSubmit` do `claude/title-hook.py` | sim |
| `~/.local/state/macropad-claude/` | `health.db` (histórico da Saúde) e `usage.db` (contagem de uso) | não |
| `$XDG_RUNTIME_DIR/macropad-claude.sock` | socket do daemon (some quando ele para) | não |

Para desinstalar:

```bash
systemctl --user disable --now macropad-claude
rm ~/.config/systemd/user/macropad-claude.service && systemctl --user daemon-reload
gnome-extensions disable macropad-claude@local
rm -r ~/.local/share/gnome-shell/extensions/macropad-claude@local
rm ~/.local/share/applications/macropad-claude.desktop
sudo rm /etc/udev/rules.d/70-macropad-claude.rules && sudo udevadm control --reload
rm -r ~/.local/state/macropad-claude        # opcional: apaga o histórico e as estatísticas
```

Depois, tire as duas entradas do `title-hook.py` de `~/.claude/settings.json` e, se quiser, o `alt+k` de `~/.claude/keybindings.json`.

## Como funciona

```
macropad ──F13..F21──▶ daemon (python-evdev) ──teclas/rolagem──▶ teclado+mouse virtual (uinput) ──▶ janela em foco
                         ▲            ▲
   extensão do GNOME ────┘            └──── UI web local (ui/server.py + ui/index.html)
   (janela em foco, abrir apps)            (socket Unix: eventos, reload, modo teste)
```

- **O macropad** só manda as teclas F13 a F21, gravadas uma vez pelo `flash.sh` (`macropad/claude-macropad.yaml`). Ele aceita no máximo 5 teclas por sequência, por isso toda a lógica fica no PC. Quem grava é o `ch57x-keyboard-tool` (de terceiros, licença MIT), em `vendor/ch57x-keyboard-tool/`.

  | Controle | Tecla | Controle | Tecla |
  |---|---|---|---|
  | Botão 1 | `F13` | Botão 4 | `F16` |
  | Botão 2 | `F14` | Botão 5 | `F17` |
  | Botão 3 | `F15` | Botão 6 | `F18` |
  | Knob, girar para a esquerda | `F19` | Knob, girar para a direita | `F21` |
  | Knob, apertar | `F20` | | |

  Os botões 1 a 3 ficam na fileira de cima e o knob à esquerda (na UI, **Girar teclado** inverte o desenho se você usa o macropad virado).
- **O daemon** (`daemon/macropad_claude.py`) captura o macropad (as teclas F13+ não chegam aos aplicativos), escolhe o perfil pela janela em foco, reconhece o gesto e executa a ação. Reconecta sozinho se o macropad for desplugado. A configuração fica em `config.json`, na raiz e versionado no git.
- **A extensão do GNOME** (`gnome-extension/`, JavaScript sem compilação) informa a janela em foco, abre ou foca aplicativos, abre sites, mostra o modo (badge na barra e aviso ao trocar) e faz a captura de atalho da UI. Ela é necessária porque, no Wayland, programas comuns não conseguem saber qual janela está ativa. Sem ela, vale sempre o perfil Global.
- **A UI** (`ui/server.py` + `ui/index.html`, só biblioteca padrão) edita o `config.json` e mostra o que o daemon está fazendo. Veja [UI](#ui).
- **A voz com toque liga/desliga:** no modo *tap*, o Claude Code envia o prompt sozinho no 2º toque. Para revisar antes de enviar, o Claude Code fica em modo *hold* e o daemon "segura" `alt+k` entre os toques.
- **Emoji no título do terminal** (`claude/title-hook.py`, registrado pelo `install.sh` em `~/.claude/settings.json`):
  - Ao abrir uma sessão nova, o título vira só `🤖` (hook `SessionStart`).
  - No 1º prompt, o hook pede um título curto ao Haiku (`claude -p --model haiku`, com a sua conta, uns 2 a 3 s a mais nesse prompt) e renomeia a sessão para `🤖 título`. O terminal mostra `✳ 🤖 Capital do Chile`. Se o Haiku falhar, vale o título que o próprio Claude Code gera, e o emoji aparece a partir do 2º prompt.
  - Um nome dado com `/rename` ou `claude -n` também ganha o emoji.
  - Para trocar o emoji, edite `EMOJI` no script: vale na hora, sem reinstalar.

## Perfis

O `config.json` do repositório traz os perfis que eu uso (**Claude**, **Gemini**, **Firefox** e **Global**). Use-os como ponto de partida e mude tudo pela UI.

- **Global** é o perfil padrão: vale quando nenhum outro bate, não tem regras e não pode ser excluído. Um botão que um perfil deixa em **Usar do Global** pega a ação do Global, com todos os gestos.
- Os outros perfis têm **regras**. O primeiro perfil, em ordem de prioridade, com uma regra que bate com a janela em foco vence. Uma regra combina `app` (id do `.desktop`, exato), `wm_class` (exato), `title` (o título contém) e/ou `title_regex` (expressão regular buscada no título, sem diferenciar maiúsculas). Todos os campos preenchidos precisam bater.
- Exemplo: o perfil Claude usa `"app": "org.gnome.Terminal.desktop"` com `"title_regex": "^[◐◑✳🤖]"`. O Claude Code começa o título da janela com ◐/◑ (trabalhando) ou ✳ (parado), ou só com o 🤖 do title-hook. Assim, só os terminais com o Claude ativam o perfil, e um shell comum (`usuario@host: ~`) cai no Global.

### Perfil fixo × automático

A ação **Acionar perfil** (`profile`), no funcionamento **Trava** (o padrão), trava o macropad num perfil, ignorando a janela em foco. Repetir **o mesmo gesto do mesmo botão** volta ao automático, mesmo que o perfil travado tenha outra ação nesse botão. Todo perfil novo já vem com o **botão 6 = acionar o Global**.

| Modo | Faz | Badge |
|---|---|---|
| **Automático** | o perfil segue a janela em foco | tecladinho, azul, **AUTO** |
| **Perfil fixo** | trava no perfil escolhido | tecladinho + cadeado, amarelo, nome do perfil |

- O badge fica no canto superior esquerdo da barra do GNOME (clicar abre um menu para fixar no Global ou voltar ao automático) e no topo da UI (clicar alterna entre o automático e o Global fixo). Ao trocar, aparece um aviso na tela com a cor do modo.
- O modo não é salvo: reiniciar o daemon volta ao automático.
- No funcionamento **Interruptor**, a mesma ação vira **perfil por um toque**: a próxima ação do macropad roda naquele perfil e ele volta sozinho.
- Renomear ou excluir um perfil na UI atualiza as ações que apontavam para ele (excluir faz elas passarem a acionar o Global).

## Gestos, ações e modos

Cada botão reconhece até três **gestos**, e cada gesto tem a sua **ação** (o quê) e o seu **funcionamento** (`mode`, como roda no tempo). As tabelas ficam em `daemon/gestures.py`, e a UI só oferece as combinações válidas.

```
Botão ─▶ Gesto (tocar | segurar | toque duplo) ─▶ Ação + Funcionamento
```

| Gesto | Na UI | Quando |
|---|---|---|
| `tap` | Ao tocar | apertar e soltar. Se o botão só tem o toque, dispara **no aperto**, sem atraso. Com segurar ou toque duplo, dispara ao soltar (ou 250 ms depois). |
| `long` | Ao segurar | ficar 350 ms apertado. |
| `double` | Toque duplo | 2º aperto até 250 ms depois de soltar o 1º; dispara no 2º aperto. |

| Ação | Na UI | Faz | Funcionamentos (o 1º é o padrão) |
|---|---|---|---|
| `text` | Digitar texto | digita um texto (`/clear`; `\n` = Enter). Só letras sem acento, números, espaço, `/` e `-`. | uma vez, alternar |
| `combo` | Atalho de teclado | aperta um atalho: `alt+p`, `ctrl+shift+t`, `super`, `shift` sozinho, teclas de mídia (`playpause`, `nextsong`, `previoussong`, `volumeup`, `volumedown`, `mute`) | uma vez, interruptor, trava, enquanto apertado, alternar, alternador |
| `launch` | Abrir aplicativo | abre um aplicativo; se já estiver aberto, foca (ou sempre abre nova janela) | uma vez |
| `url` | Abrir site | abre um site (http/https) no navegador padrão, de qualquer janela | uma vez |
| `profile` | Acionar perfil | trava num perfil ou ativa por um toque | trava, interruptor |
| `scroll` | Rolar a página | gira a roda do mouse (1 a 20 cliques, para cima ou para baixo) | uma vez |
| `none` | Nada | nada | — |
| (sem a tecla) | Usar do Global | usa o botão do Global, com todos os gestos | — |

| Funcionamento | `mode` | Faz |
|---|---|---|
| Uma vez | `once` | executa uma vez |
| Interruptor | `toggle` | **fraco**: liga; desliga com o mesmo gesto **ou com a próxima ação** do macropad. Atalho: segura as teclas e solta antes da outra ação (é o da voz, `alt+k`). Perfil: **perfil por um toque**. |
| Trava | `latch` | **forte**: liga; só o mesmo gesto do mesmo botão desliga. Atalho: modificador travado, como o Caps Lock (`shift`). Perfil: perfil fixo. |
| Enquanto apertado | `while_held` | ligado enquanto o botão estiver apertado. O GNOME repete, como num teclado (`backspace`). |
| Alternar | `cycle` | cada vez, o próximo item da lista (`ctrl+c` → `ctrl+v`); pede pelo menos 2 itens |
| Alternador de janelas | `switcher` | estilo Alt+Tab: segura o 1º modificador entre toques seguidos e solta após uma pausa; outro botão só confirma |

Restrições:
- **Knob e botão não combinam.** Com um botão segurado, girar o knob faz o macropad mandar "soltou" desse botão (e não avisar mais nada dele): o botão segurado vale como um toque. Entre botões isso não acontece: veja [Combinações](#combinações-dois-botões-juntos).
- "Enquanto apertado" e "alternador" no toque só valem se o botão não tiver segurar nem toque duplo (o toque seria decidido com o botão já solto). O alternador só vale no toque.
- Girar o knob: só o toque, com uma vez, alternar ou alternador.
- Só um interruptor fica ligado por vez. Travas podem coexistir; de perfil, só uma (travar outro substitui).
- Reload, desconexão e modo teste soltam tudo (a trava de perfil continua). No modo teste, o gesto é reconhecido e aparece na UI, mas nada é executado.

### Combinações (dois botões juntos)

Apertar **dois botões juntos** (só os botões 1 a 6, sem o knob) roda uma ação própria, no lugar da ação de cada um. Ex.: no perfil Claude, 1 + 2 digita `/model` e Enter.

- **Juntos** quer dizer o 2º botão apertado até **50 ms** depois do 1º (`timing.chord_ms`). A combinação dispara no 2º aperto; soltar os botões não faz nada.
- Um botão que faz parte de alguma combinação no perfil ativo espera até 50 ms antes de disparar o toque, para ver se o outro vem. Os outros botões continuam instantâneos, e o "segurar" conta a partir do aperto, sem atraso extra.
- As ações e os funcionamentos são os do toque, menos "enquanto apertado" e "alternador". Interruptor e trava desligam com a mesma combinação.
- Valem por perfil. Diferente dos botões, uma combinação do Global só vale nos outros perfis se for **compartilhada** (`"shared": true`, a caixa **Herdar nos outros perfis** na UI). Um perfil pode ter a sua no mesmo par ou desligar a compartilhada com **Nada** (`"action": "none"`).
- Só pares (1 + 2), não trios.

Medido neste macropad: dois botões apertados "juntos" chegam com 8 ms de diferença, e dois toques separados bem rápidos chegam com 16 a 72 ms. Os mais rápidos (abaixo de 50 ms) contam como combinação; se isso atrapalhar, diminua o `chord_ms`.

## UI

```bash
python3 ui/server.py                                      # abre http://127.0.0.1:8765 no navegador
python3 ui/server.py --no-browser --port 9000 --interval 60
```

Na barra do topo: o estado do daemon, do macropad e da extensão, o perfil ativo, o badge do modo (AUTO ou o perfil fixo; clicar alterna), a chave **Modo teste** e o tema claro/escuro (segue o sistema até você escolher). As mudanças ficam pendentes até **Salvar e aplicar**, que grava o `config.json` e recarrega o daemon na hora, ou **Descartar**. Fechar a aba com mudanças pendentes pede confirmação. `#health` ou `#usage` no fim do endereço abrem direto na aba.

### Botões

- Uma aba por perfil (o ponto marca o ativo agora). O desenho mostra o nome de cada controle; em itálico, o que vem do Global. Passe o mouse num controle para ver tudo o que ele faz.
- **Editar:** clique num controle e escolha nome, ação e funcionamento. Os funcionamentos que não valem ali aparecem desabilitados, com o motivo. **+ Ao segurar** e **+ Toque duplo** adicionam gestos ao botão; **Remover este gesto** tira.
- **Ajudas:** sugestões prontas de textos (`/clear`, `/usage`, `/compact`…), de atalhos (um carrossel), de listas e de sites. **Capturar** pega o próximo atalho que você apertar, inclusive os do sistema, como Ctrl+Alt+T (pela extensão do GNOME; sem ela, a captura é pelo navegador e não pega esses). A lista de aplicativos tem filtro. Ao escolher um app, um perfil ou um site, o nome do botão é preenchido sozinho, enquanto você não escrever outro.
- **Trocar de lugar:** arraste um botão sobre outro para trocar os dois, com todos os gestos e as combinações deles (um botão herdado do Global vira ação própria do perfil). **Desfazer**, na barra de salvar, volta a troca.
- **Combinações:** segure Shift e clique em dois botões (ou use o botão **Combinação** ou a faixa **Nova combinação**). Elas aparecem embaixo do desenho e usam o mesmo editor. No Global, cada uma tem a caixa **Herdar nos outros perfis**; nos outros perfis, as herdadas aparecem à parte, na linha **Do Global**.
- **Girar teclado** gira o desenho 180°, para o macropad usado com o knob à direita (fica lembrado neste navegador).
- **Último evento** mostra o que acabou de chegar do macropad; **ver todos →** leva à lista na aba Uso.
- **Layout do teclado**, no fim do editor: ABNT2 (o padrão) ou US. Os textos são digitados como teclas físicas, então a `/` depende do layout.

### Perfis

- **Prioridade:** a lista dos perfis, na ordem em que as regras são testadas. Arraste ⋮⋮ ou use ↑ ↓ (ou as setas do teclado). **+ Novo** cria um perfil logo antes do Global, já com o botão 6 acionando o Global.
- **Editor do perfil:** nome; regras (**Quando ativar**), com a coluna **bate agora?** dizendo se a regra pega a janela em foco ou alguma recente; um mini teclado com os controles e as combinações do perfil (clique para editar na aba Botões); **Excluir perfil**.
- **Janelas:** a janela em foco e as 12 mais recentes, com app, wm_class, título e o perfil que cada uma ativaria com as regras atuais (mesmo antes de salvar). Clique numa janela para **+ regra: app**, **+ app + título**, **Copiar título** ou **Novo perfil daqui**. A UI ignora a própria janela: clique na janela que quer configurar e volte para a UI.

### Saúde

- **Serviço:** o estado do systemd, do daemon e da extensão, com **Iniciar**, **Reiniciar** e **Parar**.
- **Verificações:** macropad no USB, permissão de leitura, teclado virtual (uinput), python3-evdev, serviço, daemon, extensão do GNOME e `config.json`. Conforme o que o config usa, também o atalho de voz, o modo de voz e o effort pelo knob do Claude Code. Cada verificação traz a dica de correção.
- **Histórico:** cada verificação tem um gráfico, com um ponto a cada 30 s (verde em cima, amarelo no meio, vermelho embaixo) e o mais novo à direita. Role para a esquerda para ver o passado e passe o mouse num ponto para ver hora e detalhe. O histórico só é gravado enquanto o `ui/server.py` está rodando; uma linha tracejada marca o período em que ele ficou fechado. Os dados ficam em `~/.local/state/macropad-claude/health.db` (últimos 7 dias). Para mudar o intervalo: `--interval 60`.

### Uso

- **Eventos ao vivo:** os últimos 40 eventos desde que a página abriu.
- **Estatísticas** por período (hoje, 7, 30, 90 dias ou tudo), atualizadas a cada 10 s: total de macros executados, perfil e macro mais usados, média por dia, perfis mais usados, os 10 macros mais usados (com a parte de cada perfil), macros por dia (por semana em períodos longos) e os botões mais usados em cada perfil.
- Quem conta é o daemon, então conta mesmo com a UI fechada. Ele guarda só os totais por dia, perfil, botão e gesto, para sempre, em `~/.local/state/macropad-claude/usage.db` (gravado a cada 10 s). Não conta o modo teste nem botões sem ação; a voz (ligar/desligar) e o acionar perfil (fixar/voltar) contam uma vez por uso. Renomear um macro começa uma contagem nova com o nome novo.

### Modo teste

A chave **Modo teste** abre uma janela com os eventos. Os botões físicos acendem no desenho e ficam selecionados no editor (no perfil ativo), com o gesto reconhecido, mas nada é executado. Fechar essa janela, ou a aba, desliga o modo teste.

A UI escuta só em `127.0.0.1` e recusa requisições vindas de outros sites.

## config.json

A UI grava o arquivo sempre com a mesma indentação e a mesma ordem de campos, para o diff do git mostrar só o que mudou. Editou à mão? Rode `systemctl --user restart macropad-claude` (ou salve qualquer coisa pela UI). Se o config estiver inválido, o daemon recusa o reload e continua com o anterior; ao iniciar, ele nem sobe. O erro aparece no log e na aba Saúde.

- Cada perfil tem `name`, `match` (lista de regras), `keys` e, opcionalmente, `chords`. O Global tem `"fallback": true` e `match` vazio.
- Tecla ausente num perfil = usa a do Global. Os campos do topo da tecla são o toque; `long` e `double` são objetos dentro dela. O funcionamento padrão da ação não é escrito. Botão só com `long`/`double`: `"action": "none"` no topo.

Um perfil (trecho do perfil Claude):

```json
{
  "name": "Claude",
  "match": [{ "app": "org.gnome.Terminal.desktop", "title_regex": "^[◐◑✳🤖]" }],
  "keys": {
    "F13": { "label": "voz", "action": "combo", "combo": "alt+k", "mode": "toggle",
             "double": { "label": "limpar voz", "action": "combo", "combo": "ctrl+u" } },
    "F15": { "label": "backspace", "action": "combo", "combo": "backspace", "mode": "while_held" },
    "F16": { "label": "clear", "action": "text", "text": "/clear" },
    "F18": { "label": "Global", "action": "profile", "profile": "Global" }
  },
  "chords": {
    "F13+F14": { "label": "model", "action": "text", "text": "/model\n" }
  }
}
```

`F13+F14` = botões 1 + 2. Outros campos por ação:
- launch: `"app": "org.gnome.Nautilus.desktop"` e, opcional, `"new_window": true`;
- url: `"url": "web.whatsapp.com"` (sem `https://`, ele é colocado sozinho);
- scroll: `"amount": -3` (negativo = para cima);
- alternar (`cycle`): uma lista no lugar do texto ou do atalho, como `"combo": ["ctrl+c", "ctrl+v"]`;
- alternador (`switcher`): o atalho começa com o modificador que fica segurado, como `"combo": "alt+tab"`;
- combinação compartilhada (só no Global): `"shared": true`.

Opcionais no topo do arquivo:
- `"timing": { "long_ms": 350, "double_ms": 250, "chord_ms": 50 }`, com `long_ms` entre 100 e 3000, `double_ms` entre 50 e 1000 e `chord_ms` entre 10 e 300.
- `"layout": { "slash_key": "KEY_RO" }` (ABNT2, o padrão) ou `"KEY_SLASH"` (US).

O formato antigo continua valendo, e a UI grava no novo ao salvar: `"behavior": "hold"`/`"toggle"` viram `"mode": "while_held"`/`"toggle"`; as ações `hold_toggle`, `cycle` (com `"combos"`) e `switcher` viram `combo` com o funcionamento correspondente; perfil com `"mode": "while_held"` (a antiga camada) vira `toggle` (por um toque).

## Diagnóstico

```bash
journalctl --user -u macropad-claude -f          # log do serviço (perfil ativo, conexões, erros do config)
journalctl --user -b | grep -i macropad          # erros da extensão aparecem no log do GNOME Shell
systemctl --user stop macropad-claude
python3 daemon/macropad_claude.py --debug        # roda à mão, mostrando cada tecla e gesto
python3 -m unittest discover -s tests            # testes (teclado falso: nada vai para a tela)
```

## Limitações

- A tecla vai para a janela em foco.
- Comandos com `/` só funcionam com a caixa de texto do Claude Code vazia.
- Textos só podem ter letras sem acento, números, espaço, `/` e `-`.
- Reconhecer o Claude pelo título depende do título que o Claude Code põe no terminal. Se ele mudar, ajuste a regra pela lista de janelas na aba Perfis.
- `ctrl+c` no terminal interrompe o Claude Code. Para copiar e colar lá, use `ctrl+shift+c/v`.
- A voz não funciona via SSH, nem sem microfone local.

## Arquivos

| Caminho | O quê |
|---|---|
| `config.json` | perfis, botões e combinações (editado pela UI) |
| `daemon/macropad_claude.py` | o daemon |
| `daemon/gestures.py` | tabelas de gestos × ações × funcionamentos (usadas pelo daemon e pela UI) |
| `ui/server.py`, `ui/index.html` | a UI web local |
| `gnome-extension/macropad-claude@local/` | a extensão do GNOME Shell |
| `claude/title-hook.py`, `claude/keybindings.json` | emoji no título e atalho de voz do Claude Code |
| `macropad/claude-macropad.yaml` | o que o `flash.sh` grava no macropad (F13 a F21) |
| `system/` | regra udev e modelo do serviço systemd |
| `install.sh`, `flash.sh` | instalação e gravação do macropad |
| `tests/` | testes do daemon (gestos e combinações) |
| `vendor/ch57x-keyboard-tool/` | gravador do macropad, de terceiros (MIT) |
| `SPEC-gestos-modos.md` | registro do desenho de gestos e modos (histórico; o código é a referência) |
