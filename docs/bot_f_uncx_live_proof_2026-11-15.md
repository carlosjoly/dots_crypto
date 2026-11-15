# Bot F — live UNCX proof validation (2026-11-15)

## Result

**Status: VALIDATED — READ-ONLY**

The live workflow run [#43713326021](https://github.com/carlosjoly/dots_crypto/actions/runs/43713326021) completed successfully. Its artifact is [bot-f-uncx-live-validation-43713326021](https://github.com/carlosjoly/dots_crypto/actions/runs/43713326021/artifacts/14160878133).

The diagnostic returned `status=verified` and `valid=true` for a real BNB Smart Chain PancakeSwap V2 pair. It did not send Telegram, approve a discovery candidate, sign a transaction, or submit any transaction.

## Exact pool and contract identity

- Network: BNB Smart Chain, chain ID `56`
- DEX: PancakeSwap V2
- Pool / LP token: `0x0055731579f3ee9be56ae6077c01256716c53692`
- PancakeSwap V2 factory: `0xca143ce32fe78f1f7019d7d551a6402fc5350c73`
- UNCX locker: `0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83`
- Verified locker runtime code hash: `0x23157a45d396213e125dfaaf13e0fe3332c31e8e54e4084fb768f3920ed7addf`
- Reserve snapshot block: `133734643`
- Reserve snapshot block hash: `0xce2a5ecfb96938554c1b4b8dd699a5b76ad825ae4c6cb8432117c696455e0b94`

The pair identity was independently confirmed by reading token0/token1, the pair's factory and `factory.getPair(token0, token1)`; the factory returned the exact pool address.

## Positive UNCX lock evidence

- Active UNCX lock records: `1`
- Lock ID: `0`
- Active LP amount: `588238306301111267572030` (raw units)
- LP total supply: `588244428942385529765686` (raw units)
- LP balance held by UNCX: `588238306301111267572030` (raw units)
- Protected fraction: `0.9999895916714668` (99.998959%)
- Lock timestamp: 2025-08-21 16:48:03 UTC
- Unlock timestamp: 2027-08-22 01:00:00 UTC
- Event block: `58395610`
- Event transaction: `0x7d42a12b6f7c668c9d3bac11bcd05fc96787016494a4a2975e151aebb2f241ca`

The verifier matched the on-chain `tokenLocks` record to the exact `onDeposit` event, then checked the transaction receipt was successful, that the receipt contained the matching locker event, and that the receipt block matched the event block.

## Fresh market data

GeckoTerminal returned the exact same pool address with fresh liquidity of **US$1,861,760.55**, above the configured US$25,000 minimum. The proof output includes this liquidity value and the same pool identity.

## Interpretation and limits

This validates the positive UNCX proof path for one real PancakeSwap V2 pool, including storage, LP custody, locker runtime hash, event and receipt linkage, and fresh liquidity.

It does **not** validate GoPlus approval for a new-launch candidate, nor does it make this existing pool an alert candidate. Production alerts still require the independent market/GoPlus gates, exact-pool fresh liquidity, this proof, and the runner's final validation. The Bot F remains alert-only; no trading execution is part of this validation.
