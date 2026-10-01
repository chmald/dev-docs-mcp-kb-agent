# 11 — Customer walkthrough

A scripted, self-checking demo. Run it **before** the session to prove the story still lands,
then use `--present` **during** the session as the talk track.

> **Script:** [`scripts/demo_walkthrough.py`](../scripts/demo_walkthrough.py) ·
> **Example:** [`samples/walkthrough.example.json`](../samples/walkthrough.example.json) ·
> **Tests:** [`tests/test_demo_walkthrough.py`](../tests/test_demo_walkthrough.py) (offline)

---

## The story in five steps

[![Customer walkthrough](./assets/customer-walkthrough.png)](./assets/customer-walkthrough.png)

<sub>Editable source: [`assets/customer-walkthrough.drawio`](./assets/customer-walkthrough.drawio). The lower half of the diagram shows how a rehearsal run evaluates each step.</sub>

| # | Step | What it proves | Pass condition |
|---:|---|---|---|
| 1 | Prose lookup | Basic grounded retrieval | Expected document in the top 3, as `text` |
| 2 | Cross-document routing | No bleed between manuals | Expected document in the top 3 |
| 3 | **Figure-only answer** | The vision path works — **the moment the pattern sells itself** | An `image-description` reference **from the expected document** in the top 3 |
| 4 | Deep section | Citation granularity | Expected document in the top 3 |
| 5 | Out of corpus | No hallucinated answer | **No** references returned |

Step 3 is the one to rehearse. A pipeline that silently discarded every diagram still passes
steps 1, 2 and 4.

---

## Before the session

```bash
cd scripts
python demo_walkthrough.py --ids-file ../demo-ids.local.json --script ../samples/walkthrough.example.json --report
```

```
[PASS] Step 3 — Answer that only exists in a figure  (2.1s)
       What is the bit width of the mtime register and which bits does it span?
       ↳ riscv-spec__p0001-0100.pdf · image-description
...
5/5 steps passed
report: out/walkthrough-2026-09-30_101500.md
```

The script exits non-zero if any step fails, so it can also gate a pipeline. The report is a
Markdown table of results plus the top citations and snippets for each step, ready to attach
to a follow-up email.

## During the session

```bash
python demo_walkthrough.py --script ../samples/walkthrough.example.json --present
```

This prints each step's talk track (`SAY`), the question to type into Copilot Chat (`ASK`) and
what a correct answer cites (`EXPECT`). It makes no Azure calls, so it's safe to run on a
screen share.

---

## Write a walkthrough for your own corpus

Copy the example and replace the queries. Schema:

| Field | Required | Meaning |
|---|---|---|
| `title` | no | Heading for the presenter view and the report |
| `steps[].title` | no | Step name |
| `steps[].say` | no | Talk track for the presenter |
| `steps[].query` | **yes** | The question — type the same text into Copilot Chat |
| `steps[].expectSource` | no | Substring of the cited document name. Split parts (`manual__p0301-0600.pdf`) still match `manual` |
| `steps[].expectKind` | no | `text` or `image-description` |
| `steps[].expectNone` | no | `true` for the out-of-corpus step |

Keep at least one figure-only step and one out-of-corpus step. `--top N` changes how many
citations count toward a pass (default 3). To target a different knowledge base, set
`walkthroughKnowledgeBase` / `walkthroughKnowledgeSource` in the ids file; the defaults are the
hybrid build's `kb-hybrid` / `ks-hybrid`.

---

## If a step fails

| Failing step | Most likely cause | Where to look |
|---|---|---|
| 3 only | Vision skill produced no rows — throttling or a missing `api-version` | [09 § Failures that lie to you](./09-findings-and-lessons.md), [04 § B](./04-testing.md#b--chunk-quality-audit-do-not-skip) |
| 2 | A document didn't ingest (rejected or still indexing) | [04 § D tier provenance](./04-testing.md#d--tier-provenance-is-the-hybrid-actually-hybrid) |
| 5 | Reranker threshold too low for the corpus | `rerankerThreshold` in `post_deploy_search.retrieve_body` |
| All | Wrong knowledge base, or indexing not finished | `hybrid_ingest.py --status` |

---

*Last updated: 2026-09-30*
