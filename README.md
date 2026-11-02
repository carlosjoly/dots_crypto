# Dot Cripto — agentes independentes de monitorização

Sistema **apenas de alertas** para apoiar decisões manuais de swing trade. Não executa ordens, não usa alavancagem e não precisa de chaves de negociação da Binance.

## O que está incluído

- Agentes independentes para altas, quedas, reversões, rompimentos/volume e swing trade.
- Coleta de candles públicos da Binance Spot (15m, 1h e 4h).
- Filtros básicos de liquidez e volume, RSI, médias móveis e ATR.
- Alertas opcionais no Telegram.
- Integração inicial com GoPlus Token Security API para redes EVM, quando configurada.
- Workflow do GitHub Actions para execução programada.
- Registos em JSONL e tratamento de erros por agente.
- Bot F: descoberta de pools recém-criados via GeckoTerminal, triagem fail-closed de contratos EVM via GoPlus e alertas informativos de candidatos à watchlist.

## Limitações importantes

1. GitHub Actions não é um serviço de execução contínua nem garante horário exato; os agendamentos podem atrasar. O workflow executa ciclos programados.
2. Os limiares incluídos são parâmetros iniciais de engenharia, não uma estratégia validada. Faça backtest e paper trading antes de confiar nos alertas.
3. O Bot F monitora pools DEX recentes nas redes EVM configuradas (Ethereum, Base, BNB Chain, Arbitrum, Polygon, Optimism e Avalanche). A descoberta de um pool não prova que o token foi criado naquele momento; registra a criação do pool indexado pelo GeckoTerminal.\n4. O Bot F só envia candidato à watchlist se os dados de mercado superarem os mínimos e os campos obrigatórios do GoPlus estiverem completos e sem alertas críticos. Dados ausentes, API indisponível ou contrato sem cobertura resultam em revisão/rejeição, nunca em aprovação automática.\n5. GoPlus é uma triagem automatizada, não auditoria nem garantia de segurança. Não detecta todos os golpes; liquidez pode ser removida, volume pode ser manipulado e preços podem sofrer slippage extremo. O Bot F não compra nem executa ordens.
4. Os dados de mercado são recolhidos de endpoints públicos da Binance. Verifique disponibilidade/região e os termos aplicáveis.
5. Alertas e pontuações não são garantias de lucro. A pontuação não deve ser tratada como probabilidade estatística enquanto não for calibrada com dados fora da amostra.

## Instalação local (Windows)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Preencha `.env` com o token do bot do Telegram e o `chat_id` se desejar alertas. Não coloque segredos no código ou no Git.

Execute um ciclo:
```powershell
python -m app.main
```

## Configuração

Edite `config.yaml` para alterar pares, timeframes e limiares. O universo inicial é deliberadamente pequeno; adicionar centenas de pares pode atingir limites de API e gerar ruído.

Variáveis de ambiente:
- `TELEGRAM_BOT_TOKEN`: token fornecido pelo BotFather.
- `TELEGRAM_CHAT_ID`: chat de destino.
- `GOPLUS_APP_KEY` e `GOPLUS_APP_SECRET`: opcionais, se exigidos pelo plano/endpoint usado.
- `GOPLUS_ENABLED`: `true` para ativar a verificação de segurança EVM. O `config.yaml` habilita o Bot F por padrão; a triagem GoPlus continua obrigatória.

Nunca use uma API key da Binance com permissão de negociação ou levantamento neste projeto. A coleta atual usa endpoints públicos.

## GitHub Actions

1. Crie um repositório privado no GitHub e envie os ficheiros.
2. Em **Settings → Secrets and variables → Actions → New repository secret**, adicione `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`. Adicione credenciais GoPlus apenas se necessárias.
3. Confirme que o workflow `.github/workflows/monitor.yml` está ativo.
4. Use **Actions → Dot Cripto Monitor → Run workflow** para testar manualmente.
5. O cron está configurado para executar a cada 15 minutos. O GitHub pode atrasar execuções, especialmente em períodos de carga; não é garantia de monitorização em tempo real.

## Como funcionam os agentes

Cada agente executa separadamente e erros são capturados individualmente. Partilham o componente de recolha de dados, mas uma falha num agente não interrompe os seguintes. Cada sinal tem o nome do agente, par, horário UTC, timeframe, dados observados, motivo e ressalvas.

- **A — Altas expressivas:** variação positiva, volume relativo e tendência.
- **B — Quedas expressivas:** variação negativa e pressão de volume.
- **C — Reversões:** procura condições preliminares, nunca assume que um extremo vai inverter.
- **D — Rompimentos:** procura fecho além de máxima/mínima recente com volume.
- **E — Swing trade:** confluência simples de tendência, RSI, volume e ATR.
- **F — Novos lançamentos:** GeckoTerminal + GoPlus EVM; filtros mínimos configuráveis de idade do pool, liquidez, volume e transações; deduplicação persistida por cache do GitHub Actions; alertas somente para candidatos aprovados na triagem obrigatória.
- **G — Inteligência social:** integração não ativada; requer API social licenciada/configurada.
- **H — Fundamentalista:** integração não ativada; exige fontes primárias verificáveis.
- **I — Segurança/fraudes:** integração GoPlus EVM auxiliar; não deve ser a única barreira de segurança.
- **J — Tokenomics:** integração não ativada; exige dados de oferta, desbloqueios e distribuição com fontes datadas.

## Próximas etapas obrigatórias antes de habilitar sinais como estratégia

- Guardar candles históricos e validar lacunas/duplicados/velas incompletas.
- Construir backtest sem viés temporal, com taxas, spread e slippage.
- Separar amostra de desenvolvimento, validação e teste fora da amostra.
- Comparar resultados com baseline e medir drawdown, expectativa, taxa de acerto e número de operações.
- Fazer paper trading e rever falsos positivos.
- Calibrar confiança apenas depois destas etapas.

## Estrutura

```text
app/
  main.py
  config.py
  market_data.py
  indicators.py
  alerts.py
  security.py
  agents.py
  logging_setup.py
config.yaml
requirements.txt
.env.example
.github/workflows/monitor.yml
```
*******************************************************************************************************

Eu seguiria com uma auditoria em duas frentes:
1. Executar e verificar um ciclo completo na main, agora com o Bot G integrado, conferindo coleta, alertas, histórico e Telegram.
2. Corrigir o Bot F e realizar testes específicos por rede, sem modificar a lógica dos Bots A–E.
Depois disso, teremos uma fotografia operacional atualizada de A a G, e poderemos avançar para H, I e J com critérios claros.

1. O que considero prioritário
Minha avaliação técnica estabelece esta ordem:
Prioridade	Trabalho	Motivo
1	Validar Bot G na execução normal da main	Confirmar operação após integração
2	Corrigir e testar Bot F	Resolver HTTP 404 e HTTP 429
3	Consolidar A–E	Backtest, custos, slippage e qualidade dos sinais
4	Construir Bot H	Inteligência fundamentalista com fontes primárias
5	Construir Bot J	Dados de oferta, distribuição e desbloqueios
6	Definir Bot I	Estabelecer sua função antes de desenvolver

Minha recomendação: não ativar H e J prematuramente e não criar um Bot I apenas para preencher a sequência. Primeiro precisamos tornar os bots já existentes confiáveis.
5. Próxima etapa operacional
