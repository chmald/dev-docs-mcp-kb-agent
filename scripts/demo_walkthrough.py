#!/usr/bin/env python3
"""Scripted customer walkthrough: prepared prompts, expected citations, pass/fail.

Run it before a customer session to prove the story still lands, and use
`--present` during the session as the presenter's talk track. Each step names a
query, what the audience should hear, and what a correct answer must cite:

    expectSource   substring of the cited document name (split parts such as
                   `manual__p0301-0600.pdf` still match `manual`)
    expectKind     `text` or `image-description` -- the figure step MUST be
                   answered from an image-description row, or the figure path
                   is silently broken
    expectNone     true for the out-of-corpus step: success is NO references

Usage:
    python demo_walkthrough.py --script ../samples/walkthrough.example.json --present
    python demo_walkthrough.py --ids-file ../demo-ids.local.json --script ../samples/walkthrough.example.json
    python demo_walkthrough.py --ids-file ../demo-ids.local.json --script ../samples/walkthrough.example.json --report
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT_DIR = Path(__file__).resolve().parent.parent / "out"


def load_script(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    steps = data.get("steps") or []
    if not steps:
        raise SystemExit(f"  ! {path} has no steps")
    for i, s in enumerate(steps, start=1):
        if "query" not in s:
            raise SystemExit(f"  ! step {i} has no 'query'")
        if s.get("expectKind") not in (None, "text", "image-description"):
            raise SystemExit(f"  ! step {i}: expectKind must be 'text' or 'image-description'")
    return data


def references(payload: dict) -> list[dict]:
    """Normalise both knowledge-base response shapes into
    [{title, kind, section, pageFrom, pageTo, snippet}] in ranked order."""
    out = []
    for r in payload.get("references") or []:
        sd = r.get("sourceData") or {}
        out.append({
            "title": r.get("title") or sd.get("sourceDocument"),
            "kind": sd.get("contentKind"),
            "section": sd.get("sectionLabel"),
            "pageFrom": sd.get("pageNumberFrom"),
            "pageTo": sd.get("pageNumberTo"),
            "snippet": " ".join(str(sd.get("content") or "").split())[:220],
        })
    if out:
        return out
    try:
        for r in json.loads(payload["response"][0]["content"][0]["text"]):
            out.append({"title": r.get("title") or r.get("sourceDocument"), "kind": r.get("contentKind"),
                        "section": r.get("sectionLabel"), "pageFrom": r.get("pageNumberFrom"),
                        "pageTo": r.get("pageNumberTo"),
                        "snippet": " ".join(str(r.get("content") or "").split())[:220]})
    except Exception:
        pass
    return out


def evaluate(step: dict, refs: list[dict], top_n: int = 3) -> tuple[bool, str]:
    if step.get("expectNone"):
        return (not refs, "no references returned" if not refs else f"{len(refs)} reference(s) returned")
    if not refs:
        return False, "no references returned"
    top = refs[:top_n]
    want_src = step.get("expectSource")
    want_kind = step.get("expectKind")
    src_ok = not want_src or any(want_src in (r["title"] or "") for r in top)
    kind_ok = not want_kind or any(r["kind"] == want_kind and (not want_src or want_src in (r["title"] or ""))
                                   for r in top)
    why = []
    if not src_ok:
        why.append(f"expected source '{want_src}' not in top {top_n}")
    if not kind_ok:
        why.append(f"no '{want_kind}' reference from the expected source in top {top_n}")
    return src_ok and kind_ok, "; ".join(why) or "citations match"


def _cite(r: dict) -> str:
    where = r.get("section") or ""
    if not where and r.get("pageFrom") is not None:
        where = f"p. {r['pageFrom']}"
        if r.get("pageTo") not in (None, r["pageFrom"]):
            where += f"–{r['pageTo']}"
    return f"{r['title']}" + (f" · {where}" if where else "") + (f" · {r['kind']}" if r.get("kind") else "")


def present(script: dict) -> None:
    print(f"\n{script.get('title', 'Customer walkthrough')}\n" + "=" * 72)
    for i, s in enumerate(script["steps"], start=1):
        print(f"\nStep {i} — {s.get('title', '')}")
        if s.get("say"):
            print(textwrap.indent(textwrap.fill(s["say"], 68), "  SAY  "))
        print(f"  ASK  {s['query']}")
        exp = ("no references (honest 'not in the corpus')" if s.get("expectNone") else
               f"cites {s.get('expectSource', 'any source')}" + (f" via {s['expectKind']}" if s.get("expectKind") else ""))
        print(f"  EXPECT  {exp}")


def run(ids: dict, script: dict, top_n: int) -> list[dict]:
    import post_deploy_search as pds
    from hybrid_ingest import _req, h_names

    names = h_names(ids)
    kb = ids.get("walkthroughKnowledgeBase", names["knowledgeBase"])
    ks = ids.get("walkthroughKnowledgeSource", names["knowledgeSource"])
    key = pds.get_admin_key(ids)
    results = []
    for i, s in enumerate(script["steps"], start=1):
        body = pds.retrieve_body(ids, s["query"])
        body["knowledgeSourceParams"][0]["knowledgeSourceName"] = ks
        started = time.time()
        resp = _req("POST", f"/knowledgebases/{kb}/retrieve", ids, key, body)
        elapsed = round(time.time() - started, 2)
        if resp.status_code >= 400:
            refs, ok, why = [], False, f"HTTP {resp.status_code}: {resp.text[:160]}"
        else:
            refs = references(resp.json())
            ok, why = evaluate(s, refs, top_n)
        results.append({"step": i, "title": s.get("title", ""), "query": s["query"], "passed": ok,
                        "reason": why, "seconds": elapsed, "references": refs[:top_n]})
        mark = "PASS" if ok else "FAIL"
        print(f"\n[{mark}] Step {i} — {s.get('title', '')}  ({elapsed}s)")
        print(f"       {s['query']}")
        for r in refs[:top_n]:
            print(f"       ↳ {_cite(r)}")
        if not ok:
            print(f"       ! {why}")
    return results


def _esc(s) -> str:
    return str(s).replace("|", "\\|")


def render_report(script: dict, results: list[dict], when: str) -> str:
    passed = sum(1 for r in results if r["passed"])
    L = [f"# Walkthrough report — {script.get('title', 'Customer walkthrough')}", "",
         f"Run {when} · **{passed}/{len(results)} steps passed**", "",
         "| # | Step | Result | Top citation | Seconds |", "|---:|---|---|---|---:|"]
    for r in results:
        top = _esc(_cite(r["references"][0])) if r["references"] else "—"
        result = "✅ pass" if r["passed"] else "❌ " + _esc(r["reason"])
        L.append(f"| {r['step']} | {_esc(r['title'])} | {result} | {top} | {r['seconds']} |")
    L.append("")
    for r in results:
        L += [f"## Step {r['step']} — {r['title']}", "", f"> {r['query']}", ""]
        if r["references"]:
            L += ["| Rank | Citation | Snippet |", "|---:|---|---|"]
            for n, ref in enumerate(r["references"], start=1):
                L.append(f"| {n} | {_esc(_cite(ref))} | {_esc(ref['snippet'])} |")
        else:
            L.append("*No references returned.*")
        L.append("")
    return "\n".join(L)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--script", required=True, help="Walkthrough JSON (see samples/walkthrough.example.json)")
    p.add_argument("--ids-file", help="Required unless --present")
    p.add_argument("--present", action="store_true", help="Print the talk track only -- no Azure calls")
    p.add_argument("--report", action="store_true", help="Also write a Markdown report to out/")
    p.add_argument("--top", type=int, default=3, help="Citations considered per step (default 3)")
    args = p.parse_args()

    script = load_script(args.script)
    if args.present:
        present(script)
        return
    if not args.ids_file:
        p.error("--ids-file is required unless --present")
    import post_deploy_search as pds
    results = run(pds.load_ids(args.ids_file), script, args.top)
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{passed}/{len(results)} steps passed")
    if args.report:
        now = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        OUT_DIR.mkdir(exist_ok=True)
        path = OUT_DIR / f"walkthrough-{now}.md"
        path.write_text(render_report(script, results, now), encoding="utf-8")
        print(f"report: {path}")
    if passed != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
