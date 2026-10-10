import json
import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# nightly-harvest.sh (triplestore repo) appends one JSON line per run here --
# timing, per-pipeline status, and the resulting per-graph triple counts.
# Default assumes a sibling checkout, same layout as local dev; the VPS
# deploy overrides this via the RUN_HISTORY_PATH env var in kb-viewer.service
# since there `triplestore` is owned by a different system user (see
# triplestore/deploy/README.md).
RUN_HISTORY_PATH = Path(os.environ.get(
    "RUN_HISTORY_PATH", BASE_DIR.parent / "triplestore" / "run-history.jsonl"
))

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/run-history")
def run_history():
    if not RUN_HISTORY_PATH.exists():
        return jsonify([])
    runs = []
    with RUN_HISTORY_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except ValueError:
                continue
    runs.sort(key=lambda r: r.get("started_at", ""))
    return jsonify(runs)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
