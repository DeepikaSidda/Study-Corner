"""Amazon DynamoDB: every lecture with its notes, quiz, transcript and board readings.

One table, on-demand billing (pay per request, nothing when idle). Each
lecture is one partition (pk = "LECTURE#<id>") holding these items:

    sk = "META"            title, source, YouTube video, length, language, status, counts, folder
    sk = "NOTES"           summary, key points, sections, key terms, worked examples, metrics
    sk = "QUIZ#001" ...    one item per quiz question
    sk = "LINE#00001" ...  one item per transcript line (start, end, text), for search
    sk = "BOARD#00001" ... one item per board or slide reading

A small index partition (pk = "INDEX", sk = "<created>#<id>") lists every
lecture newest first, so the home page needs one Query instead of a Scan.

Large files (videos, frame pictures, PDFs) stay in the lecture folder; the
table stores the folder path. Settings: LECTURE_DDB_TABLE (default
study-corner-lectures). Set LECTURE_DDB=off to run without DynamoDB.
"""

from __future__ import annotations

import json
import os
import threading
import time
from decimal import Decimal

from . import config

TABLE = os.environ.get("LECTURE_DDB_TABLE", "study-corner-lectures")
ENABLED = os.environ.get("LECTURE_DDB", "on").lower() not in ("off", "0", "false", "no")

_lock = threading.Lock()
_table = None


class DatabaseError(RuntimeError):
    pass


# ----------------------------------------------------------------- helpers
def _to_ddb(value):
    """DynamoDB needs Decimal instead of float and has no empty-set types."""
    return json.loads(json.dumps(value, ensure_ascii=False), parse_float=Decimal)


def _from_ddb(value):
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def table():
    """The table, created on first use (on-demand billing)."""
    global _table
    if not ENABLED:
        raise DatabaseError("DynamoDB is switched off (LECTURE_DDB=off)")
    with _lock:
        if _table is not None:
            return _table
        resource = config.session().resource("dynamodb")
        client = resource.meta.client
        try:
            client.describe_table(TableName=TABLE)
        except client.exceptions.ResourceNotFoundException:
            client.create_table(
                TableName=TABLE,
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                      {"AttributeName": "sk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
                Tags=[{"Key": "app", "Value": "study-corner"}],
            )
            client.get_waiter("table_exists").wait(TableName=TABLE, WaiterConfig={"Delay": 2, "MaxAttempts": 60})
        _table = resource.Table(TABLE)
        return _table


def _pk(job_id: str) -> str:
    return f"LECTURE#{job_id}"


def _query_all(**kwargs) -> list[dict]:
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = table().query(**kwargs)
        items += page.get("Items", [])
        start = page.get("LastEvaluatedKey")
        if not start:
            return items


# ------------------------------------------------------------------- write
def _meta(job: dict, data: dict | None) -> dict:
    source = job.get("source") or {}
    video = source.get("video") or {}
    metrics = (data or {}).get("metrics", {})
    notes = (data or {}).get("notes", {})
    return {
        "id": job["id"],
        "title": notes.get("title") or job.get("original_name", ""),
        "original_name": job.get("original_name", ""),
        "status": job.get("status", ""),
        "created": job.get("created", 0),
        "finished": job.get("finished") or 0,
        "kind": job.get("kind", ""),
        "source_type": source.get("type", "upload"),
        "source_mode": source.get("mode", "upload" if not source else "download"),
        "youtube_id": video.get("id", ""),
        "youtube_url": video.get("url", ""),
        "channel": video.get("channel", ""),
        "license": video.get("license", ""),
        "seconds": metrics.get("lecture_seconds") or video.get("seconds") or 0,
        "transcript_source": metrics.get("transcript_source", ""),
        "sections": len(notes.get("sections", [])),
        "questions": len((data or {}).get("quiz", [])),
        "board_readings": sum(1 for b in (data or {}).get("board", []) if b.get("has_content")),
        "transcript_lines": len((data or {}).get("segments", [])),
        "coverage": (metrics.get("notes") or {}).get("coverage", 0),
        "folder": f"output/web/{job['id']}",
        "error": job.get("error") or "",
    }


def save_lecture(job: dict, data: dict | None = None) -> int:
    """Write (or rewrite) one lecture. data is the lecture.json content, or None for a job
    that hasn't finished. Returns the number of items written."""
    pk = _pk(job["id"])
    meta = _meta(job, data)
    items = [{"pk": pk, "sk": "META", **meta}]
    if data:
        notes = dict(data["notes"])
        items.append({"pk": pk, "sk": "NOTES", "notes": notes, "metrics": data.get("metrics", {})})
        for n, q in enumerate(data.get("quiz", []), start=1):
            items.append({"pk": pk, "sk": f"QUIZ#{n:03d}", "n": n, **q})
        for seg in data.get("segments", []):
            items.append({"pk": pk, "sk": f"LINE#{seg['id']:05d}", **seg, "text_lc": seg["text"].lower()})
        for n, b in enumerate(data.get("board", []), start=1):
            items.append({"pk": pk, "sk": f"BOARD#{n:05d}", "n": n, **b,
                          "text_lc": (b.get("text") or "").lower()})

    t = table()
    # Remove items from an older version of this lecture that the new one doesn't have.
    keep = {i["sk"] for i in items}
    old = _query_all(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": pk},
                     ProjectionExpression="sk")
    with t.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
        for o in old:
            if o["sk"] not in keep:
                batch.delete_item(Key={"pk": pk, "sk": o["sk"]})
        for item in items:
            batch.put_item(Item=_to_ddb(item))
        # Index entry for the home page list, newest first.
        for o in _query_all(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": "INDEX"},
                            ProjectionExpression="sk, id"):
            if o.get("id") == job["id"] and o["sk"] != _index_sk(job):
                batch.delete_item(Key={"pk": "INDEX", "sk": o["sk"]})
        batch.put_item(Item=_to_ddb({"pk": "INDEX", "sk": _index_sk(job), **meta}))
    return len(items) + 1


def _index_sk(job: dict) -> str:
    return f"{job.get('created', 0):017.6f}#{job['id']}"


def update_status(job: dict) -> None:
    """Keep status and error current while a job runs (cheap: two small writes)."""
    meta = _meta(job, None)
    t = table()
    names = {"#s": "status", "#e": "error", "#f": "finished"}
    values = _to_ddb({":s": meta["status"], ":e": meta["error"], ":f": meta["finished"]})
    for key in ({"pk": _pk(job["id"]), "sk": "META"}, {"pk": "INDEX", "sk": _index_sk(job)}):
        try:
            t.update_item(Key=key, UpdateExpression="SET #s = :s, #e = :e, #f = :f",
                          ConditionExpression="attribute_exists(pk)",
                          ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        except t.meta.client.exceptions.ConditionalCheckFailedException:
            if key["sk"] == "META":
                save_lecture(job, None)  # first time we see this job
                return


def delete_lecture(job_id: str) -> int:
    pk = _pk(job_id)
    keys = _query_all(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": pk},
                      ProjectionExpression="pk, sk")
    index = [o for o in _query_all(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": "INDEX"},
                                   ProjectionExpression="pk, sk, id") if o.get("id") == job_id]
    with table().batch_writer() as batch:
        for k in keys + index:
            batch.delete_item(Key={"pk": k["pk"], "sk": k["sk"]})
    return len(keys) + len(index)


# -------------------------------------------------------------------- read
def list_lectures(limit: int = 50) -> list[dict]:
    """Every lecture, newest first (one Query on the index partition)."""
    page = table().query(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": "INDEX"},
                         ScanIndexForward=False, Limit=limit)
    return [_from_ddb({k: v for k, v in i.items() if k not in ("pk", "sk")}) for i in page.get("Items", [])]


def get_lecture(job_id: str) -> dict | None:
    """The lecture rebuilt in the same shape as lecture.json, or None if it isn't stored."""
    items = _query_all(KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": _pk(job_id)})
    by_kind: dict[str, list] = {}
    for item in items:
        by_kind.setdefault(item["sk"].split("#")[0], []).append(_from_ddb(item))
    if "NOTES" not in by_kind:
        return None
    strip = ("pk", "sk", "n", "text_lc")
    quiz = sorted(by_kind.get("QUIZ", []), key=lambda i: i["sk"])
    lines = sorted(by_kind.get("LINE", []), key=lambda i: i["sk"])
    board = sorted(by_kind.get("BOARD", []), key=lambda i: i["sk"])
    notes_item = by_kind["NOTES"][0]
    return {
        "notes": notes_item["notes"],
        "quiz": [{k: v for k, v in q.items() if k not in strip} for q in quiz],
        "segments": [{k: v for k, v in s.items() if k not in strip} for s in lines],
        "board": [{k: v for k, v in b.items() if k not in strip} for b in board],
        "metrics": notes_item.get("metrics", {}),
    }


def search(query: str, limit: int = 20) -> list[dict]:
    """Lectures whose transcript, board or title mention the words in `query`.

    Uses a Scan with a filter, which reads the whole table: fine for a class's
    lectures, and the one place to change (e.g. to OpenSearch) if it grows.
    """
    words = [w for w in query.lower().split() if len(w) >= 2][:5]
    if not words:
        return []
    from boto3.dynamodb.conditions import Attr

    condition = None
    for w in words:
        part = Attr("text_lc").contains(w)
        condition = part if condition is None else condition & part
    hits: dict[str, list] = {}
    kwargs = {"FilterExpression": condition, "ProjectionExpression": "pk, sk, #st, #t",
              "ExpressionAttributeNames": {"#st": "start", "#t": "text"}}
    start = None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = table().scan(**kwargs)
        for item in page.get("Items", []):
            hits.setdefault(item["pk"].split("#", 1)[1], []).append(_from_ddb(item))
        start = page.get("LastEvaluatedKey")
        if not start:
            break
    lectures = {m["id"]: m for m in list_lectures(200)}
    out = []
    for job_id, found in hits.items():
        meta = lectures.get(job_id)
        if not meta:
            continue
        found.sort(key=lambda i: (i["sk"][:4] != "LINE", i.get("start", 0)))
        out.append({"lecture": meta, "matches": len(found),
                    "first": [{"kind": "board" if i["sk"].startswith("BOARD") else "line",
                               "start": i.get("start"), "text": i.get("text", "")[:200]} for i in found[:3]]})
    for meta in lectures.values():  # title matches with no text match
        if meta["id"] not in hits and all(w in meta.get("title", "").lower() for w in words):
            out.append({"lecture": meta, "matches": 0, "first": []})
    out.sort(key=lambda r: r["matches"], reverse=True)
    return out[:limit]


def counts() -> dict:
    """How many items of each kind the table holds (for checks)."""
    totals: dict[str, int] = {}
    start = None
    kwargs = {"ProjectionExpression": "sk"}
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = table().scan(**kwargs)
        for item in page.get("Items", []):
            kind = item["sk"].split("#")[0] if not item["sk"][0].isdigit() else "INDEX"
            totals[kind] = totals.get(kind, 0) + 1
        start = page.get("LastEvaluatedKey")
        if not start:
            return totals


def safe(fn, *args, log=print, **kwargs):
    """Run a database call; a database problem must never break a lecture."""
    if not ENABLED:
        return None
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                log(f"database: {fn.__name__} failed ({type(exc).__name__}: {str(exc)[:120]})")
                return None
            time.sleep(1 + attempt)
