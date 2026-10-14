import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, abort, jsonify, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Wikidata portraits for Person records: identity-etl links many of our
# authority/person records to a Wikidata Q-id via sdo:sameAs (see the
# "Also known as" panel in static/index.html). Wikidata's P18 (image) claim
# points at a Commons file, which we fetch once and cache here -- so the
# browser only ever hotlinks our own origin (same isLocalUri() policy every
# other image in this viewer already follows), not upload.wikimedia.org
# directly, and repeat page views don't re-hit Wikidata/Commons at all.
WIKIDATA_IMAGE_CACHE = BASE_DIR / "cache" / "wikidata-images"
WIKIDATA_IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
WIKIDATA_QID_RE = re.compile(r"^Q[1-9][0-9]*$")
WIKIDATA_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".svg")
# Wikimedia's API etiquette asks for a descriptive User-Agent identifying the
# tool and a contact -- an anonymous/default UA risks being throttled. Reused
# for RKD requests below too, same courtesy.
OUTBOUND_USER_AGENT = "iisg-kb-viewer/1.0 (https://kb.zijdeman.nl; claude@zijdeman.nl)"

# RKDartists portraits: Wikidata's P650 (RKDartists ID) claim -- present on
# the same Wikidata item the portrait above already came from -- gives us
# the numeric id RKD uses internally. research.rkd.nl's own detail API (the
# one the RKD Research website itself calls) then lists that artist's
# "depicted by" images as IIIF bases; we take the first openly licensed
# (accessStatus "NOLIM") one. Same local-cache-and-serve pattern as the
# Wikidata portrait: two small JSON/image caches below, keyed first by Q-id
# (to the RKD numeric id) then by that RKD id (to the portrait bytes), so a
# person with no RKDartists match or no public portrait only ever costs one
# lookup, ever.
RKD_ARTIST_CACHE = BASE_DIR / "cache" / "rkd-artist"
RKD_ARTIST_CACHE.mkdir(parents=True, exist_ok=True)
RKD_IMAGE_CACHE = BASE_DIR / "cache" / "rkd-images"
RKD_IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
RKD_ID_RE = re.compile(r"^[1-9][0-9]*$")

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


def _wikidata_entity_claims(qid):
    """All claims for a Q-id, in one request -- P18 (image) and P650
    (RKDartists ID) are both read off this same document by their
    respective callers below."""
    url = "https://www.wikidata.org/wiki/Special:EntityData/%s.json" % qid
    req = urllib.request.Request(url, headers={"User-Agent": OUTBOUND_USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return data.get("entities", {}).get(qid, {}).get("claims", {})


def _wikidata_image_filename(qid):
    """Look up the Commons filename behind a Q-id's P18 (image) claim, or
    None if it has no such claim."""
    p18 = _wikidata_entity_claims(qid).get("P18")
    if not p18:
        return None
    return p18[0]["mainsnak"]["datavalue"]["value"]


def _wikidata_rkd_artist_id(qid):
    """Look up a Q-id's P650 (RKDartists ID) claim, or None."""
    p650 = _wikidata_entity_claims(qid).get("P650")
    if not p650:
        return None
    return p650[0]["mainsnak"]["datavalue"]["value"]


def _download_commons_file(filename, dest_path):
    """Fetch a Commons file by name (Special:FilePath redirects to the
    actual CDN URL) straight into dest_path, atomically."""
    commons_url = "https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(filename)
    req = urllib.request.Request(commons_url, headers={"User-Agent": OUTBOUND_USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read()
    tmp_path = dest_path.with_name(dest_path.name + ".tmp")
    tmp_path.write_bytes(body)
    tmp_path.replace(dest_path)


@app.route("/api/wikidata-image/<qid>")
def wikidata_image(qid):
    if not WIKIDATA_QID_RE.match(qid):
        abort(400)

    none_marker = WIKIDATA_IMAGE_CACHE / (qid + ".none")
    if none_marker.exists():
        abort(404)
    for ext in WIKIDATA_IMAGE_EXTENSIONS:
        cached = WIKIDATA_IMAGE_CACHE / (qid + ext)
        if cached.exists():
            return send_from_directory(WIKIDATA_IMAGE_CACHE, cached.name, max_age=86400 * 30)

    try:
        filename = _wikidata_image_filename(qid)
        if not filename:
            none_marker.touch()
            abort(404)
        ext = Path(filename).suffix.lower()
        if ext not in WIKIDATA_IMAGE_EXTENSIONS:
            ext = ".jpg"
        dest = WIKIDATA_IMAGE_CACHE / (qid + ext)
        _download_commons_file(filename, dest)
        return send_from_directory(WIKIDATA_IMAGE_CACHE, dest.name, max_age=86400 * 30)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            none_marker.touch()
        abort(404)
    except urllib.error.URLError:
        abort(502)


@app.route("/api/rkd-artist/<qid>")
def rkd_artist(qid):
    if not WIKIDATA_QID_RE.match(qid):
        abort(400)

    none_marker = RKD_ARTIST_CACHE / (qid + ".none")
    if none_marker.exists():
        abort(404)
    cached = RKD_ARTIST_CACHE / (qid + ".json")
    if cached.exists():
        return send_from_directory(RKD_ARTIST_CACHE, cached.name, max_age=86400 * 30)

    try:
        rkd_id = _wikidata_rkd_artist_id(qid)
        if not rkd_id or not RKD_ID_RE.match(rkd_id):
            none_marker.touch()
            abort(404)
        tmp_path = cached.with_name(cached.name + ".tmp")
        tmp_path.write_text(json.dumps({"rkd_id": rkd_id}))
        tmp_path.replace(cached)
        return send_from_directory(RKD_ARTIST_CACHE, cached.name, max_age=86400 * 30)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            none_marker.touch()
        abort(404)
    except urllib.error.URLError:
        abort(502)


def _rkd_first_open_portrait_src(rkd_id):
    """The IIIF base URL of this RKD artist's first openly licensed
    (accessStatus "NOLIM") depiction, or None -- mirrors what
    research.rkd.nl's own artist page shows as the lead portrait.
    Subscription-only ("SUBONLY") depictions are skipped: media.rkd.nl
    enforces that restriction itself, so fetching one would just fail."""
    detail_url = "https://research.rkd.nl/api/detail?" + urllib.parse.urlencode(
        {"id": "https://data.rkd.nl/artists/" + rkd_id}
    )
    req = urllib.request.Request(detail_url, headers={"User-Agent": OUTBOUND_USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    for img in data.get("images", []):
        if img.get("accessStatus") == "NOLIM" and img.get("src"):
            return img["src"]
    return None


@app.route("/api/rkd-image/<rkd_id>")
def rkd_image(rkd_id):
    if not RKD_ID_RE.match(rkd_id):
        abort(400)

    none_marker = RKD_IMAGE_CACHE / (rkd_id + ".none")
    if none_marker.exists():
        abort(404)
    cached = RKD_IMAGE_CACHE / (rkd_id + ".jpg")
    if cached.exists():
        return send_from_directory(RKD_IMAGE_CACHE, cached.name, max_age=86400 * 30)

    try:
        iiif_base = _rkd_first_open_portrait_src(rkd_id)
        if not iiif_base:
            none_marker.touch()
            abort(404)
        image_url = iiif_base.rstrip("/") + "/full/400,/0/default.jpg"
        req = urllib.request.Request(image_url, headers={"User-Agent": OUTBOUND_USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
        tmp_path = cached.with_name(cached.name + ".tmp")
        tmp_path.write_bytes(body)
        tmp_path.replace(cached)
        return send_from_directory(RKD_IMAGE_CACHE, cached.name, max_age=86400 * 30)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            none_marker.touch()
        abort(404)
    except urllib.error.URLError:
        abort(502)


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
