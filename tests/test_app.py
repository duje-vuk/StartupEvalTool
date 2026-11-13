import json
from types import SimpleNamespace

import pytest

import app as startupEval


def makeFakeGroq(replies):
    calls = []

    def create(**options):
        calls.append(options)
        replyText = replies.pop(0)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20)
        message = SimpleNamespace(content=replyText)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    fakeClient = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return fakeClient, calls


goodStartup = {
    "name": "Acme",
    "summary": "Does things",
    "url": "https://acme.test",
    "source": "Hacker News",
    "problem": "A problem",
    "businessModel": "Subscriptions",
    "keyBet": "People pay",
}

goodCritique = {
    "agrees": False,
    "verdict": "Weak idea",
    "mainArgument": "Because reasons.",
    "blindSpots": ["Competition", "", 5],
    "strongestPoint": "None",
}

sampleEvaluation = {
    "willWork": "yes",
    "reasoning": "Big market",
    "biggestRisk": "timing",
    "confidence": 3,
    "wouldBuild": "no",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(startupEval, "databasePath", tmp_path / "test.db")
    monkeypatch.setattr(startupEval, "fetchShowHackerNews", lambda: [
        {"title": "Show HN: Acme", "url": "https://acme.test", "score": 5, "text": ""},
    ])
    startupEval.createTables()
    return startupEval.app.test_client()


def useFakeGroq(monkeypatch, replies):
    fakeClient, calls = makeFakeGroq([json.dumps(reply) if not isinstance(reply, str) else reply for reply in replies])
    monkeypatch.setattr(startupEval, "groqClient", fakeClient)
    return calls


def test_cleanStartupRejectsMissingFields():
    assert startupEval.cleanStartup(goodStartup)["name"] == "Acme"
    assert startupEval.cleanStartup({**goodStartup, "keyBet": ""}) is None
    assert startupEval.cleanStartup({**goodStartup, "name": 5}) is None
    assert startupEval.cleanStartup("not a dict") is None


def test_cleanCritiqueKeepsOnlyTextBlindSpots():
    critique = startupEval.cleanCritique(goodCritique)
    assert critique["blindSpots"] == ["Competition"]
    assert critique["agrees"] is False


def test_cleanCritiqueRejectsEmptyReply():
    with pytest.raises(startupEval.AiError):
        startupEval.cleanCritique({"agrees": True})


def test_fetchSavesOnlyValidStartups(client, monkeypatch):
    calls = useFakeGroq(monkeypatch, [{"startups": [goodStartup, {"name": "Broken"}]}])
    response = client.post("/api/fetch")
    assert response.status_code == 200
    assert [startup["name"] for startup in response.get_json()["startups"]] == ["Acme"]
    assert calls[0]["extra_body"] == {"reasoning_effort": "none"}


def test_fetchKeepsOldStartupsWhenAiFails(client, monkeypatch):
    useFakeGroq(monkeypatch, [{"startups": [goodStartup]}])
    client.post("/api/fetch")

    useFakeGroq(monkeypatch, ["this is not json"])
    response = client.post("/api/fetch")
    assert response.status_code == 502
    assert "error" in response.get_json()
    assert len(client.get("/api/today").get_json()["startups"]) == 1


def test_evalSavesCritique(client, monkeypatch):
    useFakeGroq(monkeypatch, [{"startups": [goodStartup]}, goodCritique])
    startupId = client.post("/api/fetch").get_json()["startups"][0]["id"]

    response = client.post("/api/eval", json={"startupId": startupId, **sampleEvaluation})
    assert response.status_code == 200
    assert response.get_json()["critique"]["verdict"] == "Weak idea"

    history = client.get("/api/history").get_json()["sessions"]
    assert history[0]["startups"][0]["critique"]["blindSpots"] == ["Competition"]


def test_evalSavesNothingWhenAiFails(client, monkeypatch):
    useFakeGroq(monkeypatch, [{"startups": [goodStartup]}, {"agrees": True}])
    startupId = client.post("/api/fetch").get_json()["startups"][0]["id"]

    response = client.post("/api/eval", json={"startupId": startupId, **sampleEvaluation})
    assert response.status_code == 502
    assert "eval" not in client.get("/api/today").get_json()["startups"][0]


def test_evalRejectsBadInput(client):
    assert client.post("/api/eval", json={"startupId": 1}).status_code == 400
    assert client.post("/api/eval", json={"startupId": "x", **sampleEvaluation}).status_code == 400
    assert client.post("/api/eval", json={"startupId": 999, **sampleEvaluation}).status_code == 404
