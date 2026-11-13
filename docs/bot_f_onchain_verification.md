# Bot F — verificador on-chain independente

`app/onchain_lp.py` usa JSON-RPC read-only para verificar chainId, bytecode, factory(), token0()/token1(), factory.getPair(), getReserves() e totalSupply() no mesmo bloco identificado por número/hash. Regista evidência em `data/bot_f_onchain_audit.jsonl`.

Só aceita pares V2-compatible com factory explicitamente allowlisted. Não há descoberta automática de factories.

## Limite de segurança
Snapshot de reservas **não é** prova de LP bloqueada. Sem adaptador específico da rede/DEX, o estado é `reserve_verified_lock_unverified` e não satisfaz o gate Telegram. O adaptador futuro deverá verificar ABI/contrato de lock, registro/evento, transação/recibo, saldo LP retido e timestamp de desbloqueio via RPC. Holder genérico não basta.

RPCs: `BOT_F_RPC_URL_ETH`, `BOT_F_RPC_URL_BSC`, `BOT_F_RPC_URL_BASE`, `BOT_F_RPC_URL_ARBITRUM`, `BOT_F_RPC_URL_POLYGON_POS`, `BOT_F_RPC_URL_OPTIMISM`, `BOT_F_RPC_URL_AVALANCHE`. Nenhuma factory é confiada por padrão. Testes simulam RPC; não certificam endpoints ou lockers reais.
