# LIVE readiness

LIVE er en kontrolleret driftsændring, ikke en almindelig deployment. Koden er
bygget fail-closed, men rigtige handler må først aktiveres efter nedenstående
accepttest.

## 1. Eksterne forudsætninger

- Saxo LIVE-app og OAuth redirect er godkendt.
- Kontoens `AccountKey` og hvert instruments UIC er verificeret i Saxo.
- Relevante markedsdataabonnementer er aktive.
- Railway Postgres og Redis har backup/retention og adgangskontrol.
- Azure/OpenAI-priser i DKK er opdateret og budgetalarmer er sat.
- Alpha Vantage-nøglen virker, earnings-kalenderen er opdateret, og
  benchmarkets Saxo-UIC er verificeret.

## 2. SIM-accepttest

- Kør `alembic upgrade head` mod SIM-databasen.
- Gennemfør OAuth-login, genstart alle services og bekræft, at token refresh
  stadig virker.
- Kør market -> strategy -> execution -> position end-to-end.
- Bekræft i Saxo, at entry, stop og take-profit er relaterede broker-ordrer.
- Afbryd Railway efter entry og bekræft, at protective orders fortsat ligger
  hos Saxo.
- Aktivér trailing-grænsen og bekræft, at stop ændres før take-profit
  annulleres.
- Fremprovokér et uklart ordre-timeout og bekræft, at jobbet går til
  reconciliation/dead-letter uden automatisk ny entry.
- Bekræft, at et gammelt signal, forsinkede data, ukendt earnings-status samt
  manglende eller forældet markedsregime afvises.

## 3. Paper/shadow periode

- Kør mindst 3–6 måneder og mindst 100–200 signaler på urørte data.
- Sammenlign regelmotor, Mini-filter og Mini+SOL med samme omkostninger.
- Medregn kurtage, spread, slippage, valuta, udbytter og splits.
- Kræv stabile resultater på tværs af perioder; et flot samlet afkast er ikke
  nok.
- Gennemgå ugentligt dead-letter-kø, reconciliation-fejl, AI-budget og
  ubeskyttede positioner.

## 4. LIVE-gate

Følgende skal ændres samlet og gennemgås af ejeren:

```text
TRADING_MODE=live
BROKER_BACKEND=saxo
LIVE_TRADING_ENABLED=true
TRADING_KILL_SWITCH=false
AUTO_EXECUTE_SIGNALS=false
```

Start med manuel API-godkendelse og en særskilt bot-kapitalgrænse. Aktivér ikke
automatisk execution i første LIVE-fase. Brug ingen gearing eller shorting.

## 5. Første kapitaltrin

- Begræns botkapitalen til et lille beløb, uanset Saxo-kontoens saldo.
- Hold risiko pr. handel på højst den konfigurerede 0,5 %.
- Sæt daglig tabsgrænse og kill switch lavere de første uger.
- Opskalér kun efter fejlfrie handler og en dokumenteret månedlig gennemgang.

## 6. Nødprocedure

1. Sæt `TRADING_KILL_SWITCH=true`.
2. Stop execution- og strategy-workers; lad position-worker køre, hvis dens
   brokerstatus er sund.
3. Kontrollér alle positioner og protective orders direkte i Saxo.
4. Luk eller beskyt manuelt enhver position, der ikke matcher databasen.
5. Gem audit/dead-letter-data, rotér kompromitterede secrets og lav
   hændelsesrapport før genstart.

## Ikke automatiseret endnu

Den tekniske platform kan ikke selv levere eksterne forudsætninger. Saxo
credentials/app-godkendelse, markedsdataabonnement, Alpha Vantage-nøgle,
verificeret benchmark-UIC, Railway-secrets og den lange paper-validering skal
udføres i de rigtige miljøer. Uden dem skal systemet forblive i SIM med kill
switch aktiv.
