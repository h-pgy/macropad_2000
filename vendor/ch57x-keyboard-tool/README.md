# ch57x-keyboard-tool

Programa de linha de comando que **grava a configuração dentro do macropad**: diz ao teclado qual tecla cada botão e cada movimento do knob manda. Este projeto usa ele só no `./flash.sh`, para gravar `macropad/claude-macropad.yaml`: cada botão passa a mandar uma tecla de F13 a F21. Depois disso, o daemon decide o que cada tecla faz, então mudar as funções pela UI **não** exige gravar o teclado de novo.

Funciona com os macropads genéricos de chip CH57x (USB `1189:8890`, `1189:8840`, `1189:8842`).

| | |
|---|---|
| Autor | Mikhail Trishchenkov ([kriomant](https://github.com/kriomant)) |
| Código-fonte | <https://github.com/kriomant/ch57x-keyboard-tool> (Rust) |
| Licença | MIT (`LICENSE`, cópia do repositório original na tag `v1.7.0`) |
| Versão aqui | `v1.7.0`, binário oficial para Linux x86_64 |
| Origem | [`ch57x-keyboard-tool-x86_64-unknown-linux-gnu.tar.gz`](https://github.com/kriomant/ch57x-keyboard-tool/releases/tag/v1.7.0) do release v1.7.0 |
| SHA-256 do binário | `63c80f42e79cebb05da495fb9dcb79539ac74fbd840123c0e5ada0f188ac7a2b` |

Não é código deste projeto: os arquivos estão aqui sem modificação, como vieram do release, para o repositório funcionar sem baixar nada (`git clone` + `./install.sh` + `./flash.sh`).

## Arquivos

- `ch57x-keyboard-tool`: o executável (2,5 MB).
- `example-mapping.yaml`: exemplo de configuração que vem no release, com comentários sobre o formato. Serve de referência para mexer em `macropad/claude-macropad.yaml`.
- `LICENSE`: licença MIT do autor.

## Uso

```bash
./flash.sh                                                     # o jeito normal: valida e grava
vendor/ch57x-keyboard-tool/ch57x-keyboard-tool show-keys       # lista as teclas e modificadores aceitos
vendor/ch57x-keyboard-tool/ch57x-keyboard-tool --help
```

## Outra arquitetura ou atualizar a versão

- Em ARM (aarch64), o `flash.sh` baixa o binário da mesma versão para `aarch64/` nesta pasta (fora do git). Em outros sistemas: `cargo install ch57x-keyboard-tool`, ou um binário pronto da [página de releases](https://github.com/kriomant/ch57x-keyboard-tool/releases).
- Para atualizar: baixe o `.tar.gz` do release novo, troque o executável aqui, atualize `VERSION` no `flash.sh` e a versão e o SHA-256 acima (`sha256sum ch57x-keyboard-tool`). Grave e teste o macropad antes de fazer commit.
