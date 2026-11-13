# Startup Eval

Daily startup evaluation tool. It picks 3 startups from today's Show HN posts, you evaluate them, and an AI critiques your reasoning.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and add your [Groq API key](https://console.groq.com/keys):

```
GROQ_API_KEY=your_key_here
```

Then run:

```bash
python app.py
```

Open http://localhost:5000

## Configuration

All config is via environment variables (or `.env` file):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROQ_API_KEY` | Yes | — | Your Groq API key |
| `GROQ_MODEL` | No | `qwen/qwen3.6-27b` | Groq model to use |
| `FLASK_DEBUG` | No | `0` | Set to `1` for debug mode |
| `PORT` | No | `5000` | Server port |

## How it works

1. Click "Load Today's Startups". The app fetches Show HN posts and the AI picks the 3 most interesting real products.
2. Evaluate each: will it work, why, biggest risk, confidence, would you build it.
3. Submit. The AI critiques your reasoning, focusing on where you're wrong.
4. History is stored in SQLite (`startup_eval.db`) and can be exported as CSV.

## Architecture

```
Browser (templates/index.html)
   │  /api/today, /api/fetch, /api/eval, /api/history
   ▼
Flask (app.py) ──► Hacker News API (Show HN posts)
   │          └──► Groq API (pick startups, write critiques)
   ▼
SQLite (startups, evals, critiques)
```

### Where the AI is used

| Step | Prompt | Output |
|------|--------|--------|
| Pick 3 startups from Show HN | `prompts/pickStartups.txt` | JSON list of startups |
| Critique your evaluation | `prompts/critique.txt` | JSON critique |

Every AI reply is validated before it is saved (`cleanStartup`, `cleanCritique`). Replies with missing fields are dropped, and the user sees a clear error instead of a crash. If the AI fails, nothing is saved, so yesterday's startups and your unsaved evaluations are never lost. Each call logs its duration and token usage.

### Known limitations

- Startup details (problem, business model) are written by the AI from a short HN post, so they can be wrong.
- Critiques may cite companies or numbers from the model's memory. Treat them as a sparring partner, not a fact source.
- There is no login. The app only listens on `localhost`, so don't expose it to the internet as-is.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

Tests use a fake AI client, so they don't need an API key or network.

### Evals

`evals/runEvals.py` runs the real prompts against fixed sample posts and checks the answers (picks only real posts, skips essays, critique calls out a lazy evaluation). It uses your real API key:

```bash
python evals/runEvals.py
```

Run it after changing a prompt or the model.

## Code style

The code deliberately uses camelCase everywhere, including Python, for readability across the Python and JavaScript parts. This breaks the usual Python style guide (PEP 8) on purpose. Run `ruff check .` for linting.

## Stack

- Python + Flask
- Groq API (`qwen/qwen3.6-27b` by default)
- SQLite
- Vanilla HTML/CSS/JS frontend

## License

MIT
