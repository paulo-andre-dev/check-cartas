# Monitor de Cartas de Consórcio Imobiliário Contempladas

Monitor local que coleta diariamente cotas contempladas de imóveis em
plataformas de revenda de consórcio, calcula desembolso real e percentual
de entrada, valida consistência aritmética dos anúncios, e alerta por
Telegram as oportunidades dentro dos seus critérios.

## Estado atual do projeto

- **Fundação completa**: modelos, filtros financeiros, consistência,
  combinações, deduplicação, persistência SQLite, evidências, bot de
  Telegram com `/silenciar`. Regras de valor: **sem restrição de crédito
  por faixa** — qualquer valor entra, desde que a entrada fique até 20%
  (classificação OURO ≤10%, EXCEPCIONAL ≤12%, MUITO BOA ≤15%, BOA ≤20%).
- **4 sites com adapter real e funcional, sem login**: Contemplei, Bidcon,
  Prime Cotas e Tramontana Consórcios — todos com API JSON pública
  (descobertas inspecionando o tráfego de rede real, nunca supostas).
  Juntos somam mais de 1.700 cotas de imóveis inspecionáveis por execução.
- **1 site confirmado como exigindo login**: ConsorcioCred
  (`api.consorciocred.com/offer` responde 401 sem sessão).
- **1 site bloqueado por proteção anti-bot**: MyCotas/Mycon (Cloudflare
  challenge) — não tentamos contornar.
- **Demais sites**: inspecionados (login não é a barreira na maioria),
  adapter ainda pendente porque exigem scraping de HTML/RSC em vez de API
  limpa. Ver seção "Status dos 20 sites".

## Instalação

Requer Python 3.12+. Neste ambiente de desenvolvimento não havia `pip`
nem `ensurepip` disponíveis no Python do sistema — se o seu notebook
tiver o mesmo problema, o bootstrap abaixo resolve.

```bash
cd monitor-cartas  # ou o diretório onde está este repositório

# se "python3 -m venv .venv" reclamar de pip ausente:
python3 -m venv .venv --without-pip
curl -s https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
.venv/bin/python /tmp/get-pip.py

# instalação normal do projeto
.venv/bin/pip install -e ".[dev]"

# navegador do Playwright (necessário só para sites que exigem login/JS
# pesado — a Contemplei não precisa). Pode pedir sudo para dependências
# de sistema; sem sudo, ainda funciona para sites sem esses requisitos.
.venv/bin/playwright install chromium
```

Copie os arquivos de exemplo e ajuste:

```bash
cp .env.example .env        # preencha TELEGRAM_BOT_TOKEN e TELEGRAM_ALLOWED_CHAT_IDS
# config.yaml já vem pronto com os valores da sua faixa (300k-400k, 15%/10%, teto 6000)
```

## Executando

```bash
# roda todos os sites ativos em config.yaml (sites.active)
.venv/bin/python -m monitor_cartas.cli run

# roda só um site
.venv/bin/python -m monitor_cartas.cli run --site contemplei

# status da última execução
.venv/bin/python -m monitor_cartas.cli status

# oportunidades dentro do percentual máximo configurado
.venv/bin/python -m monitor_cartas.cli list-opportunities

# combinações de 2-3 cotas da mesma administradora dentro da faixa de crédito
.venv/bin/python -m monitor_cartas.cli list-combinations

# erros/bloqueios registrados por adapter
.venv/bin/python -m monitor_cartas.cli list-errors

# silenciar/reativar uma cota específica
.venv/bin/python -m monitor_cartas.cli silence contemplei 865981
.venv/bin/python -m monitor_cartas.cli reactivate contemplei 865981
```

## Login manual (sites que exigem sessão)

A Contemplei **não precisa disso** — usa a API pública do marketplace
diretamente. Para adapters futuros que exigirem login:

```bash
.venv/bin/python -m monitor_cartas.cli login <nome-do-site>
```

Abre um Chromium visível, você faz login manualmente, a sessão fica salva
em `data/sessions/<site>/storage_state.json` (fora do Git). Nunca digite
senha no terminal — é tudo feito na janela do navegador.

## Bot do Telegram

Preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_ALLOWED_CHAT_IDS` no `.env`
(o projeto não procura tokens automaticamente em outros lugares — copie
manualmente).

**Se você já tem um bot** (ex.: reaproveitando de outro projeto seu no
Railway), pegue os dois valores de lá:

```bash
railway link --project crypto_bot --service bot_binance_volume
railway variables --kv | grep TELEGRAM
```

Isso mostra `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` (esse último vira o
único valor de `TELEGRAM_ALLOWED_CHAT_IDS`, separando por vírgula se tiver
mais de um chat). Cole os dois no `.env` deste projeto.

**Se precisar criar um bot do zero:**
1. Fale com [@BotFather](https://t.me/BotFather) no Telegram, mande `/newbot`,
   siga as instruções — ele te devolve o `TELEGRAM_BOT_TOKEN`.
2. Para achar o `chat_id`: mande qualquer mensagem pro seu bot novo, depois
   abra `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` no navegador —
   o `chat.id` aparece no JSON de resposta. Ou fale com
   [@userinfobot](https://t.me/userinfobot) pra pegar o ID da sua própria conta.

```bash
.venv/bin/python -m monitor_cartas.cli telegram
```

Isso sobe um processo separado e contínuo (não é o mesmo processo do
`run` diário). Comandos disponíveis: `/status`, `/novas`, `/melhores`,
`/detalhes <site> <id>`, `/silenciar <site> <id>`, `/reativar <site> <id>`,
`/silenciadas`, `/erros`.

Sem token configurado, o `run` funciona normalmente e apenas loga um
aviso em vez de enviar o alerta — nada quebra.

## Agendamento diário

**O notebook precisa estar ligado e conectado no horário agendado.**

Linux (cron), horário 08:00:

```
0 8 * * * cd /caminho/do/projeto && .venv/bin/python -m monitor_cartas.cli run >> logs/cron.log 2>&1
```

`config.yaml` tem `monitoring.alert_if_last_success_older_than_hours`
(padrão 30h) — rode `status` periodicamente (ou adicione ao cron) para
saber se uma execução foi perdida.

## Rodando 24/7 sem depender do notebook (Railway)

Cron local só funciona com o computador ligado. Pra rodar de verdade
sem depender disso, o projeto tem um worker contínuo
(`src/monitor_cartas/worker.py`) e um `Dockerfile` prontos pra deploy
numa plataforma tipo Railway:

```bash
python -m monitor_cartas.worker   # roda local pra testar: bot do Telegram + ciclo de coleta
```

O worker sobe duas coisas no mesmo processo: o bot do Telegram (sempre
ativo, pra `/silenciar` etc.) e um ciclo que roda o pipeline a cada
`CYCLE_INTERVAL_SECONDS` (padrão 86400 = 24h).

Todos os parâmetros de `config.yaml` podem ser sobrepostos por variável
de ambiente (útil pra ajustar direto no dashboard do Railway sem
redeployar):

| Variável | Equivale a |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | credenciais do bot |
| `SITES_ACTIVE` | `sites.active` (separado por vírgula) |
| `IMOVEL_MAX_CREDIT`, `IMOVEL_MAX_MONTHLY_PAYMENT` | `financial.modalities.imovel.*` |
| `VEICULO_MAX_CREDIT`, `VEICULO_MAX_MONTHLY_PAYMENT` | `financial.modalities.veiculo.*` |
| `MAX_ENTRY_PERCENTAGE`, `GOLD_ENTRY_PERCENTAGE`, `GOOD_ENTRY_PERCENTAGE` | tetos de classificação |
| `CYCLE_INTERVAL_SECONDS` | intervalo entre coletas |
| `DATA_DIR` | onde fica `cotas.db`/evidências (aponte pro volume persistente) |

## Como o pipeline decide o que é oportunidade

1. Adapter coleta o anúncio bruto (nunca calcula nada).
2. `core/filters.py` soma custos iniciais conhecidos (valor ao vendedor +
   taxas conhecidas), marca `has_unknown_fees` quando alguma taxa não é
   publicada — **nunca trata taxa desconhecida como zero**.
3. `core/consistency.py` compara parcelas restantes × parcela atual contra
   o saldo devedor anunciado; divergência acima do limite crítico
   (padrão 35%) marca a cota como "exigir extrato oficial" e ela some do
   alerta normal.
4. `core/confidence.py` classifica alta/média/baixa confiabilidade.
5. `core/filters.py::passes_modality_limits` aplica teto de crédito e de
   parcela **por modalidade** (`config.yaml` → `financial.modalities`):
   imóvel e veículo têm limites diferentes, porque o perfil de negociação
   é diferente. Cota que estoura o teto da própria modalidade não entra
   no alerta, mesmo com percentual de entrada bom.
7. `core/combinations.py` testa combinações de 2-3 cotas da mesma
   administradora — só vira alerta de "combinação confirmada" quando
   existe uma regra de administradora validada dizendo que múltiplos
   créditos podem ser usados no mesmo imóvel; caso contrário fica
   marcada como "potencial, regra a confirmar".
8. Tudo isso fica no SQLite (`data/cotas.db`) com histórico de preço e
   status. `/silenciar` é permanente até `/reativar` explícito.

## Regulamentos de administradora

`administrator_rules` no banco começa vazio (`PENDING_MANUAL_VALIDATION`)
para cada administradora nova que aparece nos anúncios. A validação real
do regulamento (transferência, combinação de cotas) é manual — não é
scraping automático — e deve ser preenchida via
`AdministratorRule`/`repo.upsert_administrator_rule(...)`.

## Testes

```bash
.venv/bin/pytest -q
```

35 testes cobrindo: dinheiro/BRL, filtros financeiros (incluindo o
exemplo real de 9,53% citado no projeto), consistência aritmética,
fingerprint/deduplicação, combinações de cotas, parsing do adapter da
Contemplei (fixtures reais salvas em `tests/fixtures/`) e o repositório
SQLite (dedupe em segunda execução, histórico de preço, silenciar/
reativar, remoção após N execuções sem ver o anúncio). Não fazem
requisição real à internet.

## Status dos 20 sites

| Site | Login? | Adapter | Observação |
|---|---|---|---|
| Contemplei | Não | ✅ Funcional | API pública `/v1/anuncios/publico` |
| Bidcon | Não | ✅ Funcional | API pública, exige header Origin/Referer |
| Prime Cotas | Não | ✅ Funcional | Supabase REST com chave anon pública |
| Tramontana Consórcios | Não | ✅ Funcional | API pública (plataforma "themedeploy") |
| Consórcio Market | Não | Pendente | Tabela pública rica (tem saldo devedor!), mas vem via RSC — precisa scraping de HTML renderizado |
| Grupo LuME | Não | Pendente | Preço confirmado em `/consorcios-contemplados-de-imoveis/` |
| Contemplado SP | Não | Pendente | Preço visível na home |
| DP Consórcios | Não | Pendente | Preço visível na listagem |
| Franzotti Contemplados | Não | Pendente | Preço confirmado em `/cartas-contempladas-de-imoveis/` |
| Bolsa do Consórcio | Não | Pendente | Filtro de categoria "imóveis" já na URL, com paginação |
| Compra Consórcios | Não | Pendente | Cotas com URL própria (`/consorcios_cotas/<id>/`) |
| Cotas Contempladas | Sinal nenhum | Pendente | Preço não confirmado na leitura estática (WP + AJAX); precisa mais inspeção |
| Consormega | Não | Pendente | Preço visível na home; listagem ainda não mapeada |
| Toco Consórcios | Não | Pendente | WordPress com admin-ajax |
| Personal Consórcios | Não | Pendente | Preço visível; listagem ainda não mapeada |
| Global Investimento | Sem sinal | Incerto | Página de "cartas contempladas" parece consultoria/lead-gen via WhatsApp, sem grade de preços confirmada |
| Carta de Crédito Contemplada | Sem sinal | Incerto | Site institucional/blog sobre administradoras; não achei vitrine com preço — provável geração de lead |
| RV Negócios | Sem sinal | Não automatizável | Landing page única, venda por WhatsApp, sem estoque estruturado |
| ConsorcioCred | **Sim** | Bloqueado | `api.consorciocred.com/offer` responde 401 sem sessão |
| MyCotas / Mycon | — | Bloqueado | Proteção anti-bot Cloudflare na primeira carga — não tentamos contornar |

## Próximos sites

Cada site da lista original precisa do mesmo processo de inspeção que a
Contemplei recebeu (nunca invente seletor/endpoint): abrir o site,
capturar as chamadas de rede reais, checar se há API JSON pública antes
de recorrer a Playwright, mapear paginação e a URL individual do anúncio.
Sites com login ou proteção anti-bot forte vão exigir mais tempo por
unidade. Rode `list-errors` depois de tentar um site novo para ver o
motivo registrado quando ele não puder ser automatizado.
