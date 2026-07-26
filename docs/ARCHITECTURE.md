# Architecture

```text
Market worker -> deterministic features -> strategy worker
                                      -> Azure GPT-5.4 Mini
                                         \-> OpenAI Mini fallback
                                      -> OpenAI SOL review (<= 5%)
                                      -> Risk Engine
                                      -> Order validation
                                      -> Saxo pre-check
                                      -> entry + broker-native stop/take-profit
                                      -> position reconciliation
```

API, workers, broker og AI er adapters omkring det deterministiske domain-lag.
Ingen adapter må omgå `RiskEngine`.

Worker-køer indeholder kun validerede datajobs. Felter som `command`, `code`,
`url`, `token` og `secret` afvises. Execution-workerens kø er ikke et offentligt
ordreinterface; ordreoprettelse sker gennem den autentificerede application
service med idempotency og audit.

PostgreSQL er system of record. Redis bruges kun til rate limiting og korte
jobkøer. Brokerens egne protective orders er system of record for beskyttelse
af åbne LIVE-positioner.
