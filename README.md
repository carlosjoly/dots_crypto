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
- Agentes de descoberta de lançamentos, inteligência social, fundamentalista, tokenomics e on-chain definidos como módulos de integração futura: **não emitem avaliações positivas sem fontes configuradas**.

## Limitações importantes

1. GitHub Actions não é um serviço de execução contínua nem garante horário exato; os agendamentos podem atrasar. O workflow executa ciclos programados.
2. Os limiares incluídos são parâmetros iniciais de engenharia, não uma estratégia validada. Faça backtest e paper trading antes de confiar nos alertas.
3. O exemplo de GoPlus é uma verificação complementar de token EVM. Não substitui auditoria, análise de liquidez, nem cobre automaticamente todas as redes. A integração Solana deve ser adicionada conforme o endpoint e esquema atual da API.
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
- `GOPLUS_ENABLED`: `true` para ativar a verificação de segurança EVM.

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
- **F — Novos lançamentos:** integração não ativada; requer fontes/listagens e validação por rede.
- **G — Inteligência social:** integração não ativada; requer API social licenciada/configurada.
- **H — Fundamentalista:** integração não ativada; exige fontes primárias verificáveis.
- **I — Segurança/fraudes:** integração GoPlus EVM opcional; não deve ser a única barreira de segurança.
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
