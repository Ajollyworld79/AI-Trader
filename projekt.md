AI Trader – Projektbeskrivelse

Du er lead software engineer og AI architect.

Du skal designe og implementere et komplet AI-baseret trading-system i Python.

Målet er IKKE at bygge en "AI der gætter aktiekurser".

Målet er at bygge en professionel trading-platform hvor AI bruges intelligent sammen med klassisk software engineering, kvantitative analyser og risikostyring.

Projektet skal kunne vokse over tid og være bygget efter clean architecture.

Primære mål

Systemet skal:

handle aktier via Saxo OpenAPI

senere kunne udvides til Forex

hostes på Railway

skrives i Python

bruge FastAPI

bruge PostgreSQL

bruge SQLAlchemy

bruge Alembic

bruge Pydantic

bruge OpenAI SDK

understøtte Azure OpenAI

være modulært

være stærkt testbart

være cloud native

Projektet skal være enterprise-grade.

Vigtige designprincipper

LLM må ALDRIG være ansvarlig for:

beregning af RSI

MACD

EMA

ATR

Bollinger Bands

position sizing

risk management

stop loss

take profit

trailing stop

portfolio limits

maksimum drawdown

eksponering

Alt dette implementeres deterministisk i Python.

LLM bruges KUN til:

markedsanalyse

klassifikation

nyhedsanalyse

sentiment

vurdering af setups

forklaring af beslutninger

identifikation af modstridende signaler

vurdering af markedsregimer

AI Arkitektur

Systemet skal anvende flere lag.

Lag 1

Python scanner markedet.

Python beregner:

EMA

SMA

RSI

ATR

MACD

ADX

Bollinger

Volume Profile

Relative Strength

Volatilitet

Support

Resistance

Ingen AI anvendes.

Kun interessante aktier går videre.

Lag 2

Azure OpenAI GPT-5.4 Mini analyserer kandidater.

Mini skal returnere struktureret JSON.

Eksempel:

{"decision":"buy","confidence":0.91,"reason":"Trend continuation after pullback","needs_sol_review","risk_flags":[]}

Mini må ALDRIG returnere:

position size

antal aktier

stop loss

take profit

Lag 3

Kun hvis nødvendigt anvendes GPT SOL.

Eksempler:

earnings

store nyheder

modstridende signaler

lav confidence

meget stor position

ekstrem volatilitet

Målet er at mindre end 5% af AI-kaldene bruger SOL.

Budget

Maks AI budget:

1000 DKK pr måned.

Azure GPT Mini skal anvendes mest muligt.

Python skal udføre alt muligt uden AI.

Tokenforbrug skal logges.

Dashboard skal vise:

månedligt AI forbrug

dagligt AI forbrug

antal Mini kald

antal SOL kald

gennemsnitlig pris pr handel

Broker

Saxo OpenAPI

Understøt:

login

token refresh

account info

portfolio

positions

orders

market orders

limit orders

stop orders

OCO

trailing stop

account balance

Systemet skal være klar til SIM og LIVE.

Order Flow

Ny handel

↓

Risk Engine

↓

Order Validation

↓

Saxo

↓

Position Monitor

↓

Trade Manager

Risk Engine

Implementer:

maks risiko pr handel

0.5%

maks åbne positioner

10

maks sektor eksponering

20%

maks dagligt tab

2%

maks samlet drawdown

10%

ingen gearing

ingen short i version 1

ingen handel hvis spread er for stort

ingen handel tæt på earnings

ingen handel ved datakvalitetsfejl

Position Manager

Ved køb oprettes automatisk:

Entry

Stop Loss

Take Profit

Hvis profit overstiger en konfigurerbar grænse:

Take Profit annulleres.

Trailing Stop aktiveres.

Hvis Railway går ned skal Saxo stadig beskytte positionen.

Portfolio Manager

Skal kende:

kontant beholdning

eksponering

sektor eksponering

land

valuta

korrelation

værdi

urealiseret gevinst

realiseret gevinst

Sharpe

Sortino

Drawdown

Win Rate

Average Winner

Average Loser

Expectancy

Database

Gem ALT.

Trades

Signals

AI prompts

AI svar

Candles

Features

Orders

Portfolio snapshots

Performance

Nyheder

Backtests

Logs

Dashboard

FastAPI

Frontend senere.

REST API først.

Vis:

åbne handler

historik

AI forbrug

performance

P/L

drawdown

budget

portfolio

risiko

Logging

Alle AI svar logges.

Alle prompts logges.

Alle handler logges.

Alle fejl logges.

Alle API kald logges.

Alt skal kunne reproduceres.

Backtesting

Byg komplet backtest motor.

Må aldrig anvende fremtidige data.

Understøt:

commission

spread

slippage

dividends

splits

walk forward

out of sample

paper trading

Railway

Projektet skal opdeles i services.

API

Market Worker

Strategy Worker

Execution Worker

Position Worker

News Worker

Scheduler

Database

Redis

Kvalitet

Brug:

type hints

pytest

ruff

black

mypy

CI/CD

Docker

GitHub Actions

Environment Variables

Secrets

Projektstruktur

Brug Domain Driven Design.

Eksempel:

app/

domain/

services/

repositories/

models/

api/

workers/

ai/

broker/

risk/

strategy/

portfolio/

news/

backtesting/

config/

tests/

AI Filosofi

Den vigtigste regel:

Python beregner.

AI vurderer.

AI må aldrig være eneste beslutningstager.

Systemet skal kunne køre helt uden SOL.

SOL skal kun bruges når Mini vurderer at sagen er usikker eller kompleks.

Målet er et professionelt enterprise trading-system som over flere år kan udbygges med flere strategier, flere markeder og flere AI-modeller.