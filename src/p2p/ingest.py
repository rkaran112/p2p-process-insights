"""Download the event log and flatten it from XES into two Parquet tables.

XES is XML: one <trace> per purchase-order line, carrying case attributes,
then one <event> per step.  The file is 700 MB, so it is streamed with
iterparse and each trace is freed as soon as it has been read.

    events.parquet  case_id, activity, ts, user, net_worth_eur, event_idx
    cases.parquet   case_id plus every case attribute in the log
"""
from __future__ import annotations

import hashlib
import shutil
import urllib.request
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

from . import config as C

# XES key -> column name.  Anything not listed is ignored.
CASE_KEYS = {
    "concept:name": "case_id",
    "Purchasing Document": "po_document",
    "Item": "po_item",
    "Item Type": "item_type",
    "GR-Based Inv. Verif.": "gr_based_iv",
    "Goods Receipt": "goods_receipt_required",
    "Source": "source_system",
    "Purch. Doc. Category name": "doc_category",
    "Company": "company",
    "Spend classification text": "spend_class",
    "Spend area text": "spend_area",
    "Sub spend area text": "sub_spend_area",
    "Vendor": "vendor",
    "Name": "vendor_name",
    "Document Type": "document_type",
    "Item Category": "item_category",
}
EVENT_KEYS = {
    "concept:name": "activity",
    "time:timestamp": "ts",
    "User": "user",
    "Cumulative net worth (EUR)": "net_worth_eur",
}


def md5(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def fetch(force: bool = False) -> Path:
    """Download the XES file once, and refuse a file whose checksum is wrong."""
    dest = C.RAW_DIR / C.XES_FILENAME
    if dest.exists() and not force:
        if md5(dest) == C.XES_MD5:
            print(f"  {dest.name} present, checksum ok")
            return dest
        print(f"  {dest.name} present but checksum differs - downloading again")
    part = dest.with_suffix(".xes.part")
    print(f"  downloading {C.XES_URL}")
    with urllib.request.urlopen(C.XES_URL) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    got = md5(part)
    if got != C.XES_MD5:
        raise RuntimeError(f"checksum mismatch: expected {C.XES_MD5}, got {got}")
    part.replace(dest)
    return dest


def _value(el: etree._Element):
    tag = etree.QName(el).localname
    v = el.get("value")
    if tag == "boolean":
        return v == "true"
    if tag == "float":
        return float(v)
    return v


EVENT_SCHEMA = pa.schema([
    ("case_id", pa.string()),
    ("event_idx", pa.int32()),
    ("activity", pa.string()),
    ("ts_raw", pa.string()),
    ("user", pa.string()),
    ("net_worth_eur", pa.float64()),
])


def parse_xes(path: Path, batch_traces: int = 20_000) -> tuple[int, int]:
    """Stream the XES file into events.parquet and cases.parquet."""
    writer = pq.ParquetWriter(C.EVENTS_PARQUET, EVENT_SCHEMA)
    cases: list[dict] = []
    buf: dict[str, list] = {n: [] for n in EVENT_SCHEMA.names}
    n_traces = n_events = 0

    def flush():
        if buf["case_id"]:
            writer.write_table(pa.table(buf, schema=EVENT_SCHEMA))
            for v in buf.values():
                v.clear()

    for _, trace in etree.iterparse(str(path), events=("end",),
                                    tag="{*}trace", huge_tree=True):
        case: dict = {}
        for child in trace:
            name = etree.QName(child).localname
            if name == "event":
                continue
            key = child.get("key")
            if key in CASE_KEYS:
                case[CASE_KEYS[key]] = _value(child)
        cid = case["case_id"]
        idx = 0
        for ev in trace.iterchildren("{*}event", "event"):
            rec = {"user": None, "net_worth_eur": None, "activity": None,
                   "ts": None}
            for a in ev:
                key = a.get("key")
                if key in EVENT_KEYS:
                    rec[EVENT_KEYS[key]] = _value(a)
            buf["case_id"].append(cid)
            buf["event_idx"].append(idx)
            buf["activity"].append(rec["activity"])
            buf["ts_raw"].append(rec["ts"])
            buf["user"].append(rec["user"])
            buf["net_worth_eur"].append(rec["net_worth_eur"])
            idx += 1
        n_events += idx
        cases.append(case)
        n_traces += 1
        trace.clear()
        while trace.getprevious() is not None:
            del trace.getparent()[0]
        if n_traces % batch_traces == 0:
            flush()
            print(f"    {n_traces:,} cases / {n_events:,} events")
    flush()
    writer.close()
    pd.DataFrame(cases).to_parquet(C.CASES_PARQUET, index=False)
    return n_traces, n_events


def ensure_parquet(force: bool = False) -> dict:
    """Fetch and flatten the log unless the Parquet tables already exist."""
    if force or not (C.EVENTS_PARQUET.exists() and C.CASES_PARQUET.exists()):
        xes = fetch()
        print("  flattening XES -> Parquet (one-off, a few minutes)")
        parse_xes(xes)
    ev = pq.ParquetFile(C.EVENTS_PARQUET).metadata.num_rows
    cs = pq.ParquetFile(C.CASES_PARQUET).metadata.num_rows
    return {"n_cases": int(cs), "n_events": int(ev),
            "source": C.DATASET_DOI, "xes_md5": C.XES_MD5}
