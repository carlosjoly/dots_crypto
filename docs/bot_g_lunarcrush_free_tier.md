# Bot G — viabilidade do LunarCrush no plano gratuito

## Decisão de escopo

- Provedor principal: LunarCrush.
- Orçamento: somente recursos gratuitos; nenhum plano pago, trial pago ou endpoint que exija upgrade.
- Estado: pesquisa técnica / prova de compatibilidade. Não habilitar o Bot G, não emitir alertas e não alterar o fluxo de produção.
- Integração na `main`: não autorizada.

## Resultado da verificação documental (02/11/2026)

A documentação oficial de chaves do LunarCrush informa que o plano Hobby gratuito oferece endpoints de dados de mercado, com limite de 4 requisições por minuto e 100 por dia. Endpoints sociais, de creators e de IA são descritos como recursos de planos pagos.

A documentação da API descreve o endpoint `GET /api4/public/coins/list/v1` como lista de ativos com campos como `social_volume_24h`, `sentiment`, `social_contributors` e `generated`. Entretanto, a existência desses campos na documentação geral não comprova que todos estejam liberados no plano gratuito da conta. A elegibilidade do endpoint e dos campos deve ser testada com uma conta gratuita real, sem contornar bloqueios de plano.

## Conclusão de viabilidade

Não é tecnicamente correto prometer, neste momento, que o plano gratuito fornece o histórico e as métricas sociais necessários para detectar variações de volume e sentimento. A documentação pública indica restrição de endpoints sociais no Hobby.

O Bot G deve operar em modo de pesquisa/observação, sem sinal, até que uma chamada autorizada pelo plano gratuito comprove:
1. autenticação e acesso ao endpoint permitido;
2. disponibilidade real dos campos sociais necessários;
3. timestamp de geração e atualização suficientemente recente;
4. cobertura dos ativos monitorados;
5. forma gratuita e confiável de obter comparação temporal.

HTTP 401/403, mensagem de upgrade, campo ausente, timestamp inválido ou resposta incompleta devem resultar em `N/D` e bloqueio — nunca em estimativa inventada, fallback pago ou alerta.

## Métricas e semântica

Mapeamento candidato, sujeito a confirmação por resposta real:
- `social_volume_24h`: volume de posts sociais reportado pelo provedor; não confundir com volume financeiro negociado.
- `sentiment`: percentual de posts positivos ponderado por interações, conforme definição do provedor.
- `social_contributors`: contribuidores sociais reportados; não assumir que equivalem a autores únicos em todas as janelas.
- `spam`: contagem de posts classificados como spam, se efetivamente retornada. Não converter essa contagem em percentual de bots sem denominador compatível.
- `generated`: timestamp de geração; validar idade antes de consumir.

Não presumir `bot_ratio`. Spam não é sinônimo de bots nem fornece automaticamente uma proporção. Se a API não expuser um indicador explícito e semanticamente compatível, exibir `N/D`.

## Estratégia técnica recomendada

### Etapa 1 — prova de acesso gratuita (sem alertas)
Criar um adaptador read-only para o endpoint que a conta gratuita efetivamente autorizar. A prova deve registrar somente status HTTP, nomes de campos, timestamp, cobertura dos símbolos e motivo de bloqueio; nunca registrar API key ou cabeçalho Authorization.

### Etapa 2 — histórico local
Se o plano gratuito fornecer apenas fotografia atual, avaliar uma série temporal própria com snapshots datados, persistidos de forma controlada. Não comparar períodos incompatíveis nem sinalizar variação antes de acumular amostra suficiente. A persistência deve ser testada contra execuções concorrentes, ausência de execução e dados duplicados.

### Etapa 3 — segurança e cobertura
A GoPlus é aplicável a contratos de tokens em redes EVM. BTC nativo e ETH nativo não devem ser tratados como contratos EVM. Para ativos nativos, registrar que a triagem de contrato não se aplica; para tokens, exigir chain ID e endereço verificados, com resposta GoPlus completa e fail-closed.

### Etapa 4 — validação isolada
Testes com fixtures documentadas e respostas sanitizadas; testes de 401/403, rate limit, upgrade-required, campos ausentes, dados stale, timestamps futuros, duplicatas e mudança de esquema. Nenhum teste deve enviar mensagem real ao Telegram.

### Etapa 5 — autorização separada
Somente após prova gratuita, revisão do código e testes, apresentar PR técnico para revisão. A integração na `main` e qualquer ativação de alertas exigem autorização explícita separada.

## Critérios de aceite da prova gratuita

- [ ] Chamada real autenticada com conta gratuita, sem upgrade.
- [ ] Endpoint e campos confirmados como permitidos no plano.
- [ ] Timestamp e cobertura dos ativos confirmados.
- [ ] Volume social e sentimento com definição e janela documentadas.
- [ ] Comparação temporal viável sem endpoint pago.
- [ ] Spam/bots apresentados sem equivalências indevidas.
- [ ] Nenhum segredo nos logs ou no repositório.
- [ ] Nenhum alerta de produção e nenhuma alteração na `main`.

## Fontes oficiais consultadas

- LunarCrush — API Keys & Authentication: https://lunarcrush.com/en/developers/keys
- LunarCrush — API documentation and plan gating: https://lunarcrush.com/en/developers/api
- LunarCrush — API v4 reference: https://github.com/lunarcrush/api

A disponibilidade pode variar por plano e conta; a documentação pública não substitui a prova de acesso com a conta gratuita efetivamente utilizada.
