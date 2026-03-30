# Startup Idea Evaluator — Build Context

## What Is This
A daily tool that scrapes startup/product launches, presents 5 ideas each morning, forces the user to write structured evaluations, then reveals an LLM's own evaluation for comparison. The core value is calibrating startup judgment over time.

## Architecture Overview

### Pipeline (runs daily via cron)
1. **Scrape sources** — Hacker News API (Show HN, top posts), Product Hunt, Y Combinator company directory
2. **LLM extraction** — For each scraped item, extract: idea name, one-line summary, problem it solves, business model, competitors
3. **LLM evaluation** — LLM writes its own structured eval (hidden from user until they submit theirs)
4. **Select top 5** — Filter/rank for most interesting or viable ideas
5. **Send daily email** — 5 ideas with context + link to web app
6. **Store everything** — SQLite database

### Web App
- User sees 5 ideas with extracted context (name, summary, problem, business model, competitors, source link)
- User fills in structured eval per idea (see schema below)
- On submit: LLM eval is revealed side-by-side
- Historical view: browse past days, see agreement/disagreement patterns

### Daily Email
- Sent every morning
- Contains: 5 ideas with summaries + source links
- CTA: "Evaluate today's ideas →" linking to web app
- Email sending method: TBD (user will plug in SendGrid, Gmail app password, Resend, or similar)

## Eval Schema

### What the system provides (per idea):
- **Idea name**
- **One-line summary**
- **Source link** (HN / PH post)
- **Problem it solves**
- **Business model** (how it makes money)
- **Competitors** (LLM-researched)

### What the user fills in (per idea):
- **Will it work?** — Yes / No / Maybe
- **Why / why not?** — Freeform text, required
- **Biggest risk?** — Pick one: market size, execution, timing, competition, regulation
- **Confidence** — 1-5 scale
- **Would you build it?** — Yes / No

### LLM eval (hidden until user submits):
- Same structure as user eval: will it work, why/why not, biggest risk, confidence, would you build it
- Revealed side-by-side after user submits their eval

## Tech Stack (Suggested)
- **Language:** Python
- **Scraping:** HN API (https://hacker-news.firebaseio.com/v0/), Product Hunt API or scraper
- **LLM:** Claude API (claude-sonnet-4-5-20250929) for extraction + evaluation
- **Web framework:** FastAPI or Flask
- **Frontend:** Simple HTML/CSS or React if preferred — key UX is the reveal mechanic
- **Database:** SQLite (simple, no infra needed)
- **Email:** Stub out — user will plug in their provider
- **Scheduling:** Cron job or similar scheduler

## Key UX Detail: The Reveal Mechanic
- When user loads the daily page, LLM evals are **hidden**
- Each idea has the eval form visible
- User must submit their eval before the LLM eval panel unlocks for that idea
- After submission, LLM eval appears next to user's eval
- This prevents anchoring — user thinks independently first

## Data Model (SQLite)

### ideas table
- id (primary key)
- date (date the idea was served)
- source (hn / producthunt / yc)
- source_url
- idea_name
- summary
- problem
- business_model
- competitors (JSON array)
- llm_will_it_work (yes/no/maybe)
- llm_reasoning (text)
- llm_biggest_risk (enum)
- llm_confidence (1-5)
- llm_would_build (yes/no)

### user_evals table
- id (primary key)
- idea_id (foreign key → ideas)
- will_it_work (yes/no/maybe)
- reasoning (text)
- biggest_risk (enum: market_size, execution, timing, competition, regulation)
- confidence (1-5)
- would_build (yes/no)
- submitted_at (timestamp)

## Build Order
1. HN scraper + LLM extraction pipeline (get ideas flowing)
2. LLM eval generation + SQLite storage
3. Web app with eval form + reveal mechanic
4. Email template + sending (stub initially)
5. Cron job to run daily
6. Historical view / dashboard showing agreement patterns over time

## Future Ideas (Not for V1)
- Track accuracy over time: revisit ideas after 6 months, mark which ones actually succeeded
- Agreement/disagreement dashboard: where do you and the LLM diverge most?
- Tag ideas by category (AI, fintech, devtools, etc.) and track your accuracy per category
- Add paper summarizer as second daily feed
- Add daily news briefing as wrapper combining all feeds

## Open Questions
- Email provider: user needs to decide (SendGrid, Gmail, Resend, etc.)
- Hosting: local cron vs. deployed somewhere (Railway, Fly.io, etc.)
- How many HN posts to scrape per day before filtering to 5?
- Should Product Hunt and YC be in V1 or just start with HN?
