import os
import json
import time
import logging
import threading
import re
from datetime import datetime, timezone
import anthropic
from flask import Flask, jsonify
from flask_cors import CORS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

REFRESH_INTERVAL_HOURS = int(os.environ.get("REFRESH_INTERVAL_HOURS", "4"))

cache = {
    "data": None,
    "last_updated": None,
    "next_update": None,
    "status": "pending",
    "error": None,
}

SYSTEM_PROMPT = """You are a geopolitical intelligence analyst specializing in Middle East affairs.
Return a JSON object with EXACTLY this structure (no markdown, ONLY JSON):
{
  "probability_30d": <integer 0-100>,
  "probability_90d": <integer 0-100>,
  "confidence": <integer 0-100>,
  "threat_level": "<LOW|MODERATE|ELEVATED|HIGH|CRITICAL>",
  "key_driver": "<most important factor, max 100 chars>",
  "analyst_summary": "<2-3 paragraph summary>",
  "escalatory_factors": ["<factor>","<factor>","<factor>","<factor>"],
  "de_escalatory_factors": ["<factor>","<factor>","<factor>"],
  "contextual_factors": ["<factor>","<factor>","<factor>"],
  "signals": [{"outlet":"<n>","headline":"<text>","sentiment":"<escalatory|neutral|de-escalatory>","time_ago":"<e.g. 2 days ago>"}],
  "timeline": [{"date":"<e.g. Feb 2025>","event":"<description max 80 chars>"}]
}
Baseline 30-day strike probability absent major provocation is 5-15%."""

USER_MESSAGE = """Search for latest news (past 2-4 weeks) on US-Iran relations, Iran nuclear program, US military posture in Middle East, Iran proxy activity, and any threats or diplomatic developments. Return full structured JSON assessment."""

def get_api_key():
    """Try every possible way to get the API key."""
    # Method 1: standard
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    
    # Method 2: os.getenv
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if key:
        return key.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    
    # Method 3: scan all env vars case-insensitively
    for k, v in os.environ.items():
        if k.upper() == "ANTHROPIC_API_KEY" and v:
            return v.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    
    return ""

def run_analysis():
    global cache

    api_key = get_api_key()
    log.info(f"API key check — length: {len(api_key)}, starts_with_sk: {api_key.startswith('sk-') if api_key else False}")

    if not api_key:
        log.error("No API key found in environment!")
        # Log all env var NAMES (not values) for debugging
        env_keys = list(os.environ.keys())
        log.info(f"Available env var names: {env_keys}")
        cache["status"] = "error"
        cache["error"] = f"ANTHROPIC_API_KEY not found. Available vars: {env_keys}"
        return

    log.info("Running analysis...")
    cache["status"] = "running"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": USER_MESSAGE}]
        )

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        raw_text = "\n".join(text_blocks)

        match = re.search(r"\{[\s\S]*\}", raw_text)
        if not match:
            raise ValueError("No JSON found in response")

        parsed = json.loads(match.group(0))
        now = datetime.now(timezone.utc)
        next_run = datetime.fromtimestamp(now.timestamp() + REFRESH_INTERVAL_HOURS * 3600, tz=timezone.utc)

        cache.update({
            "data": parsed,
            "last_updated": now.isoformat(),
            "next_update": next_run.isoformat(),
            "status": "ok",
            "error": None,
        })
        log.info(f"Done! 30d: {parsed.get('probability_30d')}% | {parsed.get('threat_level')}")

    except Exception as e:
        log.error(f"Analysis failed: {e}")
        cache["status"] = "error"
        cache["error"] = str(e)

def scheduler_loop():
    run_analysis()
    while True:
        time.sleep(REFRESH_INTERVAL_HOURS * 3600)
        run_analysis()

threading.Thread(target=scheduler_loop, daemon=True).start()

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
    api_key = get_api_key()
    return jsonify({
        "ok": True,
        "status": cache["status"],
        "api_key_set": bool(api_key),
        "api_key_length": len(api_key),
        "api_key_valid_format": api_key.startswith("sk-ant-") if api_key else False,
        "all_env_var_names": list(os.environ.keys()),
    })

@app.route("/")
def index():
    return jsonify({"message": "Iran Monitor API"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
