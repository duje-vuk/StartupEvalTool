import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import app as startupEval

sampleStories = [
    {"title": "Show HN: InvoiceBot – AI that chases unpaid invoices", "url": "https://invoicebot.example", "score": 120, "text": "We email your late-paying clients for you."},
    {"title": "Show HN: My weekend Rust raytracer", "url": "https://github.com/example/raytracer", "score": 80, "text": "A hobby project."},
    {"title": "Show HN: ShiftSwap – shift trading for hospital nurses", "url": "https://shiftswap.example", "score": 95, "text": "Nurses trade shifts without calling managers."},
    {"title": "Show HN: Why I left Big Tech", "url": "https://blog.example/left", "score": 300, "text": "An essay."},
    {"title": "Show HN: PlotPal – garden planning SaaS", "url": "https://plotpal.example", "score": 40, "text": "Plan your vegetable garden, $5/month."},
]

lazyEvaluation = {
    "willWork": "yes",
    "reasoning": "good idea",
    "biggestRisk": "competition",
    "confidence": 5,
    "wouldBuild": "yes",
}

results = []


def check(name, passed, detail=""):
    results.append(passed)
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name} {detail}")


startups = startupEval.pickStartups(sampleStories)
allowedUrls = {story["url"] for story in sampleStories}
check("picks exactly 3 startups", len(startups) == 3, f"(got {len(startups)})")
check("only uses the given posts", all(startup["url"] in allowedUrls for startup in startups))
check("skips the essay", all("Big Tech" not in startup["name"] for startup in startups))

critique = startupEval.generateCritique(startups[0] if startups else {
    "name": "InvoiceBot", "summary": "Chases unpaid invoices", "problem": "Late payments",
    "businessModel": "Subscription", "keyBet": "Firms hate chasing invoices", "source": "Hacker News",
}, lazyEvaluation)
check("critique has an argument", len(critique["mainArgument"]) > 50)
check("critique lists 2-3 blind spots", 2 <= len(critique["blindSpots"]) <= 3, f"(got {len(critique['blindSpots'])})")
check("critique calls out the lazy answer", "lazy" in critique["mainArgument"].lower() or "one-liner" in critique["mainArgument"].lower()
      or "vague" in critique["mainArgument"].lower(), "(looks for 'lazy', 'one-liner' or 'vague')")

passedCount = sum(results)
print(f"\n{passedCount}/{len(results)} checks passed")
sys.exit(0 if passedCount == len(results) else 1)
