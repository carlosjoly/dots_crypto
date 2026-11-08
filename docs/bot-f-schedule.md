# Agendamento independente do Bot F

## Arquitetura

- `Dot Cripto Monitor`: permanece a cada 15 minutos para os Bots A–E e o observador G.
- `Dot Cripto Bot F Discovery`: workflow separado, acionado por `workflow_dispatch`, para descoberta de pools e triagem GoPlus.
- O Bot F não roda mais dentro do ciclo principal. Isso evita que o intervalo de 15 minutos limite a descoberta e permite uma cadência própria.
- O workflow do Bot F usa um grupo de concorrência próprio, timeout de 8 minutos e cache próprio para deduplicação e telemetria.
- Não existe cron nativo no workflow do Bot F. O disparo recorrente deve ser feito por cron-job.org, evitando dois agendadores concorrentes.

## Configuração do cron-job.org

Criar um job independente com:

- Título: `dots_crypto - Bot F - cada 5 minutos`
- Agenda: a cada 5 minutos.
- Método: `POST`
- URL: `https://api.github.com/repos/carlosjoly/dots_crypto/actions/workflows/BOT_F_WORKFLOW_ID/dispatches`
- Headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer SEU_FINE_GRAINED_TOKEN`
  - `X-GitHub-Api-Version: 2026-03-10`
  - `Content-Type: application/json`
- Body JSON: `{"ref":"main"}`

O token precisa de permissão **Actions: Read and write** no repositório. Não colocar o token no corpo, URL ou logs. A validade do token deve ser monitorada e renovada antes da expiração.

## Regras de segurança operacional

1. Manter o workflow principal em 15 minutos.
2. Não adicionar um cron nativo ao workflow do Bot F enquanto cron-job.org estiver ativo.
3. Não criar outro job que dispare o workflow principal em paralelo com o cron nativo atual.
4. A concorrência do Bot F é serializada pelo GitHub Actions; se uma execução demorar mais que 5 minutos, a próxima aguarda, não cancela a anterior.
5. Verificar a duração real por pelo menos 24 horas antes de reduzir o intervalo.
6. O Bot F permanece apenas informativo; não executa ordens.
