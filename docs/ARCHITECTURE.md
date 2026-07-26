# Architecture

```text
Scheduler -> Market worker -> Saxo candles -> PostgreSQL
                              |
                              v
                    deterministic features
                              |
                              v
Strategy worker -> Azure GPT-5.4 Mini -> OpenAI Mini fallback
                              |
                              +-> OpenAI SOL review (<= 5%)
                              |
                              v
                      persisted signal_id
                              |
                              v
Execution worker/API -> fresh Saxo quote + actual portfolio
                              |
                              v
               deterministic Risk Engine + validation
                              |
                              v
              Saxo pre-check + entry + stop/take-profit
                              |
                              v
Position worker -> reconciliation -> trailing stop state machine
```

API, workers, broker og AI er adapters omkring det deterministiske domain-lag.
Ingen adapter må omgå `RiskEngine`.

Worker-køer indeholder kun validerede datajobs. Felter som `command`, `code`,
`url`, `token` og `secret` afvises. Execution-workerens kø er ikke et offentligt
ordreinterface; ordreoprettelse sker gennem den autentificerede application
service med idempotency og audit.

PostgreSQL er system of record for signaler, idempotency, managed positions,
AI-budget og audit. Redis bruges kun til rate limiting, deduplikerede jobkøer
og dead-letter-kø. Brokerens egne protective orders er system of record for
beskyttelse af åbne LIVE-positioner.

## Tillidsgrænser

- API-klienten må ikke levere balance, markedspris, stop eller target til
  execution.
- AI-output kan kun klassificere et setup og kan ikke indeholde quantity eller
  ordreparametre.
- Execution accepterer kun friske signaler, som allerede er gemt i databasen.
- LIVE kræver autoritativ earnings-status; ukendt status afvises.
- En entry uden to accepterede protective order-id'er markeres til
  reconciliation og betragtes ikke som en normal succes.

## Trailing stop

Position-workerens state machine gemmer high-watermark. Når den konfigurerede
profitgrænse nås, ændres den eksisterende Saxo-stopordre først til trailing
stop. Først efter en accepteret ændring annulleres den faste take-profit. Hvis
andet kald fejler, er positionen stadig beskyttet af stoppet.
