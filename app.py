import json
import logging
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

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger("startupEval")

groqApiKey = os.environ["GROQ_API_KEY"]
groqModel = os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b")
projectFolder = Path(__file__).parent
databasePath = projectFolder / "startup_eval.db"
hackerNewsApiUrl = "https://hacker-news.firebaseio.com/v0"

app = Flask(__name__)
groqClient = Groq(api_key=groqApiKey)


def readPrompt(fileName):
    return (projectFolder / "prompts" / fileName).read_text(encoding="utf-8")


pickStartupsInstructions = readPrompt("pickStartups.txt")
critiqueInstructions = readPrompt("critique.txt")


class AiError(Exception):
    pass


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

    database.executescript("""
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
        );

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
        );

        CREATE TABLE IF NOT EXISTS critiques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evalId INTEGER NOT NULL,
            agrees INTEGER,
            verdict TEXT,
            mainArgument TEXT,
            blindSpots TEXT,
            strongestPoint TEXT,
            FOREIGN KEY (evalId) REFERENCES evals(id)
        );
    """)

    database.commit()
    database.close()


def fetchShowHackerNews(limit=20):
    try:
        storyIdsResponse = requests.get(f"{hackerNewsApiUrl}/showstories.json", timeout=10)
        storyIds = storyIdsResponse.json()[:limit]
    except Exception as error:
        logger.warning(f"HN fetch error: {error}")
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


def askGroqForJson(taskName, messages, temperature=0.7):
    startTime = time.time()
    try:
        response = groqClient.chat.completions.create(
            model=groqModel,
            messages=messages,
            temperature=temperature,
            max_tokens=4000,
            response_format={"type": "json_object"},
            extra_body={"reasoning_effort": "none"},
        )
        parsedReply = json.loads(response.choices[0].message.content)
    except Exception as error:
        logger.warning(f"AI call '{taskName}' failed: [{type(error).__name__}] {str(error)[:200]}")
        raise AiError("The AI service failed. Please try again.") from error

    secondsTaken = time.time() - startTime
    usage = response.usage
    if usage:
        logger.info(
            f"AI call '{taskName}': {secondsTaken:.1f}s, "
            f"{usage.prompt_tokens} prompt tokens, {usage.completion_tokens} reply tokens"
        )

    if not isinstance(parsedReply, dict):
        raise AiError("The AI returned an unexpected answer. Please try again.")
    return parsedReply


def readText(item, fieldName, maxLength):
    value = item.get(fieldName)
    if not isinstance(value, str):
        return ""
    return value.strip()[:maxLength]


def cleanStartup(item):
    if not isinstance(item, dict):
        return None

    startup = {
        "name": readText(item, "name", 200),
        "summary": readText(item, "summary", 500),
        "url": readText(item, "url", 500),
        "source": readText(item, "source", 100) or "Hacker News",
        "problem": readText(item, "problem", 1000),
        "businessModel": readText(item, "businessModel", 1000),
        "keyBet": readText(item, "keyBet", 1000),
    }

    requiredFields = ["name", "summary", "problem", "businessModel", "keyBet"]
    for fieldName in requiredFields:
        if not startup[fieldName]:
            return None
    return startup


def cleanCritique(reply):
    blindSpots = []
    rawBlindSpots = reply.get("blindSpots")
    if isinstance(rawBlindSpots, list):
        for blindSpot in rawBlindSpots:
            if isinstance(blindSpot, str) and blindSpot.strip():
                blindSpots.append(blindSpot.strip()[:1000])

    critique = {
        "agrees": reply.get("agrees") is True,
        "verdict": readText(reply, "verdict", 500),
        "mainArgument": readText(reply, "mainArgument", 3000),
        "blindSpots": blindSpots[:5],
        "strongestPoint": readText(reply, "strongestPoint", 1500),
    }

    if not critique["verdict"] and not critique["mainArgument"]:
        raise AiError("The AI returned an empty critique. Please try again.")
    return critique


def pickStartups(hackerNewsStories):
    storyDescriptions = []
    for storyNumber, story in enumerate(hackerNewsStories[:15], start=1):
        description = f'{storyNumber}. "{story["title"]}" ({story["url"]}) — {story["score"]} pts\n{story["text"]}'
        storyDescriptions.append(description)

    allStoriesText = "\n\n".join(storyDescriptions)
    userMessage = f"Today's Show HN posts:\n\n{allStoriesText}\n\nSelect the 3 most interesting startups to evaluate. Return JSON only."

    messages = [
        {"role": "system", "content": pickStartupsInstructions},
        {"role": "user", "content": userMessage},
    ]
    reply = askGroqForJson("pickStartups", messages, temperature=0.8)

    rawStartups = reply.get("startups")
    if not isinstance(rawStartups, list):
        rawStartups = []

    startups = []
    for item in rawStartups:
        startup = cleanStartup(item)
        if startup:
            startups.append(startup)
    return startups[:3]


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
    reply = askGroqForJson("critique", messages)
    return cleanCritique(reply)


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
    hackerNewsStories = fetchShowHackerNews()
    if not hackerNewsStories:
        return jsonify({"error": "Could not load Hacker News. Please try again."}), 502

    try:
        chosenStartups = pickStartups(hackerNewsStories)
    except AiError as error:
        return jsonify({"error": str(error)}), 502

    if not chosenStartups:
        return jsonify({"error": "The AI did not return any usable startups. Please try again."}), 502

    database = getDatabase()
    today = date.today().isoformat()
    database.execute("DELETE FROM startups WHERE batchDate = ?", (today,))

    savedStartups = []
    for startup in chosenStartups:
        cursor = database.execute(
            """INSERT INTO startups (batchDate, name, summary, url, source, problem, businessModel, keyBet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                today,
                startup["name"],
                startup["summary"],
                startup["url"],
                startup["source"],
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

    try:
        critique = generateCritique(startup, userEvaluation)
    except AiError as error:
        return jsonify({"error": str(error)}), 502

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

    database.execute(
        """INSERT INTO critiques (evalId, agrees, verdict, mainArgument, blindSpots, strongestPoint)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            evaluationId,
            int(critique["agrees"]),
            critique["verdict"],
            critique["mainArgument"],
            json.dumps(critique["blindSpots"]),
            critique["strongestPoint"],
        ),
    )
    database.commit()

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
