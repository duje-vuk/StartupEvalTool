import json
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request
from groq import Groq

load_dotenv()

# ─── Config ───────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
DB_PATH = Path(__file__).parent / "startup_eval.db"
HN_API = "https://hacker-news.firebaseio.com/v0"

app = Flask(__name__)
groq_client = Groq(api_key=GROQ_API_KEY)


# ─── Database ─────────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("""
        CREATE TABLE IF NOT EXISTS startups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_date TEXT NOT NULL,
            name TEXT NOT NULL,
            summary TEXT,
            url TEXT,
            source TEXT,
            problem TEXT,
            business_model TEXT,
            key_bet TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS evals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            startup_id INTEGER NOT NULL,
            will_work TEXT,
            reasoning TEXT,
            biggest_risk TEXT,
            confidence INTEGER,
            would_build TEXT,
            submitted_at TEXT,
            FOREIGN KEY (startup_id) REFERENCES startups(id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS critiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eval_id INTEGER NOT NULL,
            agrees INTEGER,
            verdict TEXT,
            main_argument TEXT,
            blind_spots TEXT,
            strongest_point TEXT,
            FOREIGN KEY (eval_id) REFERENCES evals(id)
        )
    """)
    db.commit()
    db.close()


# ─── HN Fetching ─────────────────────────────────────────────────────────────
def fetch_show_hn(limit=20):
    try:
        ids = requests.get(f"{HN_API}/showstories.json", timeout=10).json()
        stories = []
        for story_id in ids[:limit]:
            try:
                s = requests.get(f"{HN_API}/item/{story_id}.json", timeout=5).json()
                if s and s.get("title"):
                    stories.append({
                        "title": s["title"],
                        "url": s.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                        "score": s.get("score", 0),
                        "text": (s.get("text") or "")[:300],
                    })
            except Exception:
                continue
        return stories
    except Exception as e:
        print(f"HN fetch error: {e}")
        return []


# ─── Groq LLM ────────────────────────────────────────────────────────────────
def groq_json(messages, temperature=0.7):
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=4000,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def groq_text(messages, temperature=0.7):
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=4000,
    )
    return resp.choices[0].message.content


def pick_startups(hn_stories):
    hn_context = "\n\n".join(
        f'{i+1}. "{s["title"]}" ({s["url"]}) — {s["score"]} pts\n{s["text"]}'
        for i, s in enumerate(hn_stories[:15])
    )

    return groq_json([
        {
            "role": "system",
            "content": """You are a startup analyst. You receive Show HN posts from Hacker News. Your job:

1. From the HN posts, identify any that are actual startups or products (not articles, questions, or libraries with no commercial intent).
2. Additionally, recall 2-3 recent notable YC-funded startups from recent YC batches (W25, S24, W24) that you know about. These should be real companies.
3. From ALL candidates, select exactly 3 that are the most interesting to evaluate as businesses.

Return ONLY valid JSON:
{
  "startups": [
    {
      "name": "string",
      "summary": "One sentence summary",
      "url": "source URL",
      "source": "Hacker News" or "YC W25" etc,
      "problem": "What problem it solves",
      "business_model": "How it makes money",
      "key_bet": "The core assumption that must be true for this to work"
    }
  ]
}

Pick diverse startups (not all same category). Prefer ones where reasonable people could disagree on viability.""",
        },
        {
            "role": "user",
            "content": f"Today's Show HN posts:\n\n{hn_context}\n\nSelect the 3 most interesting startups to evaluate. Return JSON only.",
        },
    ], temperature=0.8)


def generate_critique(startup, user_eval):
    return groq_json([
        {
            "role": "system",
            "content": """You are a brutally honest startup investor who has seen 10,000 pitches. A user evaluated a startup. Your job: find the weakest part of their reasoning and attack it.

Rules:
- DO NOT be diplomatic. Be specific and concrete.
- If they're right, say so in one sentence, then tell them what they STILL got wrong or missed.
- If they're wrong, explain exactly why with specific evidence or analogies to real companies.
- Never praise them for "identifying X as a risk" — that's empty. Instead, tell them whether their risk assessment is actually correct and why.
- Reference specific companies, markets, or patterns when making your case. "There's market demand" is weak. "Citus was acquired by Microsoft for $X because Postgres scaling is a $Y market" is strong.
- Your blind_spots should be specific factual gaps, not vague reframings of their point.
- Your strongest_point should explain WHY it's strong, not just restate it.
- Vary your structure. Don't follow the same pattern every time.
- If the user gave a lazy one-liner evaluation, call that out. They need to think harder.
- If the user is directionally right but for the wrong reasons, that's more interesting than being flat wrong. Say so.

Return ONLY valid JSON:
{
  "agrees": true/false,
  "verdict": "short, punchy — your actual take on the startup, not on their eval",
  "main_argument": "2-4 sentences. Be concrete. Name companies, cite patterns, use numbers if you know them. Attack the weakest link in their reasoning.",
  "blind_spots": ["2-3 SPECIFIC things they missed — not generic reframings. Each should contain a concrete fact, comparison, or question they failed to ask."],
  "strongest_point": "What they got right AND why it matters. If nothing was strong, say so."
}""",
        },
        {
            "role": "user",
            "content": f"""STARTUP:
Name: {startup["name"]}
Summary: {startup["summary"]}
Problem: {startup["problem"]}
Business Model: {startup["business_model"]}
Key Bet: {startup["key_bet"]}
Source: {startup["source"]}

USER'S EVALUATION:
Will it work? {user_eval["will_work"]}
Reasoning: {user_eval["reasoning"]}
Biggest risk: {user_eval["biggest_risk"]}
Confidence: {user_eval["confidence"]}/5
Would build it: {user_eval["would_build"]}

Critique their evaluation. Return JSON only.""",
        },
    ])


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/today")
def api_today():
    """Get today's startups. If already fetched today, return cached."""
    db = get_db()
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT * FROM startups WHERE batch_date = ?", (today,)
    ).fetchall()

    if rows:
        startups = []
        for r in rows:
            s = dict(r)
            # Check if eval exists
            ev = db.execute(
                "SELECT * FROM evals WHERE startup_id = ?", (s["id"],)
            ).fetchone()
            if ev:
                s["eval"] = dict(ev)
                cr = db.execute(
                    "SELECT * FROM critiques WHERE eval_id = ?", (ev["id"],)
                ).fetchone()
                if cr:
                    cr_dict = dict(cr)
                    cr_dict["blind_spots"] = json.loads(cr_dict.get("blind_spots") or "[]")
                    s["critique"] = cr_dict
            startups.append(s)
        return jsonify({"startups": startups, "cached": True})

    return jsonify({"startups": [], "cached": False})


@app.route("/api/fetch", methods=["POST"])
def api_fetch():
    """Fetch new startups from HN + Groq."""
    db = get_db()
    today = date.today().isoformat()

    # Clear today's if re-fetching
    db.execute("DELETE FROM startups WHERE batch_date = ?", (today,))
    db.commit()

    hn_stories = fetch_show_hn()
    result = pick_startups(hn_stories)

    startups_out = []
    for s in result.get("startups", [])[:3]:
        db.execute(
            """INSERT INTO startups (batch_date, name, summary, url, source, problem, business_model, key_bet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (today, s["name"], s["summary"], s.get("url", ""), s.get("source", ""),
             s["problem"], s["business_model"], s["key_bet"]),
        )
        s["id"] = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        s["batch_date"] = today
        startups_out.append(s)

    db.commit()
    return jsonify({"startups": startups_out})


@app.route("/api/eval", methods=["POST"])
def api_eval():
    """Submit user eval, get AI critique back."""
    data = request.json
    if not data:
        return jsonify({"error": "Request body required"}), 400

    required = ["startup_id", "will_work", "reasoning", "biggest_risk", "confidence", "would_build"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        startup_id = int(data["startup_id"])
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid startup_id"}), 400

    db = get_db()

    # Get startup
    row = db.execute("SELECT * FROM startups WHERE id = ?", (startup_id,)).fetchone()
    if not row:
        return jsonify({"error": "Startup not found"}), 404
    startup = dict(row)

    # Save user eval
    db.execute(
        """INSERT INTO evals (startup_id, will_work, reasoning, biggest_risk, confidence, would_build, submitted_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (startup_id, data["will_work"], data["reasoning"], data["biggest_risk"],
         data["confidence"], data["would_build"], datetime.now().isoformat()),
    )
    eval_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()

    # Generate critique
    critique = generate_critique(startup, data)

    # Save critique
    db.execute(
        """INSERT INTO critiques (eval_id, agrees, verdict, main_argument, blind_spots, strongest_point)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (eval_id, 1 if critique.get("agrees") else 0, critique.get("verdict", ""),
         critique.get("main_argument", ""), json.dumps(critique.get("blind_spots", [])),
         critique.get("strongest_point", "")),
    )
    db.commit()

    critique["blind_spots"] = critique.get("blind_spots", [])
    return jsonify({"eval_id": eval_id, "critique": critique})


@app.route("/api/history")
def api_history():
    """Get all past sessions."""
    db = get_db()
    dates = db.execute(
        "SELECT DISTINCT batch_date FROM startups ORDER BY batch_date DESC"
    ).fetchall()

    sessions = []
    for d in dates:
        batch_date = d["batch_date"]
        rows = db.execute(
            "SELECT * FROM startups WHERE batch_date = ?", (batch_date,)
        ).fetchall()
        session_startups = []
        for r in rows:
            s = dict(r)
            ev = db.execute(
                "SELECT * FROM evals WHERE startup_id = ?", (s["id"],)
            ).fetchone()
            if ev:
                s["eval"] = dict(ev)
                cr = db.execute(
                    "SELECT * FROM critiques WHERE eval_id = ?", (ev["id"],)
                ).fetchone()
                if cr:
                    cr_dict = dict(cr)
                    cr_dict["blind_spots"] = json.loads(cr_dict.get("blind_spots") or "[]")
                    s["critique"] = cr_dict
            session_startups.append(s)
        sessions.append({"date": batch_date, "startups": session_startups})

    return jsonify({"sessions": sessions})


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    print("Starting Startup Eval on http://localhost:5000")
    app.run(debug=os.getenv("FLASK_DEBUG", "0") == "1", port=int(os.getenv("PORT", 5000)))
