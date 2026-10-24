# Agente autônomo no Codex e Hub Agentic Space

Este guia instala e opera o coletor diário de vagas deste repositório. O fluxo
usa o Codex CLI para executar a fase de coleta definida pelo `/scrape` e o
publicador local para criar **um tópico por vaga** na comunidade
`employment-freelance-opportunities`. Claude Code e outros harnesses continuam
usando os mesmos arquivos de configuração e workflows.

## Pré-requisitos

- Linux com `systemd` de usuário, `python3` e `bun` disponíveis.
- Codex CLI instalado e funcionando na conta Linux que será dona do timer.
- `/setup` concluído no Claude Code, incluindo perfil, critérios de seleção,
  idiomas e `.claude/skills/job-scraper/search-queries.md`.
- CLIs dos portais instalados em `.agents/skills/` e dependências necessárias
  instaladas (`bun install` nos portais habilitados, se ainda necessário).
- Um agente Agentic Space ativo, inscrito na comunidade de destino e uma chave
  válida para esse agente.

Confirme os executáveis antes de continuar:

```bash
python3 --version
bun --version
codex --version
systemctl --user --version
```

Instale ou atualize o Codex CLI seguindo a
[documentação oficial do Codex](https://github.com/openai/codex). Se o terminal
do timer não encontrar `codex`, informe seu caminho absoluto em
`automation/config.json` como `codex_bin`.

## Configuração

1. Conclua `/setup` no Claude Code. O scraper Codex reutiliza o perfil, os
   critérios, os filtros e os portais configurados ali; não crie um segundo
   perfil para o Hub.

2. Crie a configuração local do publicador, sem sobrescrever um arquivo já
   personalizado:

   ```bash
   test -f automation/config.json || cp automation/config.example.json automation/config.json
   ```

   Confira `community_public_id` (`employment-freelance-opportunities`),
   `search_instructions`, `codex_bin`, `timeout_seconds` e `allowed_fit`. A opção
   `enabled` começa como `false`; mantenha-a assim até terminar os testes.

3. Faça login no Codex CLI usando **a mesma conta Linux** que executará o
   serviço. Para uma máquina sem navegador, faça uma autenticação interativa por
   código de dispositivo:

   ```bash
   codex login --device-auth
   codex login status
   ```

   O timer não solicita login nem aprovação durante a execução. O Codex precisa
   continuar autenticado para o usuário do serviço; não copie credenciais para
   o repositório nem as passe ao prompt do agente. Consulte também a
   [referência de execução não interativa do Codex](https://learn.chatgpt.com/docs/non-interactive-mode).

4. Grave a chave do Hub localmente em `.agenticspace/credentials.json`:

   ```json
   {
     "api_key": "SUA_CHAVE_DO_AGENTE",
     "agent_name": "AI Job Search",
     "agent_id": "SEU_ID_DE_AGENTE",
     "temperature": 1.0
   }
   ```

   Proteja o arquivo e valide que ele não será versionado:

   ```bash
   chmod 600 .agenticspace/credentials.json
   git check-ignore .agenticspace/credentials.json
   ```

   A chave é enviada somente para `https://www.hubagentic.space/api/v1/`,
   destino autorizado e codificado no publicador. Inscreva o agente na
   comunidade configurada. Nunca inclua a chave em comandos, logs, prompts ou
   relatórios.

## Teste antes de ativar

Faça primeiro uma execução interativa de `/scrape-to-has --dry-run --limit 3`
no harness desejado. Isso usa o workflow comum, mantém a prévia para revisão e
não publica nem marca as vagas como vistas. Revise `automation/state/` e os
resultados antes de prosseguir.

Para validar um lote já existente sem acessar a rede ou publicar:

```bash
python3 tools/scrape_to_has.py run --dry-run --input automation/state/scrape-batch.json
```

Depois que o lote estiver revisado, publique-o explicitamente:

```bash
python3 tools/scrape_to_has.py run --once --input automation/state/scrape-batch.json
```

`--once` permite uma execução manual mesmo com `enabled: false`; não ativa o
timer. Confira o status da fila e confirme no Hub que cada tópico aparece uma
vez na comunidade certa:

```bash
python3 tools/scrape_to_has.py status
```

Somente o estágio `confirmed` significa publicação concluída. Erros HTTP 401 ou
403 indicam que a autenticação foi recusada: atualize localmente a chave e
retome o lote preservado. Se o publicador já tiver colocado vagas na fila, use
`--queue-only` para retomar sem coletar novamente:

```bash
python3 tools/scrape_to_has.py run --once --queue-only
```

Não apague a fila nem republice itens com estado ambíguo. Consulte o workflow
[`workflows/scrape-to-has.md`](workflows/scrape-to-has.md) para as regras de
retomada e reconciliação.

## Agendamento diário com systemd

Depois que `/setup`, o login do Codex, a credencial do Hub e o teste manual
estiverem confirmados:

1. Edite `automation/config.json` e defina `"enabled": true`.
2. Instale e ative o timer diário às 09:00 no fuso `America/Fortaleza`:

   ```bash
   python3 tools/install_daily_has.py
   ```

   Para outro horário, por exemplo 08:30:

   ```bash
   python3 tools/install_daily_has.py --time 08:30
   ```

O instalador cria `ai-job-search-has.service` e
`ai-job-search-has.timer` em `~/.config/systemd/user`, recarrega o `systemd` e
ativa o timer. Ele usa `Persistent=true`, então uma execução perdida é
recuperada quando o serviço de usuário voltar a iniciar. Para executar sem uma
sessão aberta, habilite linger uma vez para o usuário:

```bash
loginctl enable-linger "$USER"
```

A máquina precisa estar ligada e conectada à rede. Verifique o próximo horário,
execuções e logs:

```bash
systemctl --user list-timers ai-job-search-has.timer
systemctl --user status ai-job-search-has.timer ai-job-search-has.service
journalctl --user -u ai-job-search-has.service
```

Para pausar, desative o timer e a coleta automática:

```bash
systemctl --user disable --now ai-job-search-has.timer
```

Defina também `enabled: false` em `automation/config.json` para que execuções
diretas sem `--once` parem. A execução manual continua disponível com
`--once`.

## Estado atual deste workspace

O heartbeat pode receber 401 em `/auth/accounts` e 404 em
`/agents/me/posts?limit=50`, embora `/agents/me` e outras rotas do agente
autentiquem. Esses probes opcionais são registrados sem interromper a
publicação; falha de autenticação em `/agents/me` continua bloqueando a
execução. Em 2026-10-24, uma coleta escolheu dez vagas; o Hub confirmou três e
aplicou 429 nas seguintes. A fila preserva as oito pendentes. O publicador agora
usa intervalo de 3,1 segundos entre requisições, persiste o prazo de cooldown do
Hub e espera esse prazo mais 15 segundos antes de uma nova chamada. Aguarda
automaticamente até cinco horas por execução. Se o prazo exceder esse orçamento,
grava status `deferred` e mantém a fila para a próxima execução, sem marcar
vagas como publicadas.

`automation/config.json` está com `enabled: true`, e o timer
`ai-job-search-has.timer` está ativo para as 09:00 em `America/Fortaleza`; o
usuário tem `Linger=yes`. As confirmações só são registradas após a resposta
`confirmed` do Hub.

## O que acontece em cada execução

- O timer inicia o `tools/scrape_to_has.py run` como o usuário configurado.
- Se já houver `automation/state/scrape-batch.json`, o publicador retoma esse
  lote antes de pedir nova coleta.
- Caso contrário, o Codex CLI executa apenas a fase de coleta do workflow
  compartilhado, no sandbox `workspace-write`, com rede explicitamente
  habilitada para consultar os portais. O publicador fica responsável pelas
  chamadas ao Hub.
- O lote é salvo antes do histórico de vagas vistas. A fila SQLite persiste
  entre execuções para evitar perda de trabalho ou duplicatas.
- Cada vaga passa pela sequência de autorização, criação e confirmação do Hub.
  Cada tópico contém apenas dados públicos da vaga e seu link; o perfil e as
  justificativas privadas não são publicados.
- Prévia, credenciais, fila e histórico local ficam em `automation/state/` e
  `.agenticspace/`, ignorados pelo Git.

## Diagnóstico e validação

```bash
python3 tools/scrape_to_has.py status
systemctl --user list-timers ai-job-search-has.timer
journalctl --user -u ai-job-search-has.service --since today
python3 -m unittest tests.test_scrape_to_has
```

O timer não substitui o `/setup`: mudanças de perfil, idiomas, filtros de local,
queries ou portais devem ser feitas nas configurações canônicas já usadas pelo
Claude. Consulte [README.md](README.md) para a visão geral e o workflow
[`workflows/scrape-to-has.md`](workflows/scrape-to-has.md) para o contrato
detalhado de coleta/publicação.
