"""Verify the signed corpus: every expected stamp exists, sits to the right of the
banking block, overlaps nothing, and no decoy/other page got stamped."""
import json
import sys
from pathlib import Path

import fitz

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from sign_invoice import SIG_HEIGHT, banking_block_rect, find_anchor  # noqa: E402

CORPUS = HERE / "corpus"
SIGNED = HERE / "corpus_signed"
manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))

fails, notes, stamped_docs = [], [], 0
for m in manifest:
    signed = SIGNED / (m["file"][:-4] + "_signed.pdf")
    if not signed.exists():
        if m["expect_space"]:
            fails.append((m["file"], "expected a stamp but no signed output was produced"))
        else:
            notes.append((m["file"], "correctly skipped (no empty space)"))
        continue
    doc = fitz.open(signed)
    doc_stamped = False
    for pno, page in enumerate(doc, 1):
        imgs = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
        if pno != m["bank_page"]:
            if imgs:
                fails.append((m["file"], f"page {pno}: stamp on a non-banking page (decoy?)"))
            continue
        if m["expect_space"] and not imgs:
            fails.append((m["file"], f"page {pno}: expected stamp, none found"))
            continue
        if not imgs:
            continue
        doc_stamped = True
        words = [fitz.Rect(w[:4]) for w in page.get_text("words")]
        anchor = find_anchor(page)
        block = banking_block_rect(page, anchor) if anchor else None
        for r in imgs:
            hits = [w for w in words if r.intersects(w)]
            if hits:
                fails.append((m["file"], f"page {pno}: stamp overlaps text at {hits[0]}"))
            if block is None:
                fails.append((m["file"], f"page {pno}: stamped but no anchor re-found"))
            elif r.x0 < block.x1 - 2:
                fails.append((m["file"], f"page {pno}: stamp x0={r.x0:.0f} not right of block x1={block.x1:.0f}"))
            else:
                # strict row rule: stamp inside the block's horizontal band
                # (short blocks: centred, so allow symmetric overhang)
                slack = max(0.0, (SIG_HEIGHT - block.height) / 2) + 4
                if r.y0 < block.y0 - slack or r.y1 > block.y1 + slack:
                    fails.append((m["file"], f"page {pno}: stamp y=[{r.y0:.0f},{r.y1:.0f}] "
                                             f"outside block row [{block.y0:.0f},{block.y1:.0f}]"))
        if not m["expect_space"]:
            notes.append((m["file"], f"page {pno}: found clean space despite crowding (ok)"))
    if doc_stamped:
        stamped_docs += 1
    doc.close()

print(f"documents: {len(manifest)} | stamped: {stamped_docs} | failures: {len(fails)}")
for f, msg in fails:
    print(f"  FAIL {f}: {msg}")
for f, msg in notes:
    print(f"  note {f}: {msg}")
