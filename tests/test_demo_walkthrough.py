"""Offline tests for demo_walkthrough.py and the hybrid_ingest oversized-document guard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import demo_walkthrough as dw  # noqa: E402
import hybrid_ingest as hi  # noqa: E402

EXAMPLE = ROOT / "samples" / "walkthrough.example.json"


def _payload(*refs):
    return {"references": [{"title": t, "sourceData": {"contentKind": k, "sectionLabel": s, "content": "x"}}
                           for t, k, s in refs]}


def test_example_script_is_valid_and_covers_every_step_kind():
    script = dw.load_script(str(EXAMPLE))
    kinds = {s.get("expectKind") for s in script["steps"]}
    assert "image-description" in kinds, "the figure-only step is the one that proves the vision path"
    assert any(s.get("expectNone") for s in script["steps"]), "needs an out-of-corpus step"


def test_references_parses_both_response_shapes():
    refs = dw.references(_payload(("a.pdf", "text", "1.2 Intro")))
    assert refs[0]["title"] == "a.pdf" and refs[0]["section"] == "1.2 Intro"
    mcp_shape = {"response": [{"content": [{"text": json.dumps([
        {"title": "b.pdf", "contentKind": "image-description", "pageNumberFrom": 4}])}]}]}
    refs = dw.references(mcp_shape)
    assert refs[0]["kind"] == "image-description" and refs[0]["pageFrom"] == 4


def test_evaluate_rules():
    step = {"expectSource": "riscv-spec", "expectKind": "image-description"}
    ok, _ = dw.evaluate(step, dw.references(_payload(("riscv-spec__p0001-0100.pdf", "image-description", None))))
    assert ok, "split-part names must match the original document stem"
    ok, why = dw.evaluate(step, dw.references(_payload(("riscv-spec.pdf", "text", "3.2"))))
    assert not ok and "image-description" in why
    ok, _ = dw.evaluate(step, dw.references(_payload(("other.pdf", "image-description", None),
                                                      ("riscv-spec.pdf", "text", None))))
    assert not ok, "the figure must come from the expected document, not any document"
    assert dw.evaluate({"expectNone": True}, [])[0]
    assert not dw.evaluate({"expectNone": True}, dw.references(_payload(("a.pdf", "text", None))))[0]


def test_run_against_mocked_search(monkeypatch, capsys):
    import post_deploy_search as pds

    class Resp:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    answers = iter([_payload(("riscv-spec.pdf", "image-description", None)), {"references": []}])
    seen = []

    def fake_req(method, path, ids, key, body):
        seen.append((path, body["knowledgeSourceParams"][0]["knowledgeSourceName"]))
        return Resp(next(answers))

    monkeypatch.setattr(pds, "get_admin_key", lambda ids: "k")
    monkeypatch.setattr(hi, "_req", fake_req)
    script = {"steps": [{"title": "fig", "query": "q1", "expectSource": "riscv-spec", "expectKind": "image-description"},
                        {"title": "none", "query": "q2", "expectNone": True}]}
    results = dw.run({"searchEndpoint": "https://x"}, script, 3)
    assert [r["passed"] for r in results] == [True, True]
    assert seen[0] == ("/knowledgebases/kb-hybrid/retrieve", "ks-hybrid")
    report = dw.render_report(script, results, "now")
    assert "2/2 steps passed" in report and "| 1 | fig | ✅ pass |" in report


def test_present_needs_no_azure(capsys):
    dw.present(dw.load_script(str(EXAMPLE)))
    out = capsys.readouterr().out
    assert "SAY" in out and "EXPECT" in out


# ---- hybrid_ingest guard -------------------------------------------------

ROWS = [{"file": "small.pdf", "pages": 120}, {"file": "big.pdf", "pages": 906}]


def test_guard_blocks_oversized_upload_without_an_explicit_choice():
    with pytest.raises(SystemExit) as e:
        hi.oversized_guard(ROWS, split=False, no_split=False, uploading=True)
    assert "big.pdf (906 pages)" in str(e.value) and "--split" in str(e.value)


def test_guard_allows_explicit_choices_and_warns_on_plan(capsys):
    hi.oversized_guard(ROWS, split=False, no_split=True, uploading=True)
    hi.oversized_guard(ROWS, split=True, no_split=False, uploading=True)
    hi.oversized_guard(ROWS, split=False, no_split=False, uploading=False)
    assert "exceed" in capsys.readouterr().out
    hi.oversized_guard([ROWS[0]], split=False, no_split=False, uploading=True)
