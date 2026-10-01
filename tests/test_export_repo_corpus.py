"""Offline tests for export_repo_corpus.py -- no Azure calls.

The fixture is a synthetic Document Intelligence prebuilt-layout result built
in code so offsets are exact: a title page, a chapter with a figure, a register
bit-field table, a pin table, and an electrical table, spread across pages.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import export_repo_corpus as erc  # noqa: E402


def _cell(r, c, text, kind="content", rs=1, cs=1):
    return {"rowIndex": r, "columnIndex": c, "content": text, "kind": kind, "rowSpan": rs, "columnSpan": cs}


def _table(header, rows, page, offset, caption=None):
    cells = [_cell(0, i, h, "columnHeader") for i, h in enumerate(header)]
    for r, row in enumerate(rows, start=1):
        cells += [_cell(r, i, v) for i, v in enumerate(row)]
    t = {"rowCount": len(rows) + 1, "columnCount": len(header), "cells": cells,
         "boundingRegions": [{"pageNumber": page}], "spans": [{"offset": offset, "length": 10}]}
    if caption:
        t["caption"] = {"content": caption}
    return t


def build_result():
    """Return (analyzeResult, figure_id). Pages: 1 title, 2 overview+figure, 3 timer, 4 pins/electrical."""
    pieces, pages, marks = [], [], {}

    def add(text, key=None):
        off = sum(len(p) for p in pieces)
        if key:
            marks[key] = off
        pieces.append(text)
        return off

    p1 = add("# Example MCU Reference Manual\n\nThis manual describes the example device.\n\n")
    add('<!-- PageFooter="Page 1" -->\n<!-- PageBreak -->\n')
    p2 = add("## 1 Overview\n\nThe device integrates a CPU and a bus matrix.\n\n")
    fig_start = add("<figure>\n<figcaption>Figure 1. Block diagram</figcaption>\nCPU\n# Bus\n</figure>\n\n", "fig")
    add('<!-- PageNumber="2" -->\n<!-- PageBreak -->\n')
    p3 = add("## 2 Timer\n\nThe timer counts up.\n\n### 2.1 CTRL register\n\n")
    reg_off = add("<table><tr><th>Bits</th></tr></table>\n\n", "reg")
    add("<!-- PageBreak -->\n")
    p4 = add("## 3 Pinout\n\n")
    pin_off = add("<table><tr><th>Pin</th></tr></table>\n\n", "pin")
    elec_off = add("<table><tr><th>Parameter</th></tr></table>\n", "elec")
    content = "".join(pieces)
    starts = [p1, p2, p3, p4]
    ends = starts[1:] + [len(content)]
    for n, (a, b) in enumerate(zip(starts, ends), start=1):
        pages.append({"pageNumber": n, "spans": [{"offset": a, "length": b - a}]})
    fig_len = content.index("</figure>", fig_start) + len("</figure>") - fig_start
    figures = [{"id": "2.1", "boundingRegions": [{"pageNumber": 2}],
                "spans": [{"offset": fig_start + 9, "length": fig_len - 9}],
                "caption": {"content": "Figure 1. Block diagram"}}]
    tables = [
        _table(["Bit", "Name", "Access", "Reset", "Description"],
               [["31:8", "RSVD", "R", "0", "Reserved"], ["7:4", "PRESC", "RW", "0x0", "Prescaler"],
                ["1", "IRQEN", "RW", "0", "Interrupt enable"], ["0", "EN", "RW", "0", "Timer enable"]],
               3, reg_off, caption="Table 5. CTRL register"),
        _table(["Pin", "Name", "Function"], [["1", "P0.00", "GPIO"], ["2", "P0.01", "UART TX"]], 4, pin_off),
        _table(["Parameter", "Min", "Typ", "Max", "Unit"], [["Supply voltage", "1.7", "3.0", "3.6", "V"]], 4, elec_off),
        _table(["Revision", "Date", "Notes"], [["1.0", "2020", "Initial"]], 4, elec_off),
    ]
    return {"content": content, "pages": pages, "figures": figures, "tables": tables}


def write_cache(cache: Path, stem: str, result: dict, figure_png: bool = True, describe: dict | None = None):
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{stem}.analyze.json").write_text(json.dumps({
        "source": f"{stem}.pdf", "sha256": "ab" * 32, "apiVersion": erc.DI_API_VERSION,
        "modelId": erc.DI_MODEL, "resultId": "r1", "analyzeResult": result}), encoding="utf-8")
    figs = cache / f"{stem}.figures"
    figs.mkdir(exist_ok=True)
    if figure_png:
        (figs / "2.1.png").write_bytes(b"\x89PNG fake")
    if describe:
        (figs / "2.1.json").write_text(json.dumps(describe), encoding="utf-8")


# --------------------------------------------------------------------------
# unit
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("31:16", (31, 16)), ("[7:0]", (7, 0)), ("15", (15, 15)), ("Bit 5", (5, 5)),
    ("3-0", (3, 0)), ("0..7", (7, 0)), ("bits 12:8", (12, 8)), ("R/W", None), ("", None),
])
def test_parse_bits(text, expected):
    assert erc.parse_bits(text) == expected


def test_classify_tables():
    r = build_result()
    kinds = [erc.classify_table(*erc.table_grid(t)) for t in r["tables"]]
    assert kinds == ["registers", "pins", "electrical", None]


def test_packet_diagram_fills_gaps_and_orders_lsb_first():
    fields = [{"msb": 7, "lsb": 4, "name": "PRESC"}, {"msb": 0, "lsb": 0, "name": "EN"}]
    d = erc.packet_diagram(fields)
    assert d.splitlines() == [erc.MERMAID_PACKET_KEYWORD, '0: "EN"', '1-3: "reserved"', '4-7: "PRESC"']


def test_packet_diagram_refuses_overlaps():
    assert erc.packet_diagram([{"msb": 7, "lsb": 0, "name": "A"}, {"msb": 3, "lsb": 0, "name": "B"}]) is None


def test_apply_edits_moves_insertions_out_of_replacements():
    s = "aaaa<figure>xx</figure>bbbb"
    start, end = s.index("<figure>"), s.index("</figure>") + 9
    out = erc.apply_edits(s, [(start + 3, start + 3, "[P]"), (start, end, "[FIG]"), (0, 0, "[S]")])
    assert out == "[S]aaaa[P][FIG]bbbb"


def test_valid_mermaid_filters():
    assert erc.valid_mermaid("flowchart TD\n  A --> B") == "flowchart TD\n  A --> B"
    assert erc.valid_mermaid("```mermaid\nstateDiagram-v2\n  [*] --> Idle\n```").startswith("stateDiagram-v2")
    assert erc.valid_mermaid("pie\n  \"a\": 1") is None
    assert erc.valid_mermaid("flowchart TD") is None
    assert erc.valid_mermaid(None) is None


def test_part_info_restores_original_page_offset():
    assert erc.part_info("manual__p0301-0600") == ("manual", 300)
    assert erc.part_info("manual") == ("manual", 0)


def test_resolve_extractors_defaults_to_none_and_rejects_unknown():
    # Generic by default: a corpus with no exportExtractors gets no hardware-specific parsing.
    assert erc.resolve_extractors({"corpus": {}}, None) == []
    assert erc.resolve_extractors(None, None) == []
    assert erc.resolve_extractors({"corpus": {"exportExtractors": ["pins"]}}, None) == ["pins"]
    assert erc.resolve_extractors({"corpus": {"exportExtractors": ["pins"]}}, "none") == []
    with pytest.raises(SystemExit):
        erc.resolve_extractors(None, "registers,bogus")


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------

def test_export_end_to_end(tmp_path):
    cache, out = tmp_path / "cache", tmp_path / "out"
    describe = {"kind": "block-diagram", "description": "CPU connected to the bus matrix.",
                "mermaid": 'flowchart LR\n  cpu["CPU"] --> bus["Bus"]'}
    # Stored as a split part: pages 11-14 of the original document.
    write_cache(cache, "mcu__p0011-0014", build_result(), describe=describe)
    docs = erc.export_corpus(cache, out, ["registers", "pins", "electrical"],
                             generated_at="2026-01-01T00:00:00+00:00", min_chars=0)
    assert len(docs) == 1
    doc = docs[0]
    root = out / "mcu"
    files = sorted(p.name for p in (root / "sections").iterdir())
    assert len(files) == 4
    assert files[0].startswith("001-example-mcu-reference-manual")

    by_title = {s.title: s for s in doc.sections}
    assert (by_title["Example MCU Reference Manual"].page_from, by_title["Example MCU Reference Manual"].page_to) == (11, 11)
    assert (by_title["1 Overview"].page_from, by_title["1 Overview"].page_to) == (12, 12)
    assert (by_title["2 Timer"].page_from, by_title["2 Timer"].page_to) == (13, 13)
    assert by_title["3 Pinout"].page_from == 14

    overview = (root / "sections" / by_title["1 Overview"].filename).read_text(encoding="utf-8")
    assert "](../assets/figures/fig-p0012-1.png)" in overview
    assert (root / "assets" / "figures" / "fig-p0012-1.png").exists()
    assert "```mermaid\nflowchart LR" in overview
    assert "AI-generated" in overview
    assert "<figure>" not in overview
    assert "\\# Bus" in overview  # heading-like figure text escaped, not a new section
    assert 'pages: "12"' in overview and 'source: "mcu.pdf"' in overview

    all_text = "".join(p.read_text(encoding="utf-8") for p in (root / "sections").iterdir())
    assert "PageBreak" not in all_text and "PageFooter" not in all_text and "PageNumber" not in all_text

    regs = json.loads((root / "structured" / "registers.json").read_text(encoding="utf-8"))
    assert regs[0]["register"] == "Table 5. CTRL register" and regs[0]["page"] == 13
    assert [f["name"] for f in regs[0]["fields"]] == ["RSVD", "PRESC", "IRQEN", "EN"]
    reg_md = (root / "structured" / "registers.md").read_text(encoding="utf-8")
    assert erc.MERMAID_PACKET_KEYWORD in reg_md and "| 7:4 | PRESC |" in reg_md
    pins = json.loads((root / "structured" / "pins.json").read_text(encoding="utf-8"))
    assert pins[0]["rows"][1]["Function"] == "UART TX" and pins[0]["page"] == 14
    elec = json.loads((root / "structured" / "electrical.json").read_text(encoding="utf-8"))
    assert elec[0]["rows"][0]["_max_value"] == 3.6

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "```mermaid\nmindmap" in readme and "| Figures (image files) | 1 |" in readme
    assert (out / "README.md").exists() and (out / "COPILOT-INSTRUCTIONS.snippet.md").exists()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generatedAt"] == "2026-01-01T00:00:00+00:00"
    assert manifest["documents"][0]["sources"][0]["pageOffset"] == 10
    paths = {f["path"] for f in manifest["files"]}
    assert "mcu/assets/figures/fig-p0012-1.png" in paths and "mcu/structured/registers.json" in paths


def test_export_without_extractors_or_figure_assets_is_still_valid(tmp_path):
    cache, out = tmp_path / "cache", tmp_path / "out"
    write_cache(cache, "doc", build_result(), figure_png=False)
    erc.export_corpus(cache, out, [], generated_at="x", min_chars=0)
    root = out / "doc"
    assert not (root / "structured").exists()
    overview = next((root / "sections").glob("*overview*.md")).read_text(encoding="utf-8")
    assert "Figure — source page 2: Figure 1. Block diagram" in overview
    assert "![" not in overview


def test_split_parts_are_stitched_into_one_document(tmp_path):
    cache, out = tmp_path / "cache", tmp_path / "out"
    write_cache(cache, "big__p0001-0004", build_result())
    write_cache(cache, "big__p0005-0008", build_result())
    docs = erc.export_corpus(cache, out, [], generated_at="x", min_chars=0)
    assert len(docs) == 1 and docs[0].pages == 8
    assert [p.page_offset for p in docs[0].parts] == [0, 4]
    assert max(s.page_to for s in docs[0].sections) == 8
    assert (out / "big" / "assets" / "figures" / "fig-p0006-1.png").exists()


def test_large_chapter_is_subsplit_at_the_next_heading_level():
    text = "## 1 Big\n\n" + ("x" * 50) + "\n\n### 1.1 A\n\n" + ("y" * 50) + "\n\n### 1.2 B\n\n" + ("z" * 50) + "\n"
    sections = erc.build_sections(text, max_chars=80, min_chars=0)
    assert [s.title for s in sections] == ["1 Big", "1.1 A", "1.2 B"]


def test_generic_corpus_output_carries_no_hardware_vocabulary(tmp_path):
    """Reusability guard (hard-rule #14): with extractors off and a non-hardware
    display name, nothing the exporter writes around the documents themselves
    may assume a hardware corpus."""
    cache, out = tmp_path / "cache", tmp_path / "out"
    write_cache(cache, "handbook", build_result(), figure_png=False)
    erc.export_corpus(cache, out, [], generated_at="x", min_chars=0, display_name="HR policy handbook")
    generated = ((out / "README.md").read_text(encoding="utf-8")
                 + (out / "COPILOT-INSTRUCTIONS.snippet.md").read_text(encoding="utf-8")).lower()
    assert "hr policy handbook" in generated
    for term in ("hardware", "firmware", "register", "pin ", "electrical", "datasheet"):
        assert term not in generated, f"domain term '{term}' leaked into generic export output"
