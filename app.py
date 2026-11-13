import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request
from groq import Groq

load_dotenv()

groqApiKey = os.environ["GROQ_API_KEY"]
groqModel = os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b")
databasePath = Path(__file__).parent / "startup_eval.db"
hackerNewsApiUrl = "https://hacker-news.firebaseio.com/v0"

app = Flask(__name__)
groqClient = Groq(api_key=groqApiKey)


def getDatabase():
    if "db" not in g:
        g.db = sqlite3.connect(str(databasePath))
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def closeDatabase(error):
    database = g.pop("db", None)
    if database:
        database.close()


def createTables():
    database = sqlite3.connect(str(databasePath))

    database.execute("""
        CREATE TABLE IF NOT EXISTS startups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batchDate TEXT NOT NULL,
            name TEXT NOT NULL,
            summary TEXT,
            url TEXT,
            source TEXT,
            problem TEXT,
            businessModel TEXT,
            keyBet TEXT
        )
    """)

    database.execute("""
        CREATE TABLE IF NOT EXISTS evals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            startupId INTEGER NOT NULL,
            willWork TEXT,
            reasoning TEXT,
            biggestRisk TEXT,
            confidence INTEGER,
            wouldBuild TEXT,
            submittedAt TEXT,
            FOREIGN KEY (startupId) REFERENCES startups(id)
        )
    """)

    database.execute("""
        CREATE TABLE IF NOT EXISTS critiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evalId INTEGER NOT NULL,
            agrees INTEGER,
            verdict TEXT,
            mainArgument TEXT,
            blindSpots TEXT,
            strongestPoint TEXT,
            FOREIGN KEY (evalId) REFERENCES evals(id)
        )
    """)

    database.commit()
    database.close()


def fetchShowHackerNews(limit=20):
    try:
        storyIdsResponse = requests.get(f"{hackerNewsApiUrl}/showstories.json", timeout=10)
        storyIds = storyIdsResponse.json()[:limit]
    except Exception as error:
        print(f"HN fetch error: {error}")
        return []

    stories = []
    for storyId in storyIds:
        try:
            storyResponse = requests.get(f"{hackerNewsApiUrl}/item/{storyId}.json", timeout=5)
            story = storyResponse.json()
        except Exception:
            continue

        if not story or not story.get("title"):
            continue

        fallbackUrl = f"https://news.ycombinator.com/item?id={storyId}"
        storyText = story.get("text") or ""

        stories.append({
            "title": story["title"],
            "url": story.get("url", fallbackUrl),
            "score": story.get("score", 0),
            "text": storyText[:300],
        })

    return stories


def askGroqForJson(messages, temperature=0.7):
    response = groqClient.chat.completions.create(
        model=groqModel,
        messages=messages,
        temperature=temperature,
        max_tokens=4000,
        response_format={"type": "json_object"},
        extra_body={"reasoning_effort": "none"},
    )
    replyText = response.choices[0].message.content
    return json.loads(replyText)


pickStartupsInstructions = """You are a startup analyst. You receive Show HN posts from Hacker News. Your job:

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
      "businessModel": "How it makes money",
      "keyBet": "The core assumption that must be true for this to work"
    }
  ]
}

Pick diverse startups (not all same category). Prefer ones where reasonable people could disagree on viability."""


critiqueInstructions = """You are a brutally honest startup investor who has seen 10,000 pitches. A user evaluated a startup. Your job: find the weakest part of their reasoning and attack it.

Rules:
- DO NOT be diplomatic. Be specific and concrete.
- If they're right, say so in one sentence, then tell them what they STILL got wrong or missed.
- If they're wrong, explain exactly why with specific evidence or analogies to real companies.
- Never praise them for "identifying X as a risk" — that's empty. Instead, tell them whether their risk assessment is actually correct and why.
- Reference specific companies, markets, or patterns when making your case. "There's market demand" is weak. "Citus was acquired by Microsoft for $X because Postgres scaling is a $Y market" is strong.
- Your blindSpots should be specific factual gaps, not vague reframings of their point.
- Your strongestPoint should explain WHY it's strong, not just restate it.
- Vary your structure. Don't follow the same pattern every time.
- If the user gave a lazy one-liner evaluation, call that out. They need to think harder.
- If the user is directionally right but for the wrong reasons, that's more interesting than being flat wrong. Say so.

Return ONLY valid JSON:
{
  "agrees": true/false,
  "verdict": "short, punchy — your actual take on the startup, not on their eval",
  "mainArgument": "2-4 sentences. Be concrete. Name companies, cite patterns, use numbers if you know them. Attack the weakest link in their reasoning.",
  "blindSpots": ["2-3 SPECIFIC things they missed — not generic reframings. Each should contain a concrete fact, comparison, or question they failed to ask."],
  "strongestPoint": "What they got right AND why it matters. If nothing was strong, say so."
}"""


def pickStartups(hackerNewsStories):
    storyDescriptions = []
    storyNumber = 1
    for story in hackerNewsStories[:15]:
        description = f'{storyNumber}. "{story["title"]}" ({story["url"]}) — {story["score"]} pts\n{story["text"]}'
        storyDescriptions.append(description)
        storyNumber += 1

    allStoriesText = "\n\n".join(storyDescriptions)
    userMessage = f"Today's Show HN posts:\n\n{allStoriesText}\n\nSelect the 3 most interesting startups to evaluate. Return JSON only."

    messages = [
        {"role": "system", "content": pickStartupsInstructions},
        {"role": "user", "content": userMessage},
    ]
    return askGroqForJson(messages, temperature=0.8)


def generateCritique(startup, userEvaluation):
    userMessage = f"""STARTUP:
Name: {startup["name"]}
Summary: {startup["summary"]}
Problem: {startup["problem"]}
Business Model: {startup["businessModel"]}
Key Bet: {startup["keyBet"]}
Source: {startup["source"]}

USER'S EVALUATION:
Will it work? {userEvaluation["willWork"]}
Reasoning: {userEvaluation["reasoning"]}
Biggest risk: {userEvaluation["biggestRisk"]}
Confidence: {userEvaluation["confidence"]}/5
Would build it: {userEvaluation["wouldBuild"]}

Critique their evaluation. Return JSON only."""

    messages = [
        {"role": "system", "content": critiqueInstructions},
        {"role": "user", "content": userMessage},
    ]
    return askGroqForJson(messages)


def loadStartupWithEvaluation(database, startupRow):
    startup = dict(startupRow)

    evaluationRow = database.execute(
        "SELECT * FROM evals WHERE startupId = ?", (startup["id"],)
    ).fetchone()
    if not evaluationRow:
        return startup

    startup["eval"] = dict(evaluationRow)

    critiqueRow = database.execute(
        "SELECT * FROM critiques WHERE evalId = ?", (evaluationRow["id"],)
    ).fetchone()
    if not critiqueRow:
        return startup

    critique = dict(critiqueRow)
    blindSpotsText = critique.get("blindSpots") or "[]"
    critique["blindSpots"] = json.loads(blindSpotsText)
    startup["critique"] = critique
    return startup


@app.route("/")
def homePage():
    return render_template("index.html")


@app.route("/api/today")
def getTodaysStartups():
    database = getDatabase()
    today = date.today().isoformat()

    startupRows = database.execute(
        "SELECT * FROM startups WHERE batchDate = ?", (today,)
    ).fetchall()

    if not startupRows:
        return jsonify({"startups": [], "cached": False})

    startups = []
    for startupRow in startupRows:
        startups.append(loadStartupWithEvaluation(database, startupRow))

    return jsonify({"startups": startups, "cached": True})


@app.route("/api/fetch", methods=["POST"])
def fetchNewStartups():
    database = getDatabase()
    today = date.today().isoformat()

    database.execute("DELETE FROM startups WHERE batchDate = ?", (today,))
    database.commit()

    hackerNewsStories = fetchShowHackerNews()
    aiResult = pickStartups(hackerNewsStories)
    chosenStartups = aiResult.get("startups", [])[:3]

    savedStartups = []
    for startup in chosenStartups:
        cursor = database.execute(
            """INSERT INTO startups (batchDate, name, summary, url, source, problem, businessModel, keyBet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                today,
                startup["name"],
                startup["summary"],
                startup.get("url", ""),
                startup.get("source", ""),
                startup["problem"],
                startup["businessModel"],
                startup["keyBet"],
            ),
        )
        startup["id"] = cursor.lastrowid
        startup["batchDate"] = today
        savedStartups.append(startup)

    database.commit()
    return jsonify({"startups": savedStartups})


@app.route("/api/eval", methods=["POST"])
def submitEvaluation():
    userEvaluation = request.json
    if not userEvaluation:
        return jsonify({"error": "Request body required"}), 400

    requiredFields = ["startupId", "willWork", "reasoning", "biggestRisk", "confidence", "wouldBuild"]
    missingFields = []
    for field in requiredFields:
        if field not in userEvaluation:
            missingFields.append(field)

    if missingFields:
        return jsonify({"error": f"Missing fields: {', '.join(missingFields)}"}), 400

    try:
        startupId = int(userEvaluation["startupId"])
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid startupId"}), 400

    database = getDatabase()

    startupRow = database.execute("SELECT * FROM startups WHERE id = ?", (startupId,)).fetchone()
    if not startupRow:
        return jsonify({"error": "Startup not found"}), 404
    startup = dict(startupRow)

    cursor = database.execute(
        """INSERT INTO evals (startupId, willWork, reasoning, biggestRisk, confidence, wouldBuild, submittedAt)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            startupId,
            userEvaluation["willWork"],
            userEvaluation["reasoning"],
            userEvaluation["biggestRisk"],
            userEvaluation["confidence"],
            userEvaluation["wouldBuild"],
            datetime.now().isoformat(),
        ),
    )
    evaluationId = cursor.lastrowid
    database.commit()

    critique = generateCritique(startup, userEvaluation)
    blindSpots = critique.get("blindSpots", [])

    if critique.get("agrees"):
        agreesNumber = 1
    else:
        agreesNumber = 0

    database.execute(
        """INSERT INTO critiques (evalId, agrees, verdict, mainArgument, blindSpots, strongestPoint)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            evaluationId,
            agreesNumber,
            critique.get("verdict", ""),
            critique.get("mainArgument", ""),
            json.dumps(blindSpots),
            critique.get("strongestPoint", ""),
        ),
    )
    database.commit()

    critique["blindSpots"] = blindSpots
    return jsonify({"evalId": evaluationId, "critique": critique})


@app.route("/api/history")
def getHistory():
    database = getDatabase()

    dateRows = database.execute(
        "SELECT DISTINCT batchDate FROM startups ORDER BY batchDate DESC"
    ).fetchall()

    sessions = []
    for dateRow in dateRows:
        batchDate = dateRow["batchDate"]
        startupRows = database.execute(
            "SELECT * FROM startups WHERE batchDate = ?", (batchDate,)
        ).fetchall()

        sessionStartups = []
        for startupRow in startupRows:
            sessionStartups.append(loadStartupWithEvaluation(database, startupRow))

        sessions.append({"date": batchDate, "startups": sessionStartups})

    return jsonify({"sessions": sessions})


if __name__ == "__main__":
    createTables()
    print("Starting Startup Eval on http://localhost:5000")
    debugMode = os.getenv("FLASK_DEBUG", "0") == "1"
    portNumber = int(os.getenv("PORT", 5000))
    app.run(debug=debugMode, port=portNumber)
