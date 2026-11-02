# Bot G — adaptador LunarCrush Hobby (coleta isolada)

## O que foi comprovado

Em 02/11/2026, uma chamada autenticada ao endpoint `/api4/public/coins/list/v1` retornou HTTP 200 e incluiu `social_volume_24h` e `sentiment` para BTC, ETH, BNB e LINK.

## O que este módulo faz

- Uma única chamada bulk para obter a lista de ativos.
- Normaliza apenas `social_volume_24h` e `sentiment`, campos confirmados na resposta real do plano gratuito testado.
- Registra `collected_at` (horário em que nosso coletor recebeu a resposta).
- Mantém `source_updated_at` como N/D, pois não foi encontrado timestamp específico de atualização das métricas sociais.
- Mantém contribuidores, spam e proporção de bots como N/D.
- Classifica 401/403, 429 e outros erros sem registrar corpo da resposta, chave ou cabeçalho.

## Limites deliberados

- Não calcula sinais ou confiança.
- Não compara períodos sem histórico validado.
- Não confunde `last_updated_price` com atualização de dados sociais.
- Não consulta GoPlus: a triagem GoPlus pertence à camada posterior de avaliação de contratos EVM; BTC/ETH/BNB nativos não são contratos EVM.
- Não persiste snapshots, não altera `app.main`, não chama Telegram e não ativa Bot G.

## Próximas validações

1. Confirmar a semântica e janela do campo `sentiment` na documentação de endpoint correspondente.
2. Projetar histórico local com deduplicação e retenção antes de calcular variações.
3. Definir limiar e baseline somente após acumular amostras suficientes.
4. Verificar freshness social por fonte documentada; sem timestamp, manter observação sem sinal.
5. Implementar GoPlus somente para ativos com contrato EVM e chain ID verificados.
6. Submeter integração operacional a revisão separada; produção continua desativada.
