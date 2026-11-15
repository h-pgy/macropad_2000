# SPEC: gestos × ações × modos (para implementar)

Estado em 2026-11-13: existe `behavior` (tap/toggle/hold) só no `combo` (`BEHAVIORS`, `LEGACY_ACTIONS`, `Runner._press_<nome>`). Esta spec substitui isso. Revisada em 2026-11-13 com o usuário (decisões no fim).

Pré-requisito confirmado: o macropad manda o soltar na hora real (o backspace com `hold` repete).

**Limite do hardware (descoberto no teste, 2026-11-14):** com um botão segurado, girar o knob chega como "soltou" do botão no mesmo instante do giro, e o macropad não avisa mais nada daquele botão. Por isso a **camada (profile + while_held) e a regra "outro botão → segurar" foram tiradas**: o botão segurado vale como toque. Config antigo com profile + while_held vira toggle (por um toque). *Correção (medido depois, no mesmo dia):* entre botões isso não acontece. Dois botões chegam apertados ao mesmo tempo, com as solturas na hora certa; daí vieram as combinações de dois botões (README, "Combinações").

## Conceito

```
Botão ─▶ Gesto (tap | long | double) ─▶ Ação (o quê; definida no código) + Modo (como roda no tempo)
```

- **Ação:** combo, text, profile, scroll, launch, url, none. O usuário não cria ações.
- **Modo** (na UI: "Funcionamento"): once, toggle, latch, while_held, cycle, switcher. Cada ação libera alguns.
- **Gesto:** tap ("Ao tocar"), long ("Ao segurar"), double ("Toque duplo"). Cada gesto tem a sua ação e o seu modo.

### Modos

| Modo | UI | Faz |
|---|---|---|
| once | Uma vez | executa uma vez |
| toggle | Interruptor | **fraco**: liga; desliga com o mesmo gesto **ou com a próxima ação** de outro (botão, gesto) |
| latch | Trava | **forte**: liga; só o mesmo gesto do mesmo botão desliga |
| while_held | Enquanto apertado | ligado enquanto o botão estiver apertado |
| cycle | Alternar (lista) | cada vez, o próximo item da lista |
| switcher | Alternador de janelas | estilo Alt+Tab (lógica atual) |

## Ação → modos permitidos (o 1º é o padrão)

| Ação | Modos | Payload |
|---|---|---|
| combo | once, toggle, latch, while_held, cycle, switcher | `combo`: str; lista se cycle |
| text | once, cycle | `text`: str; lista se cycle |
| profile | latch, toggle | `profile`: nome do perfil |
| scroll | once | `amount` |
| launch | once | `app`, `new_window` |
| url | once | `url` |
| none | once | — |

- `cycle` exige lista com ≥ 2 itens. Fora do cycle, o payload é um item só.
- `switcher` exige que o combo comece com modificador (regra atual).
- combo: toggle = segura as teclas até desligar (voz); latch = modificador travado tipo Caps Lock.
- profile: latch = trava/destrava (comportamento atual); toggle = **perfil por um toque** (a próxima ação roda nele e volta sozinho); while_held = camada temporária (ao soltar, volta ao estado anterior, inclusive a um perfil travado).
- "Outra ação" (que desliga o fraco) = qualquer gesto resolvido de outro (botão, gesto), inclusive o outro gesto do mesmo botão e ligar uma trava. O fraco do combo solta **antes** dessa ação (senão o Enter sai como Alt+Enter na voz); o do profile desliga **depois** (a ação roda no perfil).
- Só um fraco ligado por vez. Travas podem coexistir (várias de combo; de perfil, uma: travar outro perfil substitui).
- `scroll` + `while_held` foi descartado (para isso: combo `pagedown` + while_held).

## Gesto → modos permitidos

| Gesto | once | toggle | latch | while_held | cycle | switcher |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| tap | ✅ | ✅ | ✅ | ✅ só se o botão **não** tiver `long` **nem** `double` | ✅ | ✅ só se o botão não tiver `long` nem `double` |
| long | ✅ | ✅ | ✅ | ✅ (dispara ao bater o tempo, dura até soltar) | ✅ | ❌ |
| double | ✅ | ✅ | ✅ | ✅ (2º toque segurado) | ✅ | ❌ |

Motivo do tap + while_held: com long ou double, o toque só é decidido no soltar (ou 250 ms depois); a tecla seria apertada com o botão já solto e ficaria presa.

## Controle → gestos permitidos

| Controle | Gestos | Modos |
|---|---|---|
| Botões 1–6 (F13–F18), apertar o knob (F20) | tap, long, double | conforme as tabelas acima |
| Girar o knob (F19, F21) | só tap | once, cycle, switcher |

Validação: no daemon (`parse_config` levanta `ValueError` legível) e as mesmas tabelas expostas para a UI filtrar as opções.

## Formato do config.toml

> Histórico: em 2026-11-14 o config passou para `config.json`, com a mesma estrutura (veja o README). Os exemplos abaixo ficaram em TOML.

Os campos no topo da tecla = gesto `tap` (compatível com o formato atual). `long` e `double` são tabelas inline aninhadas, cada uma com `label` opcional. **Tudo numa linha só por tecla**: tabela inline em várias linhas não é TOML 1.0 (o `tomllib` recusa).

```toml
F15 = { label = "backspace", action = "combo", combo = "backspace", mode = "while_held" }
F13 = { label = "voz", action = "combo", combo = "alt+k", mode = "toggle" }
F18 = { label = "copiar/colar", action = "combo", combo = ["ctrl+c", "ctrl+v"], mode = "cycle" }
F21 = { label = "janela →", action = "combo", combo = "alt+tab", mode = "switcher" }
F14 = { label = "enter", action = "combo", combo = "enter", long = { label = "Fn", action = "profile", profile = "Global", mode = "while_held" }, double = { label = "clear", action = "text", text = "/clear\n" } }
```

- Um botão só com `long`/`double` e sem `tap`: `action = "none"` no topo.
- Herança do Global continua **por botão inteiro** (todos os gestos juntos), não por gesto.
- Tempos globais, opcionais: `[timing] long_ms = 350, double_ms = 250`.

### Legado (aceitar na leitura; o server grava sempre o formato canônico)

| Antigo | Novo |
|---|---|
| `behavior = "tap"` / `"hold"` / `"toggle"` | `mode = "once"` / `"while_held"` / `"toggle"` |
| `action = "hold_toggle"` | combo + toggle |
| `action = "cycle", combos = [...]` | combo + `combo = [...]` + cycle |
| `action = "switcher"` | combo + switcher |
| `action = "profile"` sem mode | profile + latch |

## Daemon: implementação

### Registro (substitui BEHAVIORS/LEGACY_ACTIONS)
```python
MODES = ("once", "toggle", "latch", "while_held", "cycle", "switcher")
ACTIONS = {  # kind -> modos (1º = padrão); payload validado em parse_<kind>
  "combo": ("once", "toggle", "latch", "while_held", "cycle", "switcher"),
  "text": ("once", "cycle"), "profile": ("latch", "toggle"),
  "scroll": ("once",), "launch": ("once",), "url": ("once",), "none": ("once",)}
GESTURES = ("tap", "long", "double")
GESTURE_MODES = {"tap": MODES, "long": MODES - switcher, "double": MODES - switcher}
TAP_NEEDS_ALONE = ("while_held", "switcher")  # tap só se o botão não tiver long nem double
TURN_KEYS = {F19, F21}; TURN_MODES = ("once", "cycle", "switcher")
```
`parse_config` → `profiles[i]["buttons"][code] = {"tap": act|None, "long": act|None, "double": act|None}`, onde `act = {"kind", "mode", "label", payload...}`.

### Interface das ações (Runner)
- `fire(act, item)`: executa uma vez (once, cycle; o cycle escolhe o item).
- `start(act)` / `stop(act)`: estado ligado (toggle, latch, while_held). combo = teclas down/up; profile = callback no main (trava / perfil por um toque / camada).
- switcher: continua específico do combo (modificador "sticky" + deadline).

Estado ligado: `active[(botão, gesto)] = {"act", "mode", "stop"}`. **O mesmo gesto do mesmo botão sempre desliga**, mesmo que o perfil ativo tenha outra coisa nele: no snapshot do aperto, cada (botão, gesto) em `active` sobrepõe a ação do perfil (e o gesto passa a existir no botão, para o resolvedor detectá-lo). Isso cobre o `lock_key` de hoje e os toggles/travas de combo.

Modos genéricos:
- once → fire.
- cycle → fire(item[pos]); pos por (perfil, botão, gesto).
- toggle → ligado: stop; senão desliga o fraco atual e start.
- latch → ligado: stop; senão start.
- while_held → start; registra `pressed[btn] = stop` (o soltar chama, mesmo se o perfil mudou).
- switcher → lógica atual.
- Antes de qualquer gesto resolvido que não seja o do fraco ligado: combo fraco → stop antes; profile fraco → stop depois.

Perfil: `choose()` = topo da pilha de camadas > perfil por um toque > travado > janela em foco. Empilhar/desempilhar camada e o perfil por um toque **não** chamam `release_all()` (o `set_lock` atual chama quando o perfil muda: separar). Trava vinda da UI/barra: `default_lock_key` acha o (botão, gesto) com profile + latch para aquele perfil.

### Resolvedor de gestos (máquina de estados por botão)
Na hora do aperto, guarda um snapshot dos gestos do botão (perfil ativo naquele momento + `active`).

- **Só tap:** dispara no aperto (latência zero, igual a hoje).
- **Tem long:** aperto → timer `long_ms`.
  - Soltou antes → tap (ou espera o double, se houver).
  - O timer venceu ainda apertado → long (while_held dura até soltar).
- **Tem double:** soltou o 1º toque → janela de `double_ms`.
  - 2º aperto dentro da janela → double, disparado no aperto (while_held dura até soltar o 2º).
  - A janela venceu → tap.
- **Outro botão apertado enquanto este está pendente:**
  - Resolve como tap (girando o knob com um botão segurado, o "soltou" falso do macropad já chega antes do giro).
  - Depois processa o outro botão normalmente.
- Durações medidas pelo timestamp dos eventos (o loop fica ocupado enquanto digita texto); o timer só decide quando não chegou evento.
- Tap resolvido com atraso: once, toggle, latch e cycle disparam na hora em que o gesto é decidido.
- Auto-repeat do dispositivo (value 2): ignorar nas teclas mapeadas (as não mapeadas continuam repassadas).
- Durante o switcher (modificador segurado), o aperto de outro botão só confirma e o gesto inteiro dele é engolido.
- `select` timeout = o menor prazo entre os timers de gesto e o deadline do switcher.
- Reload, desconexão ou mudança do modo teste: cancela os pendentes sem disparar + `release_all()` (teclas, camadas, perfil por um toque). A trava de perfil sobrevive ao reload, como hoje.
- Modo teste: resolve o gesto normalmente (a UI vê qual foi) e não executa nada.

### Eventos e uso
- Evento `key` da UI ganha `gesture` e `mode` (o modo da ação). O campo antigo `mode: lock/unlock` do acionar perfil vira `profile_op`: `lock`/`unlock` (trava), `oneshot`/`oneshot_off` (por um toque), `layer`/`layer_off` (camada), com `target`. A mensagem `type: "mode"` (automático/fixo) não muda (só ganha `oneshot` e `layer`): a extensão depende dela; camada e perfil por um toque não mandam popup para a extensão.
- Continuam valendo `hold: holding/released` (toggle/latch/while_held) e um evento no soltar do while_held.
- usage.db: recriar a tabela com `gesture TEXT NOT NULL DEFAULT 'tap'` na PK (linhas antigas = tap; ações antigas `hold_toggle`/`cycle`/`switcher` → `combo`). Contagem: 1 por uso (o soltar e o desligar não contam).

## ui/server.py
- `read_config`: normalizar o legado e devolver `keys[F] = {inherit, tap: G, long: G|None, double: G|None}`, com `G = {label, action, mode, text, combo, items, amount, app, new_window, url, profile}` (`items` = lista do cycle).
- `dump_action`: gravar o canônico, uma linha por tecla; omitir `mode` quando for o padrão da ação; long/double como inline aninhado.
- `/api/config` também devolve `meta` (vindo do `mc`; cópia se faltar o evdev): ACTIONS→modos, GESTURE_MODES, TAP_NEEDS_ALONE, restrições do knob.
- Saúde: voz = qualquer combo com toggle/latch, em qualquer gesto. Effort = combos shift+up/down em qualquer gesto.
- Uso: mostrar o gesto no nome do macro quando não for tap.
- Reiniciar o `ui/server.py` depois de editar.

## ui/index.html
- Editor do botão: 3 linhas, "Ao tocar / Ao segurar / Toque duplo". Cada uma com select de ação e select "Funcionamento" filtrado por `meta`. Long e double vêm recolhidos ("+ adicionar").
- Modo inválido para a combinação: opção desabilitada com o motivo (ex.: "Enquanto apertado indisponível: este botão tem 'Ao segurar'").
- Ao configurar long/double, aviso: "o toque deste botão passa a disparar ao soltar (pequeno atraso)".
- Girar o knob: só a linha "Ao tocar" e os modos once, cycle, switcher.
- Cycle: o campo vira uma lista (texto separado por vírgula), para combo e text.
- Tirar `hold_toggle`, `cycle` e `switcher` do select de ações.
- `describe()`: mostrar o modo e os gestos (ex.: "enter · segurar: Fn").
- Log e modo teste: mostrar o gesto detectado.

## Testes (teclado falso, relógio injetável)
- Parse: todas as combinações da tabela, válidas e inválidas (mensagens legíveis); legado → canônico.
- Gestos: tap puro sem latência; long (antes e depois do tempo); double (dentro e fora da janela); long + double; outro botão durante a pendência (while_held → long; outro → tap).
- toggle fraco desliga com outra ação (combo antes, profile depois); latch não desliga; mesmo gesto desliga mesmo com outro perfil ativo.
- while_held: soltar com o perfil trocado no meio; release_all no reload.
- profile: latch (o mesmo gesto destrava), toggle (perfil por um toque), while_held (volta ao travado anterior).
- server: ida e volta do config sem mudanças; legado gravado no formato canônico.
- usage: migração da tabela.

## Docs
README: seções "Ações disponíveis" e "Comportamento do botão" → "Gestos, ações e modos" com as tabelas. Atualizar o cabeçalho do config (HEADER no server) e o docstring do daemon.

## Decisões da revisão (2026-11-13)
- Dois interruptores: fraco (`toggle`, "Interruptor") e forte (`latch`, "Trava"). Podem estar no mesmo botão em gestos diferentes (ex.: toque = perfil por um toque, toque duplo = trava, como o Shift do celular).
- Perfil por um toque (profile + toggle): aprovado.
- ~~"Outro botão apertado → segurar" só quando o long é while_held.~~ Tirado junto com a camada (limite do hardware).
- Camada Fn: tirada (2026-11-14). O usuário preferiu combinações de botões ("macro dentro do macro"), feitas no mesmo dia como pares simultâneos de botões (sem o knob), com janela de 50 ms e herança do Global (README, "Combinações").
- Campo "modo" na UI = "Funcionamento".
