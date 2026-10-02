# agent-throttle

Rode vários agents de código (Claude Code, Codex) na mesma máquina sem derrubá-la. Validações pesadas em slots derivados do hardware, teto
de workers com número literal garantido por um hook, uma checagem de memória antes de cada agent novo e as métricas
para calibrar tudo isso.

[Read in English](README.md)

## Quickstart

```bash
git clone https://github.com/lucasrosati/agent-throttle && ./agent-throttle/install.sh
solo sleep 3                          # roda pelo semáforo da máquina: [solo] rc=0 in 3s (wait 0s)
throttle-guard --check 'npx jest'     # blocked (jest-no-workers): Jest needs --maxWorkers=N (<=2 outside solo).
```

A primeira linha instala os comandos em `~/.local/bin` e acrescenta o hook ao `~/.claude/settings.json` (com backup
antes e mantendo seus outros hooks). Rode `solo sleep 3` em mais terminais do que os slots disponíveis para ver o excedente esperar. A
terceira mostra a checagem que o Claude Code passa a fazer antes de cada comando Bash. Depois, cole as regras para os
seus agents ([abaixo](#regras-para-os-agents)).

## Status

- **Versão 0.1.0**, a primeira pública.
- **Testado em macOS e Linux** (o guard e o semáforo): todos os testes rodam no CI em `ubuntu-latest` e
  `macos-latest`.
- **Próxima versão (Unreleased):** slots e workers derivados do hardware, gate de memória, locks com PID + início e veredito portátil.
- **Jobs agendados:** launchd no macOS; timers no Linux seguem como contribuição separada.
- **Defaults em runtime:** slots e tetos internos de Jest/Playwright são derivados dos núcleos e da RAM em cada execução.
  Use o relatório semanal para calibrar a configuração.

## O problema

Agents escrevem código em paralelo sem problema: uma sessão usa bem menos de 1 GB. O que derruba a máquina é a
validação. Três agents terminaram ao mesmo tempo, cada um rodou a suíte Jest inteira com os workers padrão (um por
núcleo), e um notebook de 16 GB passou de 27 GB de memória; as três sessões morreram antes de abrir o pull request.
Nenhum agent errou pelas próprias instruções: cada um rodou os testes antes de terminar. A coordenação precisa morar na
máquina. A história completa e as medições por trás de cada default estão em [docs/why.md](docs/why.md) (em inglês).

## O que vem no pacote

| Comando | O que faz |
|---|---|
| `solo <comando>` | Roda num slot disponível derivado do hardware, compartilhado por todos os agents e terminais. Reduz workers por slot, controla slots extras pela memória, trata timeout e dono morto e registra cada execução. |
| `solo-ci` | Quando a fila está longa: faz push da branch (nunca forçado, nunca `main`) e espera o CI do pull request no lugar da validação local. |
| `throttle-guard` | Hook `PreToolUse` do Claude Code. Bloqueia Jest, Vitest, pytest e Playwright sem número literal de workers ou acima do teto, suíte inteira fora do `solo` (inclusive quando o caminho passado É a suíte), modo watch, runners interativos e `tsc` do projeto. Entende `npx`, `pnpm`, `uv run`, scripts do `package.json` e `bash -c`. |
| `throttle-load` | Foto da memória com um veredito antes de lançar outro agent: "ok to launch" ou "DO NOT launch". Usa memória, pressão, crescimento de swap, sinais térmicos e contagem de agents em runtime. Com `--log`, uma amostra para as métricas. macOS e Linux. |
| `throttle-clean` | Fim do dia: poda worktrees, apaga saída de build dentro delas e oferece matar processos de teste que sobraram. |
| `throttle-report` | Relatório semanal em Markdown: sessões mortas, pressão de memória, espera pelo slot, durações, timeouts, bloqueios do guard por regra. |
| `throttle-logrotate` | Mantém os últimos 60 dias de log. |
| `throttle-config` | Mostra a configuração efetiva. |

Tetos padrão de workers, dentro do `solo` / fora:

| Runner | Dentro do `solo` | Fora |
|---|---|---|
| Jest `--maxWorkers` | ceil(C/2) | 2 |
| Vitest `--maxWorkers` | 4 | 2 |
| pytest `-n` | 4 | 2 |
| Playwright `--workers` | min(ceil(C/2), 4) | 1 |

Os tetos internos são derivados dos núcleos e da RAM em runtime; pares explícitos em `[limits]` têm prioridade.
C é o número de performance cores no macOS (CPUs lógicas como fallback), ou `nproc` no Linux. O `solo` fixa
W = min(ceil(C/2), floor(C/slots_ocupados)) na aquisição e apenas reduz flags. Playwright tem teto padrão de 4.
Slots = max(1, min(floor(C/max(1, floor(C/2))), floor(RAM_GB/10))); config ou `SOLO_SLOTS` / `SOLO_CORES` sobrescrevem.
Slots extras exigem pelo menos 25% de memória livre e o p90 dos picos recentes de suítes; o slot 1 ignora esse gate.

O número de agents codando ao mesmo tempo (`max_agents`) sai por padrão de `floor((RAM em GB - 6) / 2)`, entre 1 e 12. É um orçamento de memória; use um inteiro para o seu teto separado de quota.

## Instalação

Requisitos: macOS ou Linux, bash, git, Python 3.11 ou mais novo. `gh` para o `solo-ci` e para a contagem opcional de
pull requests.

```bash
git clone https://github.com/lucasrosati/agent-throttle
cd agent-throttle
./install.sh                 # opções: --prefix DIR, --python PATH, --settings FILE, --no-hook, --launchd, --force
```

O `install.sh` pode rodar de novo a qualquer momento (ele atualiza o que instalou). Ele:

- copia os arquivos para `~/.local/share/agent-throttle` e cria os links dos comandos em `~/.local/bin` (um comando que
  já existe com o mesmo nome fica como está, a não ser com `--force`);
- fixa o Python que encontrou, porque launchd e hooks não leem o perfil do seu shell;
- cria `~/.config/agent-throttle/config.toml` a partir do exemplo, se você ainda não tem um;
- mescla o hook no `~/.claude/settings.json`: backup em `settings.json.bak-agent-throttle-<data>`, seus outros hooks e
  configurações mantidos, JSON validado depois da escrita, nada escrito se o arquivo não for JSON válido;
- com `--launchd` (macOS), pergunta e então carrega três jobs: uma amostra de memória a cada 5 minutos, o relatório
  semanal na segunda às 08:00 e a rotação diária dos logs.

O `./uninstall.sh` remove o hook (com backup), os jobs, os links e os arquivos instalados. A configuração e os logs
ficam, a não ser com `--purge`.

## Configuração

`throttle-config path` mostra onde fica a configuração; `throttle-config show` imprime os valores efetivos. O
[config de exemplo](config/config.example.toml) documenta cada chave e termina com uma seção sobre como calibrar os
números para o seu hardware usando o `throttle-load` e uma suíte medida pelo `solo`. Um valor errado gera um aviso e
volta ao default: um erro de digitação nunca desliga uma ferramenta.

## Regras para os agents

O hook cobre o que dá para ver em um comando. Cole as regras para que os agents também saibam quando lançar outro
agent, como ler a saída do `solo` e o que fazer quando a fila está longa:

- Claude Code: [templates/claude-md-rules.md](templates/claude-md-rules.md) no `~/.claude/CLAUDE.md`.
- Codex: [templates/agents-md-rules.md](templates/agents-md-rules.md) no `~/.codex/AGENTS.md`. O Codex não roda os
  hooks do Claude Code, então para ele as regras são a única proteção.

Mais em [docs/rules-for-agents.md](docs/rules-for-agents.md), inclusive como manter as regras da sua máquina para um
repositório fora do repositório.

## Plataformas

| | macOS | Linux |
|---|---|---|
| `solo`, `solo-ci`, `throttle-guard`, `throttle-config`, `throttle-clean`, `throttle-logrotate` | sim | sim |
| `throttle-report` | sim | sim |
| `throttle-load` | sim | sim, com fallbacks conservadores dos sinais |
| jobs agendados | launchd | ainda não (timers do systemd são bem-vindos) |

Feito para o Claude Code 2.1 (o hook) e o Codex CLI 0.15 (só regras, sem hook). O CI roda todos os testes em
`ubuntu-latest` e `macos-latest`.

## Limitações conhecidas

- O Codex não tem hook aqui: só as regras.
- O guard não lê `projects` do Jest, configs só com `testRegex`, nem valores de config que não sejam strings
  literais; `node --test`, `python -m unittest` e `make` não são reconhecidos. Veja
  [docs/how-it-works.md](docs/how-it-works.md#known-gaps).
- O preload Node cobre Jest/Playwright em scripts npm/pnpm/yarn. `cross-env NODE_OPTIONS=...` o substitui; o guard
  continua exigindo flags literais. Node é opcional para comandos que não usam Node.
- A pressão de memória no Linux usa PSI (`some.avg10` >= 1% amarela, `full.avg10` >= 10% vermelha, configurável). Sem PSI
  ou limites térmicos hot/critical utilizáveis, o veredito bloqueia. Esses sinais não equivalem à pressão do macOS.
- O veredito exige uma amostra de swap nos últimos 30 minutos: `throttle-load --log`. Swap total acima de RAM/8 só avisa,
  salvo se você mantiver explicitamente o limite legado `max_swap_mb`. Jobs agendados continuam exclusivos do macOS.

## Documentação (em inglês)

- [Why](docs/why.md): o incidente e as medições.
- [How it works](docs/how-it-works.md): o semáforo, as regras do hook, os arquivos.
- [Rules for agents](docs/rules-for-agents.md).
- [Metrics](docs/metrics.md): os logs e o relatório semanal.
- Lições: [repositórios dentro de pastas sincronizadas](docs/lessons/icloud-synced-folders.md),
  [`@import` de fora do projeto no CLAUDE.md](docs/lessons/claude-md-external-imports.md).

## Contribuindo

Veja o [CONTRIBUTING.md](CONTRIBUTING.md). Jobs agendados no Linux e medições de outros hardwares são contribuições bem-vindas.

## Licença

[MIT](LICENSE)
