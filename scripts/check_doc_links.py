#!/usr/bin/env python3
"""Verify every internal markdown link and #anchor in this pattern resolves.

Run from the repo root:  python scripts/check_doc_links.py

Uses GitHub anchor slugging (lowercase, drop punctuation, each space -> one
hyphen). Restructuring the docs silently breaks cross-references otherwise --
this caught 8 real breaks during the 2026-08-21 hybrid docs rewrite.
"""
import re, pathlib, urllib.parse
root = pathlib.Path(".")
files = list(root.glob("*.md")) + list(root.glob("docs/*.md")) + list(root.glob("samples/*.md")) + list(root.glob("tests/*.md"))
def slug(t):
    t = re.sub(r"<[^>]+>", "", t)     # GitHub ignores inline HTML (icon <img>) in anchors
    t = t.strip().lower()
    t = re.sub(r"[^\w\s-]", "", t)   # drop punctuation incl. em dash
    return t.replace(" ", "-")        # each space -> one hyphen (GitHub behaviour)
anchors = {}
for f in files:
    hs = re.findall(r"(?m)^#{1,6}\s+(.*)$", f.read_text(encoding="utf-8"))
    anchors[f.as_posix()] = {slug(h) for h in hs}
bad = []
for f in files:
    txt = f.read_text(encoding="utf-8")
    for m in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", txt):
        link = m.group(1).strip()
        if link.startswith(("http://","https://","mailto:")): continue
        path_part, _, frag = link.partition("#")
        path_part = urllib.parse.unquote(path_part)
        target = (f.parent / path_part).resolve() if path_part else f.resolve()
        if path_part and not target.exists():
            bad.append(f"{f.as_posix()} -> MISSING FILE {link}"); continue
        if frag:
            try: rel = target.relative_to(root.resolve()).as_posix()
            except Exception: continue
            if rel in anchors and frag.lower() not in anchors[rel]:
                bad.append(f"{f.as_posix()} -> #{frag} not in {rel}")
print(f"checked {len(files)} files")
print(f"{len(bad)} broken" if bad else "all internal links and anchors resolve")
for b in bad: print("  " + b)
