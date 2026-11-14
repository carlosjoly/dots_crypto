# Bot F — BSC runtime LP proof

## Alert gate

A candidate reaches Telegram only after all of these checks pass, in order:

1. GeckoTerminal discovery and exact pool identity.
2. Basic market eligibility and GoPlus security status `eligible_for_watchlist`.
3. Fresh GeckoTerminal pool liquidity of at least **US$ 25,000**.
4. A read-only BSC RPC snapshot at one pinned block.
5. Exact PancakeSwap V2 pair/factory identity.
6. UNCX locker runtime hash and configured factory match.
7. Positive, active legacy six-field `tokenLocks(address,uint256)` records.
8. Actual LP token custody in the locker covers active lock records.
9. Active locked LP is at least **99.9%** of total LP supply.
10. Each active lock is linked to one unique `onDeposit(address,address,uint256,uint256,uint256)` event and a successful transaction receipt in the bounded 50,000-block history.

Discovery-provided proof metadata is overwritten by the runner's own RPC result. The runner does not accept pending GoPlus candidates.

## BSC deployment pin

- Chain ID: `56`
- PancakeSwap V2 factory: `0xca143ce32fe78f1f7019d7d551a6402fc5350c73`
- UNCX locker: `0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83`
- Runtime Keccak pin: `0x23157a45d396213e125dfaaf13e0fe3332c31e8e54e4084fb768f3920ed7addf`
- Pin observation: successful read-only diagnostic at BSC block `133556223`.

A changed runtime hash blocks verification and requires a reviewed code change. The runtime hash is a bytecode fingerprint; it is not, by itself, independent proof of UNCX ownership or endorsement. The locker address and source must continue to be checked against authoritative UNCX/BscScan references before changing this allowlist.

## ABI and event handling

The deployed BSC locker exposes a legacy six-field `tokenLocks` tuple. The legacy `onDeposit` event arguments are non-indexed. Its `user` field is the depositor and is not required to equal the lock record's stored `owner`. Event matching uses exact LP address, amount (current or initial), lock date and unlock date, then verifies the successful receipt and exact event log.

Log retrieval starts with 5,000-block chunks and halves the chunk size on recognized provider range errors, down to 100 blocks. If the bounded history cannot establish a unique event/receipt link, the proof fails closed.

## Network scope

Only BSC / PancakeSwap V2 / this pinned UNCX locker has a reviewed adapter. Other configured networks may be discovered and screened, but the runner blocks their Telegram delivery until a separate network/DEX lock adapter has its own deployment pin, tests and live validation.

## Operational safety

- Read-only JSON-RPC methods only.
- No transaction signing, transaction broadcast, order placement or trading.
- RPC URL is supplied through the GitHub Actions secret `BOT_F_RPC_URL_BSC`; endpoint values are not written to logs.
- A successful workflow or diagnostic is not an alert approval. A candidate must pass the live lock proof gate.
- The two previously investigated pools returned zero current UNCX locks; they remain blocked.

## Live validation

The workflow **Bot F - BSC UNCX adapter live validation** runs on pushes to the integration branch and can also be dispatched manually after merge. It checks the two known pools and uploads a read-only JSON artifact. Expected no-lock results are reported as `no_active_locks_confirmed`, not as approvals.


## Evidência live de reservas e lock

O workflow de validação read-only executa agora, para cada pool, primeiro `verify_v2_pool_reserves` e depois o adaptador UNCX no mesmo bloco. O JSON inclui token0/token1, factory, confirmação de `factory.getPair`, reservas, totalSupply e hash do bloco, além da identidade do locker. O artefato também inclui `data/bot_f_onchain_audit.jsonl` com os registos individuais das consultas.

`no_active_locks_confirmed` significa que a identidade do par e do locker foi validada, mas não existe lock ativo; nunca é aprovação. `active_lock_verified` é apenas resultado diagnóstico e mantém `approval=false`; o runner continua a exigir a própria prova RPC combinada e liquidez fresca antes do Telegram.
