# Startup Eval

Daily startup evaluation tool. Fetches startups from Hacker News + YC, you evaluate them, AI critiques your reasoning.

## Setup

```bash
cd startup-eval
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
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | Groq model to use |
| `FLASK_DEBUG` | No | `0` | Set to `1` for debug mode |
| `PORT` | No | `5000` | Server port |

## How it works

1. Click "Load Today's Startups" — fetches Show HN posts, Groq picks the 3 most interesting (mixing in YC-funded startups)
2. Evaluate each: will it work, why, biggest risk, confidence, would you build it
3. Submit — AI critiques your reasoning, focusing on where you're wrong
4. History persists in SQLite (`startup_eval.db`)

## Stack

- Python + Flask
- Groq API (Llama 3.3 70B)
- SQLite
- Vanilla HTML/CSS/JS frontend

## License

MIT
