# Bot F — adaptador UNCX V2 / PancakeSwap V2 / BNB Smart Chain

## Escopo fechado
Este adaptador cobre apenas pares V2-compatible da PancakeSwap V2 na BNB Smart Chain:
- chainId 56;
- factory PancakeSwap V2: `0xca143ce32fe78f1f7019d7d551a6402fc5350c73`;
- locker UNCX listado para PancakeSwap V2: `0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83`.

A documentação oficial da UNCX lista o endereço, mas declara que os endereços publicados ainda não foram verificados independentemente e exige confirmação com a UNCX antes da integração. O adaptador, por isso, exige `BOT_F_UNCX_BSC_LOCKER_CODE_HASH` definido por operador após validação independente. Não há hash padrão. O pin não deve ser preenchido com hash de fixture/teste ou obtido apenas de fonte não autenticada.

## Verificações implementadas (RPC somente leitura)
- chainId 56 e bytecode presente no endereço do locker;
- hash Keccak do runtime bytecode comparado ao pin do operador;
- factory() do par e uniswapFactory() do locker iguais à factory PancakeSwap V2;
- contagem e conteúdo dos registros de lock;
- somente locks com amount positivo e unlock futuro;
- saldo LP real do locker cobrindo a soma dos locks ativos;
- fração LP bloqueada >= 99,9% do totalSupply;
- leituras de lock ancoradas no mesmo bloco/hash do snapshot de reservas;
- busca de eventos `onDeposit(address,address,uint256,uint256,uint256)`, correspondência com LP/owner/amount inicial/lockDate e validação do recibo de transação, status, bloco e log;
- liquidez fresca >= US$ 25.000 para o comprovante combinado.

A busca histórica cobre no máximo 50.000 blocos, em janelas de 5.000. RPC que não aceite `web3_sha3`, `eth_getLogs` ou consultas necessárias resulta em bloqueio. Locks cujo evento não esteja no intervalo, seja ambíguo ou não corresponda exatamente também são bloqueados.

## Limitações que impedem ativação
1. **Deployment ainda não autenticado:** a UNCX pede confirmação independente do endereço. O hash de runtime real ainda não foi fixado nem validado em RPC BSC real.
2. **Compatibilidade ABI pendente:** a implementação usa a interface/evento legado `onDeposit`. A documentação UNCX também publica interface 2.1, com eventos e estrutura diferentes. Ainda não foi demonstrado por leitura real do bytecode/ABI que o deployment BSC listado corresponde exatamente à interface consumida pelo adaptador.
3. **Correspondência do proprietário conservadora:** o evento legado descreve `user` como usuário que realiza o depósito, enquanto o registro armazena `owner` como titular que poderá retirar. A implementação exige igualdade entre ambos. Isso pode bloquear locks legítimos em que depositante e titular sejam diferentes; não flexibilizar sem uma regra de associação autenticada e testes de casos reais.
4. **Janela histórica:** locks anteriores aos 50.000 blocos consultados falham fechado.
5. **Sem integração:** o adaptador não é chamado pelo runner Bot F e não pode, sozinho, autorizar Telegram.
6. **Sem teste operacional:** os testes de CI usam RPC simulado. Ainda não houve validação em RPC BSC real nem contra pools reais.

## Testes automatizados
A suíte cobre sucesso simulado e bloqueio para hash ausente/incorreto, chainId errado, factory errada, lock expirado, cobertura LP insuficiente, evento ausente, recibo malsucedido, ausência de liquidez fresca e leituras em bloco fixo. Isso comprova comportamento sob fixtures, não a autenticidade do deployment ou compatibilidade real da ABI.

## Segurança e ativação
Read-only: sem assinatura, transações, ordens ou execução de trades. Nenhum alerta é habilitado por este adaptador. Não integrar ao runner nem permitir Telegram até concluir: confirmação direta do deployment com UNCX, hash runtime obtido e reproduzido em RPC BSC confiável, ABI real confirmada, testes contra pools reais e revisão independente do resultado.
