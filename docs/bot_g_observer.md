# Bot G — observador social com triagem GoPlus

## Estado operacional desta etapa

- Coleta horária read-only pelo endpoint LunarCrush Hobby já testado.
- Ativos cobertos: BTC, ETH, BNB e LINK, os quatro símbolos efetivamente observados no teste autenticado.
- Uma chamada bulk por hora (máximo teórico de 24 chamadas/dia), abaixo do limite documentado de 100/dia para Hobby.
- Histórico local versionado pelo cache do GitHub Actions, com retenção de sete dias.
- Sem alertas Telegram, sem sinais, sem execução de ordens e sem alteração dos Bots A–F.
- A métrica social não possui timestamp de atualização verificado no endpoint observado. `collected_at` é somente o horário da nossa coleta.
- `social_contributors`, `spam` e `bot_ratio_pct` permanecem N/D. Não inferir spam ou bots a partir de outros campos.

## GoPlus

- LINK é consultado somente na rede Ethereum, chain ID 1, usando o contrato publicado na documentação oficial Chainlink.
- BTC, ETH e BNB nativos não são contratos EVM e não são enviados à GoPlus.
- GoPlus é consultado no máximo uma vez a cada 24 horas após resposta válida. Erros deixam o status em `review_required` e permitem nova tentativa na próxima coleta horária.
- `eligible_for_watchlist` significa apenas que os campos obrigatórios retornados não dispararam os bloqueios configurados; não certifica segurança ou legitimidade.
- Falta de campos obrigatórios permanece `review_required`; risco desconhecido nunca é tratado como aprovado.

## Critérios antes de qualquer alerta social

1. Acumular histórico suficiente e validar continuidade, duplicatas e retenção.
2. Distinguir variação entre nossas coletas de atualização real da fonte.
3. Validar a semântica do campo `sentiment` e a janela de cada métrica na documentação do endpoint.
4. Definir baseline/limiares com teste retrospectivo; não converter volume/sentimento em probabilidade.
5. Só então submeter a camada de alertas a revisão separada.

## Segredos

O workflow consome `SOCIAL_API_KEY` como secret. A chave nunca é escrita em arquivo ou log.
