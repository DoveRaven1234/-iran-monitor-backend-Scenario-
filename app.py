import os
import json
import time
import logging
import threading
from datetime import datetime, timezone
import anthropic
from flask import Flask, jsonify
from flask_cors import CORS

# ── Setup ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # allow any frontend to call this API

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
REFRESH_INTERVAL_HOURS = int(os.environ.get("REFRESH_INTERVAL_HOURS", "4"))

# ── In-memory cache ────────────────────────────────────────────────────────────
cache = {
    "data": None,          # the parsed JSON assessment
    "last_updated": None,  # ISO timestamp string
    "next_update": None,   # ISO timestamp string
    "status": "pending",   # pending | ok | error
    "error": None,
}

# ── Prompt ─────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a geopolitical intelligence analyst specializing in Middle East affairs.
Analyze current news and provide a structured probability assessment of a US military strike against Iran.

Return a JSON object with EXACTLY this structure (no markdown, no explanation, ONLY the JSON):

{
  "probability_30d": <integer 0-100>,
  "probability_90d": <integer 0-100>,
  "confidence": <integer 0-100>,
  "threat_level": "<LOW|MODERATE|ELEVATED|HIGH|CRITICAL>",
  "key_driver": "<most important factor, max 100 chars>",
  "analyst_summary": "<2-3 paragraph summary grounded in recent events>",
  "escalatory_factors": ["<factor>", "<factor>", "<factor>", "<factor>"],
  "de_escalatory_factors": ["<factor>", "<factor>", "<factor>"],
  "contextual_factors": ["<factor>", "<factor>", "<factor>"],
  "signals": [
    { "outlet": "<name>", "headline": "<text>", "sentiment": "<escalatory|neutral|de-escalatory>", "time_ago": "<e.g. 2 days ago>" }
  ],
  "timeline": [
    { "date": "<e.g. Feb 2025>", "event": "<description max 80 chars>" }
  ]
}

Be calibrated. Baseline 30-day strike probability absent major provocation is 5-15%.
Ground everything in your web search results."""

USER_MESSAGE = """Search for the latest news (past 2-4 weeks) about:
1. US-Iran diplomatic relations and any negotiations
2. Iran nuclear program — enrichment levels, IAEA reports
3. US military posture in the Middle East (carrier groups, troop movements)
4. Iran-linked proxy activity: Houthis, Hezbollah, Iraq militias
5. Israeli military operations that could pull in the US
6. US or Israeli threats / ultimatums regarding Iran
7. Iranian retaliatory threats or actions
8. Any back-channel talks or diplomatic off-ramps

Then return your full structured JSON assessment."""

# ── Core analysis function ─────────────────────────────────────────────────────
def run_analysis():
    global cache

    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set!")
        cache["status"] = "error"
        cache["error"] = "ANTHROPIC_API_KEY environment variable is not set."
        return

    log.info("Running analysis...")
    cache["status"] = "pending"

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": USER_MESSAGE}]
        )

        # Extract text from response
        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        raw_text = "\n".join(text_blocks)

        # Parse JSON
        import re
        match = re.search(r"\{[\s\S]*\}", raw_text)
        if not match:
            raise ValueError("No JSON found in Claude response")

        parsed = json.loads(match.group(0))

        now = datetime.now(timezone.utc)
        next_run = datetime.fromtimestamp(
            now.timestamp() + REFRESH_INTERVAL_HOURS * 3600, tz=timezone.utc
        )

        cache["data"] = parsed
        cache["last_updated"] = now.isoformat()
        cache["next_update"] = next_run.isoformat()
        cache["status"] = "ok"
        cache["error"] = None

        log.info(f"Analysis complete. Probability 30d: {parsed.get('probability_30d')}% | Threat: {parsed.get('threat_level')}")

    except Exception as e:
        log.error(f"Analysis failed: {e}")
        cache["status"] = "error"
        cache["error"] = str(e)

# ── Background scheduler ───────────────────────────────────────────────────────
def scheduler_loop():
    """Run analysis immediately on start, then every N hours."""
    run_analysis()
    while True:
        time.sleep(REFRESH_INTERVAL_HOURS * 3600)
        run_analysis()

# Start background thread
scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
scheduler_thread.start()

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/api/assessment")
def assessment():
    return jsonify({
        "status": cache["status"],
        "last_updated": cache["last_updated"],
        "next_update": cache["next_update"],
        "error": cache["error"],
        "data": cache["data"],
    })

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "status": cache["status"]})

@app.route("/")
def index():
    return jsonify({"message": "Iran Monitor API", "endpoints": ["/api/assessment", "/api/health"]})

# ── Run ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
