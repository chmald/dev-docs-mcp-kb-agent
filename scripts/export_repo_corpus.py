#!/usr/bin/env python3
"""Export technical PDFs as a repository-committable, agent-friendly corpus.

The search-index path (hybrid_ingest.py) answers questions *about* a document
from inside Copilot Chat. This script produces the other artifact teams ask
for: the document itself, converted into files that live next to the code --
per-section Markdown, the original figures as image files, Mermaid renderings
where a diagram can be expressed as text, and (optionally) structured JSON for
register maps, pin tables and electrical characteristics. Everything a coding
assistant reads from a working tree, with every passage traceable to a page in
the source PDF.

The two stages are split on purpose -- one costs money, the other is free:

    --analyze   (Azure, billable)  Document Intelligence prebuilt-layout, Markdown
                                   output + cropped figure images, cached locally.
                --describe-figures (Azure, billable, optional) a vision model
                                   describes each figure and, where the figure is
                                   a flow/block/state diagram, drafts Mermaid.
    --export    (local, free)      Cached results -> repository package. Re-run as
                                   often as you like; no service is called.

Split parts produced by `hybrid_ingest.py --split` (`manual__p0301-0600.pdf`)
are recognised and stitched back into ONE package with page numbers restored to
the ORIGINAL document, so a citation never silently becomes part-relative.

Usage:
    python export_repo_corpus.py --ids-file ../demo-ids.local.json --analyze --source-dir ../samples/corpus
    python export_repo_corpus.py --ids-file ../demo-ids.local.json --describe-figures
    python export_repo_corpus.py --export --out ../out/repo-export
    python export_repo_corpus.py --ids-file ../demo-ids.local.json --export --extract registers,pins
"""
from __future__ import annotations

import argparse
import base64
import bisect
import datetime as _dt
import hashlib
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

EXPORTER_VERSION = "1.0.0"

# Document Intelligence v4.0 GA.
DI_API_VERSION = "2024-11-30"
DI_MODEL = "prebuilt-layout"
COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"

# Mermaid's bit-field diagram. The documented keyword is now `packet` (Mermaid
# v11.0+), but every version that has the diagram still accepts `packet-beta`,
# and GitHub does not pin or publish its Mermaid version -- so `packet-beta` is
# the most compatible choice. Every packet diagram is ALSO emitted as a Markdown
# table: the table is the source of truth, the diagram is the visual.
MERMAID_PACKET_KEYWORD = "packet-beta"

# Only these Mermaid headers are accepted from the vision model. Anything else
# (or anything malformed) is dropped rather than committed as a broken diagram.
ALLOWED_MERMAID_HEADERS = ("flowchart", "graph", "stateDiagram-v2", "stateDiagram", "sequenceDiagram")

KNOWN_EXTRACTORS = ("registers", "pins", "electrical")

DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "out" / "export-cache"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "out" / "repo-export"

PART_RE = re.compile(r"^(?P<stem>.+)__p(?P<first>\d{4})-(?P<last>\d{4})$")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
NUMBERED_RE = re.compile(r"^(?:chapter|section|appendix)?\s*(\d+(?:\.\d+)*)\.?\s+\S", re.I)
DI_NOISE_RE = re.compile(r"^[ \t]*<!-- (?:PageHeader|PageFooter|PageNumber)=.*?-->[ \t]*\n?|^[ \t]*<!-- PageBreak -->[ \t]*\n?", re.M)
PAGE_MARK_RE = re.compile(r"<!-- page: (\d+) -->")
FIGURE_BLOCK_RE = re.compile(r"<figure>(.*?)</figure>", re.S)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "section"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def part_info(stem: str) -> tuple[str, int]:
    """(document stem, page offset). `manual__p0301-0600` -> ('manual', 300)."""
    m = PART_RE.match(stem)
    if not m:
        return stem, 0
    return m.group("stem"), int(m.group("first")) - 1


def _clean_label(text: str, limit: int = 60) -> str:
    """Characters Mermaid mindmap/flowchart labels tolerate everywhere."""
    s = re.sub(r"[^\w .,:/&+-]", "", text or "").strip()
    s = re.sub(r"\s+", " ", s)
    return (s[: limit - 1] + "…") if len(s) > limit else (s or "untitled")


def _md_cell(text) -> str:
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------------------
# stage 1: analyze (Azure)
# --------------------------------------------------------------------------

def _token() -> str:
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential().get_token(COGNITIVE_SCOPE).token


def _di_endpoint(ids: dict) -> str:
    ep = ids.get("documentIntelligenceEndpoint")
    if not ep:
        raise SystemExit("  ! 'documentIntelligenceEndpoint' is missing from the ids file.")
    return ep.rstrip("/")


def analyze_pdf(endpoint: str, pdf: Path, token: str, poll_seconds: float = 3.0,
                timeout_seconds: float = 1800.0) -> tuple[str, dict]:
    import requests

    # stringIndexType=unicodeCodePoint makes span offsets line up with Python str
    # indices. The service default (textElements, grapheme clusters) drifts on any
    # multi-code-point character, which would misplace figures and page markers.
    url = (f"{endpoint}/documentintelligence/documentModels/{DI_MODEL}:analyze"
           f"?api-version={DI_API_VERSION}&outputContentFormat=markdown&output=figures"
           f"&stringIndexType=unicodeCodePoint")
    body = {"base64Source": base64.b64encode(pdf.read_bytes()).decode("ascii")}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=body, timeout=300)
    if r.status_code != 202:
        raise RuntimeError(f"analyze {pdf.name}: {r.status_code} {r.text[:500]}")
    op = r.headers["Operation-Location"]
    result_id = op.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    started = time.time()
    while True:
        time.sleep(poll_seconds)
        s = requests.get(op, headers={"Authorization": f"Bearer {token}"}, timeout=120)
        s.raise_for_status()
        payload = s.json()
        state = payload.get("status")
        if state == "succeeded":
            return result_id, payload["analyzeResult"]
        if state == "failed":
            raise RuntimeError(f"analyze {pdf.name} failed: {json.dumps(payload.get('error'))[:500]}")
        if time.time() - started > timeout_seconds:
            raise RuntimeError(f"analyze {pdf.name}: timed out after {timeout_seconds:.0f}s")


def download_figure(endpoint: str, result_id: str, figure_id: str, token: str) -> bytes:
    import requests

    url = (f"{endpoint}/documentintelligence/documentModels/{DI_MODEL}/analyzeResults/"
           f"{result_id}/figures/{figure_id}?api-version={DI_API_VERSION}")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    r.raise_for_status()
    return r.content


def analyze_corpus(ids: dict, source_dir: Path, cache_dir: Path, force: bool = False) -> None:
    endpoint = _di_endpoint(ids)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(source_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"  ! no PDFs in {source_dir}")
    token = _token()
    for pdf in pdfs:
        target = cache_dir / f"{pdf.stem}.analyze.json"
        digest = sha256_file(pdf)
        if target.exists() and not force:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if cached.get("sha256") == digest:
                print(f"  cached    {pdf.name}")
                continue
        print(f"  analyzing {pdf.name} ...")
        result_id, result = analyze_pdf(endpoint, pdf, token)
        fig_dir = cache_dir / f"{pdf.stem}.figures"
        fig_dir.mkdir(exist_ok=True)
        figures = result.get("figures") or []
        for fig in figures:
            try:
                (fig_dir / f"{fig['id']}.png").write_bytes(download_figure(endpoint, result_id, fig["id"], token))
            except Exception as exc:  # one missing crop must not lose the whole document
                print(f"    ! figure {fig.get('id')}: {exc}")
        target.write_text(json.dumps({
            "source": pdf.name, "sha256": digest, "apiVersion": DI_API_VERSION, "modelId": DI_MODEL,
            "resultId": result_id, "analyzeResult": result,
        }), encoding="utf-8")
        print(f"    {len(result.get('pages') or [])} pages, {len(figures)} figures, "
              f"{len(result.get('tables') or [])} tables")


FIGURE_SYSTEM = "You describe technical figures from engineering documentation precisely and literally."
FIGURE_PROMPT = (
    "Return a JSON object with keys: kind, description, mermaid.\n"
    "kind: one of flowchart, block-diagram, state-machine, sequence, timing-diagram, "
    "register-bitfield, schematic, chart, photo, table, other.\n"
    "description: 2-6 sentences a developer can search. For a register bit-field diagram list every "
    "field in order with its bit range. Do not speculate beyond the image.\n"
    "mermaid: ONLY when kind is flowchart, block-diagram, state-machine or sequence, a Mermaid "
    "diagram (flowchart TD, stateDiagram-v2 or sequenceDiagram) reproducing the figure's nodes and "
    "edges using the exact labels in the image, with every label in double quotes. Otherwise null."
)


def describe_figures(ids: dict, cache_dir: Path, force: bool = False) -> None:
    import requests

    base = ids["foundryOpenAIEndpoint"].rstrip("/")
    dep = ids.get("exportFigureDeployment") or ids.get("visionDeployment", "vision")
    ver = ids.get("chatApiVersion", "2025-04-01-preview")
    url = f"{base}/openai/deployments/{dep}/chat/completions?api-version={ver}"
    token = _token()
    pngs = sorted(cache_dir.glob("*.figures/*.png"))
    if not pngs:
        print("  no cached figures -- run --analyze first")
        return
    done = 0
    for png in pngs:
        out = png.with_suffix(".json")
        if out.exists() and not force:
            continue
        body = {
            "messages": [
                {"role": "system", "content": FIGURE_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": FIGURE_PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode("ascii"),
                        "detail": "high"}},
                ]},
            ],
            "max_completion_tokens": ids.get("exportFigureMaxTokens", 1500),
            "response_format": {"type": "json_object"},
        }
        for attempt in range(5):
            r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=120)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2 ** attempt * 5)))
                continue
            break
        if r.status_code >= 400:
            print(f"  ! {png.parent.name}/{png.name}: {r.status_code} {r.text[:200]}")
            continue
        try:
            parsed = json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception:
            print(f"  ! {png.parent.name}/{png.name}: unparseable response")
            continue
        out.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        done += 1
    print(f"  described {done} figure(s)")


# --------------------------------------------------------------------------
# stage 2: export (local)
# --------------------------------------------------------------------------

@dataclass
class Part:
    stem: str
    source: str
    sha256: str
    page_offset: int
    result: dict
    figure_dir: Path


@dataclass
class Section:
    title: str
    text: str
    page_from: int | None = None
    page_to: int | None = None
    filename: str = ""


@dataclass
class DocExport:
    stem: str
    title: str
    parts: list[Part]
    sections: list[Section] = field(default_factory=list)
    figures: int = 0
    tables: int = 0
    pages: int = 0
    structured: dict = field(default_factory=dict)


def load_cache(cache_dir: Path) -> dict[str, list[Part]]:
    """Group cached analyze results by source document, stitching split parts."""
    docs: dict[str, list[Part]] = {}
    for f in sorted(cache_dir.glob("*.analyze.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        file_stem = f.name[: -len(".analyze.json")]
        stem, offset = part_info(file_stem)
        docs.setdefault(stem, []).append(Part(
            stem=file_stem, source=data.get("source", file_stem + ".pdf"), sha256=data.get("sha256", ""),
            page_offset=offset, result=data["analyzeResult"], figure_dir=cache_dir / f"{file_stem}.figures",
        ))
    for parts in docs.values():
        parts.sort(key=lambda p: p.page_offset)
    return docs


def apply_edits(content: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply (start, end, replacement) edits against ORIGINAL offsets.

    Replacements must not overlap each other. An insertion (start == end) that
    lands inside a replacement is moved to the replacement's start, so a page
    marker that falls inside a figure block survives the figure being rewritten.
    """
    repl = sorted([e for e in edits if e[1] > e[0]], key=lambda e: e[0])
    ins = [e for e in edits if e[1] == e[0]]
    moved = []
    for s, _, t in ins:
        for rs, re_, _ in repl:
            if rs < s < re_:
                s = rs
                break
        moved.append((s, s, t))
    ordered = sorted(moved + repl, key=lambda e: (e[0], 0 if e[1] == e[0] else 1))
    out, cur = [], 0
    for s, e, t in ordered:
        if s < cur:  # overlapping replacement -- keep the first, skip this one
            continue
        out.append(content[cur:s])
        out.append(t)
        cur = e
    out.append(content[cur:])
    return "".join(out)


def _figure_block(content: str, offset: int, length: int) -> tuple[int, int]:
    """The `<figure>...</figure>` block that contains a figure's span."""
    start = content.rfind("<figure>", 0, offset + 1)
    if start != -1:
        end = content.find("</figure>", start)
        if end != -1 and end + len("</figure>") >= offset + length:
            return start, end + len("</figure>")
    return offset, offset + length


def _figure_inner_text(block: str) -> str:
    inner = re.sub(r"</?figure>", "", block)
    inner = re.sub(r"<figcaption>.*?</figcaption>", "", inner, flags=re.S)
    lines = [ln.strip() for ln in inner.splitlines() if ln.strip()]
    # A stray heading marker inside figure text would become a section split.
    return "\n".join(("\\" + ln) if ln.startswith("#") else ln for ln in lines)


def _load_description(fig_dir: Path, fig_id: str) -> dict | None:
    p = fig_dir / f"{fig_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def valid_mermaid(src: str | None) -> str | None:
    if not src or not isinstance(src, str):
        return None
    body = src.strip().strip("`").strip()
    if body.lower().startswith("mermaid"):
        body = body[len("mermaid"):].strip()
    first = body.splitlines()[0].strip() if body else ""
    if not first.startswith(ALLOWED_MERMAID_HEADERS) or len(body.splitlines()) < 2:
        return None
    return body


def render_figure(fig: dict, block: str, page: int, image_rel: str | None, desc: dict | None) -> str:
    caption = ((fig.get("caption") or {}).get("content") or "").strip()
    label = caption or "untitled figure"
    out = ["", ""]
    if image_rel:
        out.append(f"![Figure, source page {page}: {_md_cell(label)}]({image_rel})")
        out.append("")
    out.append(f"*Figure — source page {page}{': ' + caption if caption else ''}*")
    out.append("")
    if desc and desc.get("description"):
        out.append("> **Figure description** (AI-generated from the image — verify against the source page): "
                   + " ".join(str(desc["description"]).split()))
        out.append("")
    mermaid = valid_mermaid((desc or {}).get("mermaid"))
    if mermaid:
        out += ["<sub>Mermaid rendering (AI-drafted from the figure — the image above is authoritative):</sub>",
                "", "```mermaid", mermaid, "```", ""]
    inner = _figure_inner_text(block)
    if inner:
        out += ["<details><summary>Text detected inside the figure</summary>", "", inner, "", "</details>", ""]
    return "\n".join(out) + "\n"


def transform_part(part: Part, assets_dir: Path | None, image_prefix: str) -> tuple[str, int]:
    """Rewrite one part's Markdown: page markers in, DI page furniture out,
    figure blocks replaced by image + description + optional Mermaid."""
    ar = part.result
    content = ar.get("content") or ""
    edits: list[tuple[int, int, str]] = []
    for pg in ar.get("pages") or []:
        spans = pg.get("spans") or []
        if spans:
            n = pg["pageNumber"] + part.page_offset
            edits.append((spans[0]["offset"], spans[0]["offset"], f"\n<!-- page: {n} -->\n"))
    figures = 0
    for fig in ar.get("figures") or []:
        spans = fig.get("spans") or []
        if not spans:
            continue
        s, e = _figure_block(content, spans[0]["offset"], spans[0].get("length", 0))
        regions = fig.get("boundingRegions") or [{}]
        page = (regions[0].get("pageNumber") or 1) + part.page_offset
        fig_id = str(fig.get("id", ""))
        idx = fig_id.split(".")[-1] if "." in fig_id else str(figures + 1)
        image_rel = None
        src_png = part.figure_dir / f"{fig_id}.png"
        if src_png.exists():
            name = f"fig-p{page:04d}-{idx}.png"
            if assets_dir is not None:
                assets_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_png, assets_dir / name)
            image_rel = f"{image_prefix}{name}"
        desc = _load_description(part.figure_dir, fig_id)
        edits.append((s, e, render_figure(fig, content[s:e], page, image_rel, desc)))
        figures += 1
    text = apply_edits(content, edits)

    def _leftover(m: re.Match) -> str:  # figure blocks DI did not list in figures[]
        inner = _figure_inner_text(m.group(0))
        return f"\n<details><summary>Figure text</summary>\n\n{inner}\n\n</details>\n" if inner else ""

    text = FIGURE_BLOCK_RE.sub(_leftover, text)
    text = DI_NOISE_RE.sub("", text)
    return text, figures


@dataclass
class Heading:
    pos: int
    level: int
    title: str
    rank: int


def find_headings(text: str) -> list[Heading]:
    heads, fenced = [], []
    for m in re.finditer(r"^```", text, re.M):
        fenced.append(m.start())
    fence_ranges = list(zip(fenced[0::2], fenced[1::2]))
    for m in HEADING_RE.finditer(text):
        if any(a <= m.start() <= b for a, b in fence_ranges):
            continue
        title = m.group(2).strip()
        num = NUMBERED_RE.match(title)
        rank = len(num.group(1).split(".")) if num else len(m.group(1))
        heads.append(Heading(m.start(), len(m.group(1)), title, rank))
    return heads


def split_ranges(text: str, heads: list[Heading], max_chars: int, start: int = 0,
                 end: int | None = None, depth: int = 0) -> list[tuple[int, int]]:
    end = len(text) if end is None else end
    inner = [h for h in heads if start < h.pos < end]
    if not inner or (depth > 0 and end - start <= max_chars):
        return [(start, end)]
    rank = min(h.rank for h in inner)
    bounds = [start] + [h.pos for h in inner if h.rank == rank] + [end]
    out: list[tuple[int, int]] = []
    for a, b in zip(bounds, bounds[1:]):
        if b <= a:
            continue
        if b - a > max_chars:
            out += split_ranges(text, heads, max_chars, a, b, depth + 1)
        else:
            out.append((a, b))
    return out


def merge_small(ranges: list[tuple[int, int]], min_chars: int, max_chars: int) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for a, b in ranges:
        if merged and (merged[-1][1] - merged[-1][0] < min_chars) and (b - merged[-1][0] <= max_chars):
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def build_sections(text: str, max_chars: int = 40000, min_chars: int = 1500) -> list[Section]:
    heads = find_headings(text)
    ranges = merge_small(split_ranges(text, heads, max_chars), min_chars, max_chars)
    marks = [(m.start(), int(m.group(1))) for m in PAGE_MARK_RE.finditer(text)]
    positions = [p for p, _ in marks]
    lead_re = re.compile(r"(?:\s*<!-- page: \d+ -->)*\s*")
    trail_re = re.compile(r"(?:\s*<!-- page: \d+ -->)*\s*\Z")
    sections = []
    for a, b in ranges:
        seg = text[a:b]
        # A page marker sitting right before the next chapter heading belongs to
        # that chapter, not this one -- trim leading/trailing markers before
        # deciding the page range, or every range is off by one at the end.
        content_start = a + lead_re.match(seg).end()
        content_end = a + trail_re.search(seg).start()
        if content_end <= content_start:
            continue
        first = next((h for h in heads if a <= h.pos < b), None)
        title = first.title if first else "Front matter"
        i = bisect.bisect_right(positions, content_start) - 1
        page_from = marks[i][1] if i >= 0 else None
        inside = [n for p, n in marks if content_start <= p < content_end]
        page_to = inside[-1] if inside else page_from
        body = text[a:content_end].strip("\n")
        sections.append(Section(title=title, text=body, page_from=page_from, page_to=page_to))
    return sections


# ---- structured extraction ----------------------------------------------

BIT_RANGE_RE = re.compile(r"^(?:bits?\s*)?\[?\s*(\d{1,3})\s*(?:(?::|-|–|\.\.)\s*(\d{1,3}))?\s*\]?$", re.I)


def parse_bits(text: str) -> tuple[int, int] | None:
    m = BIT_RANGE_RE.match((text or "").strip())
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) is not None else a
    return max(a, b), min(a, b)


def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9/ ]+", " ", (h or "").lower()).strip()


def table_grid(table: dict) -> tuple[list[str], list[list[str]]]:
    rows, cols = table.get("rowCount", 0), table.get("columnCount", 0)
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    header_rows: set[int] = set()
    kinds: dict[int, set] = {}
    for c in table.get("cells") or []:
        r0, c0 = c.get("rowIndex", 0), c.get("columnIndex", 0)
        for r in range(r0, min(rows, r0 + c.get("rowSpan", 1))):
            for cc in range(c0, min(cols, c0 + c.get("columnSpan", 1))):
                grid[r][cc] = (c.get("content") or "").strip()
        kinds.setdefault(r0, set()).add(c.get("kind", "content"))
    for r, ks in kinds.items():
        if ks == {"columnHeader"}:
            header_rows.add(r)
    if not header_rows and rows:
        header_rows = {0}
    headers = []
    for cc in range(cols):
        parts = []
        for r in sorted(header_rows):
            v = grid[r][cc]
            if v and v not in parts:
                parts.append(v)
        headers.append(" ".join(parts))
    body = [grid[r] for r in range(rows) if r not in header_rows]
    return headers, body


def _col(headers: list[str], *preds) -> int | None:
    for i, h in enumerate(headers):
        n = _norm(h)
        if any(p(n) for p in preds):
            return i
    return None


def classify_table(headers: list[str], body: list[list[str]]) -> str | None:
    bits = _col(headers, lambda n: n.startswith("bit") and "name" not in n and "field" not in n,
                lambda n: n in ("position", "bit position", "offset bits"))
    if bits is not None and body:
        parsed = sum(1 for r in body if parse_bits(r[bits]))
        others = [i for i in range(len(headers)) if i != bits]
        name = _col([headers[i] if i in others else "" for i in range(len(headers))],
                    lambda n: n in ("name", "field", "field name", "bit name", "symbol", "mnemonic",
                                    "bit field", "bitfield"))
        if name is not None and parsed / len(body) >= 0.6:
            return "registers"
    pin = _col(headers, lambda n: n == "pin" or n.startswith("pin ") or n in ("ball", "pad", "pin no", "pin number"))
    if pin is not None and _col(headers, lambda n: any(k in n for k in ("name", "function", "signal", "type", "description"))) is not None:
        return "pins"
    has_min = _col(headers, lambda n: n.startswith("min")) is not None
    has_max = _col(headers, lambda n: n.startswith("max")) is not None
    has_typ = _col(headers, lambda n: n.startswith("typ")) is not None
    if (has_min or has_typ) and (has_max or has_typ) and _col(
            headers, lambda n: any(k in n for k in ("parameter", "symbol", "description", "condition", "characteristic"))) is not None:
        return "electrical"
    return None


def _num(v: str):
    m = re.match(r"^\s*([-+−]?\d+(?:\.\d+)?)\s*$", (v or "").replace("−", "-"))
    return float(m.group(1)) if m else None


def extract_register(headers, body, context: str, page: int, caption: str) -> dict:
    bits = _col(headers, lambda n: n.startswith("bit") and "name" not in n and "field" not in n,
                lambda n: n in ("position", "bit position", "offset bits"))
    name = _col([h if i != bits else "" for i, h in enumerate(headers)],
                lambda n: n in ("name", "field", "field name", "bit name", "symbol", "mnemonic", "bit field", "bitfield"))
    access = _col(headers, lambda n: "access" in n or n in ("r/w", "rw", "type", "attribute", "attr"))
    reset = _col(headers, lambda n: "reset" in n or "default" in n or n.startswith("init"))
    desc = _col(headers, lambda n: "desc" in n or "function" in n or "comment" in n)
    fields = []
    for r in body:
        pb = parse_bits(r[bits])
        if not pb:
            continue
        fields.append({
            "bits": r[bits], "msb": pb[0], "lsb": pb[1], "name": r[name] if name is not None else "",
            "access": r[access] if access is not None else None,
            "reset": r[reset] if reset is not None else None,
            "description": r[desc] if desc is not None else None,
        })
    return {"register": caption or context or f"Register table (p. {page})", "page": page,
            "context": context, "caption": caption or None, "fields": fields}


def packet_diagram(fields: list[dict]) -> str | None:
    if not fields:
        return None
    ordered = sorted(fields, key=lambda f: f["lsb"])
    lines, cursor = [MERMAID_PACKET_KEYWORD], 0
    for f in ordered:
        if f["lsb"] < cursor:  # overlapping / duplicate rows -- not drawable honestly
            return None
        if f["lsb"] > cursor:
            lines.append(f'{cursor}-{f["lsb"] - 1}: "reserved"')
        label = (f["name"] or "unnamed").replace('"', "'")
        rng = f'{f["lsb"]}' if f["msb"] == f["lsb"] else f'{f["lsb"]}-{f["msb"]}'
        lines.append(f'{rng}: "{label}"')
        cursor = f["msb"] + 1
    return "\n".join(lines)


def extract_rows(headers, body, context: str, page: int, caption: str, kind: str) -> dict:
    rows = []
    for r in body:
        if not any(v.strip() for v in r):
            continue
        row = {headers[i] or f"col{i + 1}": r[i] for i in range(len(headers))}
        if kind == "electrical":
            for key in list(row):
                n = _norm(key)
                if n.startswith(("min", "typ", "max")):
                    row[f"_{n[:3]}_value"] = _num(row[key])
        rows.append(row)
    return {"table": caption or context or f"Table (p. {page})", "page": page, "context": context,
            "caption": caption or None, "columns": headers, "rows": rows}


def extract_structured(parts: list[Part], extractors: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: [] for k in extractors}
    if not extractors:
        return out
    for part in parts:
        ar = part.result
        content = ar.get("content") or ""
        heads = [(m.start(), m.group(2).strip()) for m in HEADING_RE.finditer(content)]
        head_pos = [p for p, _ in heads]
        for t in ar.get("tables") or []:
            headers, body = table_grid(t)
            kind = classify_table(headers, body)
            if kind not in extractors:
                continue
            off = ((t.get("spans") or [{}])[0]).get("offset", 0)
            i = bisect.bisect_right(head_pos, off) - 1
            context = heads[i][1] if i >= 0 else ""
            page = ((t.get("boundingRegions") or [{}])[0].get("pageNumber") or 1) + part.page_offset
            caption = ((t.get("caption") or {}).get("content") or "").strip()
            if kind == "registers":
                out[kind].append(extract_register(headers, body, context, page, caption))
            else:
                out[kind].append(extract_rows(headers, body, context, page, caption, kind))
    return out


# ---- writers -------------------------------------------------------------

def _frontmatter(d: dict) -> str:
    lines = ["---"]
    for k, v in d.items():
        lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _pages(a, b) -> str:
    if a is None:
        return "n/a"
    return f"{a}" if b in (None, a) else f"{a}–{b}"


def write_structured(doc_dir: Path, structured: dict[str, list[dict]], source: str) -> list[Path]:
    written = []
    sdir = doc_dir / "structured"
    for kind, items in structured.items():
        if not items:
            continue
        sdir.mkdir(parents=True, exist_ok=True)
        jp = sdir / f"{kind}.json"
        jp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(jp)
        md = [f"# {kind.capitalize()} — {source}", "",
              f"> Extracted from tables in `{source}`. `{kind}.json` alongside this file carries the same "
              "data for tools. Always confirm a value against the cited source page before relying on it.", ""]
        if kind == "registers":
            md += ["| Register | Fields | Source page |", "|---|---:|---:|"]
            for i, r in enumerate(items):
                md.append(f"| [{_md_cell(r['register'])}](#reg-{i + 1}) | {len(r['fields'])} | {r['page']} |")
            md.append("")
            for i, r in enumerate(items):
                md += [f'<a id="reg-{i + 1}"></a>', f"## {_md_cell(r['register'])}", "",
                       f"Source page **{r['page']}**" + (f" · section *{_md_cell(r['context'])}*" if r["context"] else ""), ""]
                diagram = packet_diagram(r["fields"])
                if diagram:
                    md += ["```mermaid", diagram, "```", ""]
                md += ["| Bits | Field | Access | Reset | Description |", "|---|---|---|---|---|"]
                for f in sorted(r["fields"], key=lambda f: -f["msb"]):
                    md.append(f"| {_md_cell(f['bits'])} | {_md_cell(f['name'])} | {_md_cell(f['access'])} | "
                              f"{_md_cell(f['reset'])} | {_md_cell(f['description'])} |")
                md.append("")
        else:
            for i, t in enumerate(items):
                md += [f"## {_md_cell(t['table'])}", "", f"Source page **{t['page']}**", ""]
                cols = [c or f"col{j + 1}" for j, c in enumerate(t["columns"])]
                md += ["| " + " | ".join(_md_cell(c) for c in cols) + " |", "|" + "---|" * len(cols)]
                for row in t["rows"]:
                    md.append("| " + " | ".join(_md_cell(row.get(c, "")) for c in cols) + " |")
                md.append("")
        mp = sdir / f"{kind}.md"
        mp.write_text("\n".join(md), encoding="utf-8")
        written.append(mp)
    return written


def export_document(stem: str, parts: list[Part], out_dir: Path, extractors: list[str],
                    max_chars: int, min_chars: int) -> DocExport:
    doc_dir = out_dir / slugify(stem)
    if doc_dir.exists():
        shutil.rmtree(doc_dir)
    (doc_dir / "sections").mkdir(parents=True)
    assets = doc_dir / "assets" / "figures"
    texts, figures, tables, pages = [], 0, 0, 0
    for part in parts:
        t, f = transform_part(part, assets, "../assets/figures/")
        texts.append(t)
        figures += f
        tables += len(part.result.get("tables") or [])
        pages = max(pages, len(part.result.get("pages") or []) + part.page_offset)
    full = "\n\n".join(texts)
    full = re.sub(r"\n{3,}", "\n\n", full)
    doc = DocExport(stem=stem, title=stem, parts=parts, figures=figures, tables=tables, pages=pages)
    first_title = next((h.title for h in find_headings(full) if h.level == 1), None)
    doc.title = first_title or stem
    doc.sections = build_sections(full, max_chars=max_chars, min_chars=min_chars)
    width = max(3, len(str(len(doc.sections))))
    for i, s in enumerate(doc.sections, start=1):
        s.filename = f"{i:0{width}d}-{slugify(s.title)}.md"
    # Cite the ORIGINAL document, even when only one split part was analyzed.
    single = len(parts) == 1 and not PART_RE.match(parts[0].stem)
    source_name = parts[0].source if single else f"{stem}.pdf"
    for i, s in enumerate(doc.sections):
        prev_link = f"[← {_md_cell(doc.sections[i - 1].title)}]({doc.sections[i - 1].filename})" if i else ""
        next_link = (f"[{_md_cell(doc.sections[i + 1].title)} →]({doc.sections[i + 1].filename})"
                     if i + 1 < len(doc.sections) else "")
        nav = " · ".join(x for x in (prev_link, "[Document index](../README.md)", next_link) if x)
        header = (_frontmatter({"source": source_name, "pages": _pages(s.page_from, s.page_to),
                                "section": s.title, "generated_by": f"export_repo_corpus.py {EXPORTER_VERSION}"})
                  + f"\n> Source: `{source_name}`, pages {_pages(s.page_from, s.page_to)} · {nav}\n\n")
        (doc_dir / "sections" / s.filename).write_text(header + s.text.strip() + f"\n\n---\n\n{nav}\n",
                                                        encoding="utf-8")
    doc.structured = extract_structured(parts, extractors)
    write_structured(doc_dir, doc.structured, source_name)
    write_document_readme(doc_dir, doc, source_name)
    return doc


def write_document_readme(doc_dir: Path, doc: DocExport, source_name: str) -> None:
    counts = {k: len(v) for k, v in doc.structured.items()}
    L = [f"# {_md_cell(doc.title)}", "",
         f"Repository-ready export of `{source_name}`, generated by `export_repo_corpus.py` "
         f"{EXPORTER_VERSION}. Every section file cites its source page range; figures are the original "
         "crops from the PDF.", "",
         "## At a glance", "",
         "| Item | Count |", "|---|---:|",
         f"| Source pages | {doc.pages} |",
         f"| Section files | {len(doc.sections)} |",
         f"| Figures (image files) | {doc.figures} |",
         f"| Tables | {doc.tables} |"]
    for k, n in counts.items():
        L.append(f"| Structured {k} records | {n} |")
    L += ["", "## Structure", "", "```mermaid", "mindmap", f"  root(({_clean_label(doc.title, 40)}))"]
    shown = doc.sections[:30]
    for s in shown:
        L.append(f"    {_clean_label(s.title)}")
    if len(doc.sections) > len(shown):
        L.append(f"    plus {len(doc.sections) - len(shown)} more sections")
    L += ["```", "", "## Sections", "", "| # | Section | Source pages | Figures | Tables |", "|---:|---|---|---:|---:|"]
    for i, s in enumerate(doc.sections, start=1):
        L.append(f"| {i} | [{_md_cell(s.title)}](sections/{s.filename}) | {_pages(s.page_from, s.page_to)} | "
                 f"{s.text.count('](../assets/figures/')} | {s.text.count('<table>')} |")
    if any(counts.values()):
        L += ["", "## Structured data", "", "| Kind | Records | Human-readable | Machine-readable |", "|---|---:|---|---|"]
        for k, n in counts.items():
            if n:
                L.append(f"| {k} | {n} | [structured/{k}.md](structured/{k}.md) | [structured/{k}.json](structured/{k}.json) |")
    L += ["", "## Provenance", "", "| Part | Pages in original | SHA-256 |", "|---|---|---|"]
    for p in doc.parts:
        n = len(p.result.get("pages") or [])
        L.append(f"| `{p.source}` | {p.page_offset + 1}–{p.page_offset + n} | `{p.sha256[:16]}…` |")
    L.append("")
    (doc_dir / "README.md").write_text("\n".join(L), encoding="utf-8")


def write_corpus_readme(out_dir: Path, docs: list[DocExport], display_name: str = "reference documentation",
                        extractors: list[str] | None = None) -> None:
    extractors = extractors or []
    flow = ["```mermaid", "flowchart LR",
            '  pdf["Source PDFs"] --> split{"Over 300 pages?"}',
            '  split -- "yes" --> parts["Page-ranged parts<br/>(page numbers preserved)"]',
            '  split -- "no" --> di',
            '  parts --> di["Document Intelligence<br/>prebuilt-layout<br/>Markdown + figure crops"]',
            '  di --> vis["Vision model<br/>figure descriptions<br/>+ draft Mermaid (optional)"]',
            '  vis --> pkg["This folder"]', '  di --> pkg']
    if extractors:
        flow += [f'  di --> tab["Table extractors<br/>{" · ".join(extractors)}"]', '  tab --> pkg']
    flow.append("```")
    L = [f"# {display_name[:1].upper() + display_name[1:]}", "",
         "Documents exported into repository-committable Markdown so coding assistants "
         "(GitHub Copilot, VS Code agent mode) can read them straight from the working tree. "
         "Generated by `export_repo_corpus.py` — regenerate rather than hand-edit.", "",
         "## How this was produced", ""] + flow + ["",
         "## Documents", "",
         "| Document | Pages | Sections | Figures | Tables | Structured records |",
         "|---|---:|---:|---:|---:|---:|"]
    for d in docs:
        n = sum(len(v) for v in d.structured.values())
        L.append(f"| [{_md_cell(d.title)}]({slugify(d.stem)}/README.md) | {d.pages} | {len(d.sections)} | "
                 f"{d.figures} | {d.tables} | {n} |")
    L += ["", "## Using this with GitHub Copilot", "",
          "| Step | What to do |", "|---|---|",
          "| 1 | Commit this folder into the repository that uses these documents (e.g. `docs/reference/`). |",
          "| 2 | Paste [`COPILOT-INSTRUCTIONS.snippet.md`](COPILOT-INSTRUCTIONS.snippet.md) into the repo's "
          "`.github/copilot-instructions.md` so Copilot knows the folder exists and how to cite it. |",
          "| 3 | In Copilot Chat, reference a section directly (`#file`) or let agent mode search the folder. |",
          "| 4 | For questions across hundreds of pages, pair this with the knowledge-base MCP endpoint — the "
          "export is best for *reading* a known section, the index is best for *finding* one. |", ""]
    (out_dir / "README.md").write_text("\n".join(L), encoding="utf-8")
    snippet = [
        f"## {display_name[:1].upper() + display_name[1:]}",
        "",
        f"- The {display_name} is exported under this folder (see its `README.md` for the index).",
        "- Prefer these files over general knowledge when answering questions they cover.",
        "- Every section file starts with its source PDF and page range. When you use a value from it, cite "
        "the document and page.",
    ]
    if extractors:
        snippet.append(f"- `structured/*.json` holds machine-readable {', '.join(extractors)} tables where "
                       "available; confirm values against the cited page before generating code from them.")
    snippet += ["- Figure descriptions and Mermaid renderings are AI-generated from images; the image file is authoritative.", ""]
    (out_dir / "COPILOT-INSTRUCTIONS.snippet.md").write_text("\n".join(snippet), encoding="utf-8")


def write_manifest(out_dir: Path, docs: list[DocExport], generated_at: str, extractors: list[str]) -> None:
    files = []
    for p in sorted(out_dir.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            files.append({"path": p.relative_to(out_dir).as_posix(), "sha256": sha256_file(p)})
    manifest = {
        "generator": "export_repo_corpus.py", "generatorVersion": EXPORTER_VERSION, "generatedAt": generated_at,
        "documentIntelligence": {"apiVersion": DI_API_VERSION, "modelId": DI_MODEL},
        "extractors": extractors,
        "documents": [{
            "stem": d.stem, "title": d.title, "pages": d.pages, "sections": len(d.sections),
            "figures": d.figures, "tables": d.tables,
            "sources": [{"file": p.source, "sha256": p.sha256, "pageOffset": p.page_offset} for p in d.parts],
            "structured": {k: len(v) for k, v in d.structured.items()},
        } for d in docs],
        "files": files,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def export_corpus(cache_dir: Path, out_dir: Path, extractors: list[str], generated_at: str | None = None,
                  max_chars: int = 40000, min_chars: int = 1500,
                  display_name: str = "reference documentation") -> list[DocExport]:
    docs_parts = load_cache(cache_dir)
    if not docs_parts:
        raise SystemExit(f"  ! no cached analyze results in {cache_dir} -- run --analyze first")
    out_dir.mkdir(parents=True, exist_ok=True)
    docs = [export_document(stem, parts, out_dir, extractors, max_chars, min_chars)
            for stem, parts in docs_parts.items()]
    write_corpus_readme(out_dir, docs, display_name, extractors)
    write_manifest(out_dir, docs, generated_at or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                   extractors)
    return docs


def resolve_extractors(ids: dict | None, cli_value: str | None) -> list[str]:
    if cli_value is not None:
        wanted = [x.strip() for x in cli_value.split(",") if x.strip() and x.strip() != "none"]
    else:
        wanted = list(((ids or {}).get("corpus") or {}).get("exportExtractors") or [])
    unknown = [x for x in wanted if x not in KNOWN_EXTRACTORS]
    if unknown:
        raise SystemExit(f"  ! unknown extractor(s) {unknown}; choose from {', '.join(KNOWN_EXTRACTORS)}")
    return wanted


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids-file", help="Required for --analyze/--describe-figures; optional for --export "
                                      "(read for corpus.exportExtractors)")
    p.add_argument("--source-dir", help="PDF folder for --analyze (split parts are fine)")
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--analyze", action="store_true", help="Run Document Intelligence and cache results (billable)")
    p.add_argument("--describe-figures", action="store_true",
                   help="Describe cached figures with a vision model; drafts Mermaid where possible (billable)")
    p.add_argument("--export", action="store_true", help="Write the repository package from the cache (free)")
    p.add_argument("--extract", default=None,
                   help=f"Comma list from {', '.join(KNOWN_EXTRACTORS)} or 'none'. "
                        "Default: corpus.exportExtractors in the ids file, else none")
    p.add_argument("--max-section-chars", type=int, default=40000)
    p.add_argument("--min-section-chars", type=int, default=1500)
    p.add_argument("--force", action="store_true", help="Ignore cached results and re-run the service calls")
    args = p.parse_args()

    if not (args.analyze or args.describe_figures or args.export):
        p.error("choose at least one of --analyze, --describe-figures, --export")
    ids = None
    if args.ids_file:
        ids = json.loads(Path(args.ids_file).read_text(encoding="utf-8"))
    if (args.analyze or args.describe_figures) and ids is None:
        p.error("--analyze/--describe-figures require --ids-file")
    cache = Path(args.cache_dir)
    if args.analyze:
        if not args.source_dir:
            p.error("--analyze requires --source-dir")
        print("Analyzing with Document Intelligence ...")
        analyze_corpus(ids, Path(args.source_dir), cache, force=args.force)
    if args.describe_figures:
        print("Describing figures ...")
        describe_figures(ids, cache, force=args.force)
    if args.export:
        extractors = resolve_extractors(ids, args.extract)
        print(f"Exporting to {args.out} (extractors: {', '.join(extractors) or 'none'}) ...")
        docs = export_corpus(cache, Path(args.out), extractors,
                             display_name=((ids or {}).get("corpus") or {}).get("displayName") or "reference documentation",
                             max_chars=args.max_section_chars, min_chars=args.min_section_chars)
        for d in docs:
            recs = ", ".join(f"{k}={len(v)}" for k, v in d.structured.items()) or "no structured extraction"
            print(f"  {d.stem}: {d.pages} pages -> {len(d.sections)} sections, {d.figures} figures, "
                  f"{d.tables} tables ({recs})")
        print(f"  wrote {Path(args.out) / 'README.md'}")


if __name__ == "__main__":
    sys.exit(main())
