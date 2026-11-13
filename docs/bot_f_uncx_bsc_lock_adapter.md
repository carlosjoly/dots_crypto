# Bot F — adaptador UNCX V2 / PancakeSwap V2 / BNB Smart Chain

## Escopo fechado
Este adaptador cobre apenas pares Uniswap-V2-compatible da PancakeSwap V2 na BNB Smart Chain:
- chainId 56;
- factory PancakeSwap V2: `0xca143ce32fe78f1f7019d7d551a6402fc5350c73`;
- locker UNCX V2 publicado para PancakeSwap V2: `0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83`.

A documentação de lockers UNCX descreve `getNumLocksForToken`, `tokenLocks`, os campos do registro e os eventos. O endereço BSC é listado para PancakeSwap V2. A própria documentação de contratos avisa, porém, que endereços publicados ainda precisam de verificação independente. Por isso, o adaptador exige `BOT_F_UNCX_BSC_LOCKER_CODE_HASH` com o hash de runtime previamente revisto. Sem o pin, falha fechado.

## O que é verificado via RPC
- chainId 56;
- runtime bytecode do locker e hash Keccak comparado ao pin;
- factory() do pool e uniswapFactory() do locker, ambos iguais à factory PancakeSwap V2;
- contagem e conteúdo de cada registro `tokenLocks(pool,index)`;
- somente registros com amount > 0 e unlockDate futuro;
- saldo real do LP token no locker, que deve cobrir a soma dos registros ativos;
- fração bloqueada sobre totalSupply do LP >= 99,9%;
- todas as leituras de locker no mesmo bloco/hash usado no snapshot de reservas.

Os selectors são derivados com `web3_sha3` do próprio nó RPC; se esse método não estiver disponível, a verificação falha fechado.

## Limite deliberado
O adaptador confirma armazenamento do registro e custódia LP, mas ainda não associa cada registro ao hash da transação original de lock. O contrato publica evento `onDeposit`, mas este primeiro corte não percorre o histórico de logs para estabelecer essa associação. Assim, o resultado é `lock_verified_storage_only` e o comprovante combinado permanece `reserve_verified_lock_unverified`; nunca retorna `status=verified` e não satisfaz o gate Telegram atual.

Também não está ligado ao runner Bot F. Nenhuma rede/DEX além desta combinação é autorizada. Os testes usam RPC simulado; antes de integração operacional, é necessária validação em RPC BSC real, revisão do hash de runtime e implementação/teste de vínculo transacional.

## Status de entrega
Este adaptador não é chamado pelo runner e não deve ser usado para construir um comprovante elegível ao Telegram até existir vínculo verificável com a transação de lock e validação operacional do deployment.


## Transação de lock
Para cada registro ativo, o adaptador localiza `onDeposit(address,address,uint256,uint256,uint256)` emitido pelo endereço do locker, confere LP, owner, amount inicial e lockDate contra `tokenLocks`, e valida o recibo da transação no mesmo bloco do log. Não aceita apenas um hash fornecido por API externa.
