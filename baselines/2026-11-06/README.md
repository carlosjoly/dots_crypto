# Baseline imutável — 2026-11-06

Esta referência fixa o código e os resultados anteriores à separação Spot/short e à reformulação do Bot E/15m.

- Repositório: carlosjoly/dots_crypto
- Commit de código: d963be314893cb02222894a37e5826e667669f57
- Branch de snapshot: baseline/2026-11-06-pre-spot-split
- Workflow: Dot Cripto Historical Backtest
- Run: 42303674240
- Artifact ID: 13545262408
- SHA-256 do ZIP descarregado: 6c2dad1861739794761887e0da5d14a71e6962ed0b9660d11524b9e6d47c85f1
- Parâmetros: 180 dias; comissão 0,10% por lado; slippage 0,05% por lado.
- Divisão: treino 60%; validação 20%; teste 20%.
- Dados: cinco pares USDT (BTC, ETH, SOL, BNB, LINK), 15m/1h/4h.
- Capital inicial: 10.000; risco por operação: 1%; exposição máxima: 80%; máximo de posições: 5.
- Stops: 1,5 ATR; reward/risk: 1,5.
- Gate original: todas as combinações, incluindo sinais short sintéticos, precisavam de trades e retorno positivo no teste.

## Resultados baseline — teste 15m

| Bot | Trades | Retorno líquido | Drawdown máximo | Profit factor | Win rate |
|---|---:|---:|---:|---:|---:|
| A | 9 | -0,65849% | -0,92365% | 0,4788 | 33,333% |
| B | 18 | -2,50265% | -2,84067% | 0,2560 | 22,222% |
| C | 93 | -5,29164% | -5,29164% | 0,3846 | 31,183% |
| D | 427 | -12,08891% | -12,31994% | 0,5631 | 41,920% |
| E | 835 | -29,60896% | -29,60896% | 0,4143 | 35,928% |

O artefacto original permanece associado ao run 42303674240 (retenção GitHub de 30 dias). O branch baseline/2026-11-06-pre-spot-split aponta diretamente para o commit original e não deve receber commits.
