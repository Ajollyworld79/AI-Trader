# Security policy

## Design og trusselsmodel

Systemet antager, at internettrafik, markedstekst, nyheder, worker-jobs og AI-
output kan være ondsindet. Sikkerhedsgrænsen ligger derfor i Python-koden, ikke
i prompts eller modellen.

De vigtigste trusler og kontroller er:

| Trussel | Kontrol |
|---|---|
| Stjålet API-nøgle | Kun SHA-256-digests gemmes server-side, constant-time sammenligning, rate limit og separat admin-nøgle |
| Prompt injection | Nyheder behandles som ubetroet data, længdebegrænses, ingen tools, strikt output-schema |
| AI afgiver risikodata | Output-schema indeholder ikke quantity/stop/take-profit; risk engine genberegner alt |
| Uautoriseret LIVE-ordre | API + admin + LIVE confirmation + konfigurationsgate + kill switch + Saxo auth |
| Dobbeltordre efter timeout | Database-idempotency, Saxo `X-Request-ID`, ingen automatisk retry af ordre-POST |
| Railway-nedbrud efter entry | Stop loss og take profit oprettes hos Saxo i samme ordreoperation |
| SSRF | Eksterne base-URL'er valideres mod faste HTTPS-hostnames; job-URL'er afvises |
| SQL injection | SQLAlchemy ORM og bind-parametre |
| Secret leakage | Pydantic `SecretStr`, struktureret redaction, ingen tokens i responses eller Git |
| Stjålet databasebackup | Saxo access- og refresh-tokens gemmes Fernet-krypteret med en separat Railway secret |
| Samtidig tokenrotation | En kort database-lease serialiserer refresh på tværs af Railway-workers |
| Manipuleret ordreinput | Execution accepterer kun et gemt signal-id; quote, portefølje, stop, target og quantity fastlægges server-side |
| Ubeskyttet trailing-skift | Eksisterende stop ændres før den faste take-profit annulleres |
| DoS | Request-size-grænse, schema limits, Redis-rate-limit, DB/HTTP timeouts |
| Supply chain | Pinned runtime image, CI med `pip-audit`, Bandit og minimal non-root container |

## LIVE-handel

LIVE må først aktiveres efter:

1. Saxo SIM er kørt end-to-end med rigtige instrumenter og market hours.
2. Alle pre-trade disclaimers er håndteret manuelt.
3. Reconciliation er testet ved timeout og genstart.
4. `TRADING_MODE=live`, `BROKER_BACKEND=saxo`,
   `LIVE_TRADING_ENABLED=true`, `TRADING_KILL_SWITCH=false` er gennemgået.
5. `LIVE_CONFIRMATION_TOKEN_HASH` er en separat nøgle fra API/admin.
6. Railway access, GitHub branch protection og secret rotation er aktiveret.
7. Position-workerens reconciliation- og trailing-forløb er verificeret mod
   Saxo SIM, inklusive genstart mellem de to ordreændringer.

Aktivér aldrig LIVE blot for at teste connectivity.

## Secret rotation

- Rotér API-, admin- og LIVE-nøgler hver 90. dag eller straks ved mistanke.
- Rotér Azure/OpenAI/Saxo-credentials i deres respektive portaler.
- Opdatér Railway variables uden at skrive værdier i shell-historik.
- Genstart services og verificér, at gamle nøgler afvises.

## Rapportering

Opret ikke en offentlig GitHub issue med sårbarhedsdetaljer eller credentials.
Brug GitHub Security Advisories på repository'et og vedlæg request-id,
påvirket endpoint, reproduktion og forventet impact uden rigtige tokens.
