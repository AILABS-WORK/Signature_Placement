r"""
sign_invoice.py — stamp a signature image next to the banking details of a PDF invoice.

The banking block can be anywhere on any page: the script finds it by searching for a
heading (e.g. "Banking Details") or label lines (e.g. "IBAN ..."), measures the full
block including the value column, then scans the area to the RIGHT of the block for
genuinely empty white space (no text, no images, no vector drawings) and stamps there.

Signature selection (first that applies):
  1. --signature <image>            explicit file
  2. --signatures <folder>          images named after the account name, e.g.
                                    "Corient Advisory SA.png" — matched against the
                                    document text (case/punctuation-insensitive)
  3. signatures.json                {"account name": "image.png"} next to the input

Usage:
  python sign_invoice.py invoice.pdf --signatures \\server\sigs --out-file signed.pdf
  python sign_invoice.py in_folder --signatures sigs_folder --out out_folder

Machine output: the last stdout line is "RESULT {json}" with status/pages/signature.
Exit codes: 0 = stamped, 2 = no banking details found, 3 = no empty space,
            4 = no matching signature, 1 = bad arguments / IO error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

VERSION = "1.3.0"  # centered-gap placement

# Headings that mark the banking-details block. Only accepted when the line is
# essentially just this text (so a sentence merely mentioning "banking details"
# in running text is ignored). First term found wins.
ANCHOR_TERMS = [
    "banking details", "bank details", "bank account details",
    "payment details", "remittance details",
]
# Fallback label-style anchors: accepted when a line STARTS with the term.
LABEL_TERMS = ["iban", "account number"]
# Max gap between the label column and the value column of the block (pt).
COLUMN_GAP_MAX = 160
# How far below the anchor line we still consider text part of the banking block (pt).
BLOCK_REACH_DOWN = 110
# Desired stamp size (pt). 1pt = 1/72". 170x60 ≈ 60x21 mm.
SIG_WIDTH, SIG_HEIGHT = 170, 60
# Clearance required around the stamp (pt).
PAD = 8
# Step used when sliding the candidate window (pt).
STEP = 10

SIG_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

# exit codes
OK, ERR_ARGS, NO_ANCHOR, NO_SPACE, NO_SIGNATURE = 0, 1, 2, 3, 4


def page_lines(page: fitz.Page):
    """All text lines on the page as (rect, text)."""
    out = []
    for tb in page.get_text("dict")["blocks"]:
        for line in tb.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                out.append((fitz.Rect(line["bbox"]), text))
    return out


def find_anchor(page: fitz.Page):
    """Rect of the banking heading. Heading-style only: the line must essentially BE
    the anchor term, so in-sentence mentions ('...to our banking details and...')
    never match. Falls back to label-style lines ('IBAN: ...')."""
    lines = page_lines(page)
    for term in ANCHOR_TERMS:
        for lr, text in lines:
            t = text.lower().rstrip(":").strip()
            if t == term or (t.startswith(term) and len(t) <= len(term) + 3):
                return lr
    for term in LABEL_TERMS:
        for lr, text in lines:
            if text.lower().startswith(term):
                return lr
    return None


def banking_block_rect(page: fitz.Page, anchor: fitz.Rect) -> fitz.Rect:
    """Union of the anchor, the label lines under it, and the adjacent value column
    (two-column layouts: Bank / Address / Account Name on the left, values right)."""
    lines = page_lines(page)
    block = fitz.Rect(anchor)
    # label column: lines starting near the anchor's left edge, within reach below it
    for lr, _ in lines:
        if abs(lr.x0 - anchor.x0) < 30 and anchor.y0 <= lr.y0 <= anchor.y1 + BLOCK_REACH_DOWN:
            block |= lr
    # value column(s): lines inside the block's vertical band starting just right of it
    grew = True
    while grew:
        grew = False
        for lr, _ in lines:
            inside_band = lr.y0 >= block.y0 - 3 and lr.y1 <= block.y1 + 3
            adjacent = block.x1 - 5 <= lr.x0 <= block.x1 + COLUMN_GAP_MAX
            if inside_band and adjacent and lr.x1 > block.x1:
                block |= lr
                grew = True
    return block


def occupied_rects(page: fitz.Page):
    """Everything already printed on the page: words, images, vector drawings."""
    rects = [fitz.Rect(w[:4]) for w in page.get_text("words")]
    rects += [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    rects += [d["rect"] for d in page.get_drawings()]
    return rects


def find_empty_spot(page: fitz.Page, block: fitz.Rect):
    """Place the signature in the white space to the RIGHT of the banking block:
    take the block's full top/bottom band, find the horizontal gaps between
    everything printed inside that band and the page edge, pick the widest gap
    that fits, and CENTRE the signature in it — both horizontally (equal space
    left/right of the signature) and vertically (equal space above/below within
    the band). STRICT ROW RULE: never above or below the block's band; a block
    shorter than the signature gets it centred on its row. Falls back to a
    closest-fit scan if the centred spot is somehow blocked."""
    occupied = occupied_rects(page)
    page_right = page.rect.width - PAD
    if block.x1 + PAD + SIG_WIDTH > page_right:
        return None

    # vertical: centred on the band (clamped to the page)
    y = block.y0 + (block.height - SIG_HEIGHT) / 2
    y = min(max(y, PAD), page.rect.height - SIG_HEIGHT - PAD)

    # horizontal: merge the x-intervals of everything overlapping the band,
    # then look at the gaps to the right of the block
    ivals = sorted(
        (r.x0, r.x1) for r in occupied
        if r.y1 > block.y0 - 2 and r.y0 < block.y1 + 2
    )
    merged = []
    for a, b in ivals:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps, prev = [], block.x1
    for a, b in merged:
        if b <= prev:
            continue
        if a > prev:
            gaps.append((prev, min(a, page_right)))
        prev = max(prev, b)
    if prev < page_right:
        gaps.append((prev, page_right))

    need = SIG_WIDTH + 2 * PAD
    for g0, g1 in sorted(gaps, key=lambda g: g[1] - g[0], reverse=True):
        if g1 - g0 < need:
            continue
        x = g0 + ((g1 - g0) - SIG_WIDTH) / 2  # centred in the gap
        cand = fitz.Rect(x, y, x + SIG_WIDTH, y + SIG_HEIGHT)
        padded = cand + (-PAD, -PAD, PAD, PAD)
        if not any(padded.intersects(r) for r in occupied):
            print(f"  placement: centered in gap x=[{g0:.0f},{g1:.0f}], "
                  f"band y=[{block.y0:.0f},{block.y1:.0f}]")
            return cand

    # fallback: closest-fit scan inside the band (previous behaviour)
    y_mid = block.y0 + (block.height - SIG_HEIGHT) / 2
    if block.height >= SIG_HEIGHT:
        y_lo, y_hi = block.y0, block.y1 - SIG_HEIGHT
        ys = sorted(
            {y_mid, y_hi} | {y_lo + i * STEP for i in range(int((y_hi - y_lo) / STEP) + 1)},
            key=lambda yy: abs(yy - y_mid),
        )
    else:
        ys = [min(max(y_mid, PAD), page.rect.height - SIG_HEIGHT - PAD)]
    x = block.x1 + PAD
    x_end = page.rect.width - SIG_WIDTH - PAD
    while x <= x_end:
        for yy in ys:
            cand = fitz.Rect(x, yy, x + SIG_WIDTH, yy + SIG_HEIGHT)
            padded = cand + (-PAD, -PAD, PAD, PAD)
            if not any(padded.intersects(r) for r in occupied):
                print(f"  placement: fallback scan (no clean gap; gaps={[(round(a), round(b)) for a, b in gaps]})")
                return cand
        x += STEP
    return None


_SIG_PIXMAPS: dict = {}


def load_signature_pixmap(sig_path: Path):
    """Load the signature image tolerantly and return a fitz.Pixmap, or None.
    Laserfiche often re-encodes imported images (e.g. to TIFF) regardless of the
    file name, so try: raw image decode -> PDF page render -> Pillow conversion."""
    if sig_path in _SIG_PIXMAPS:
        return _SIG_PIXMAPS[sig_path]
    pix = None
    try:
        data = sig_path.read_bytes()
    except OSError:
        data = b""
    if data:
        try:
            pix = fitz.Pixmap(data)
        except Exception:
            pix = None
        if pix is None and data[:5] == b"%PDF-":
            try:
                with fitz.open(stream=data, filetype="pdf") as sdoc:
                    pix = sdoc[0].get_pixmap(dpi=300, alpha=True)
            except Exception:
                pix = None
        if pix is None:
            try:
                import io

                from PIL import Image
                buf = io.BytesIO()
                Image.open(io.BytesIO(data)).convert("RGBA").save(buf, format="PNG")
                pix = fitz.Pixmap(buf.getvalue())
            except Exception:
                pix = None
    _SIG_PIXMAPS[sig_path] = pix
    return pix


def normalize(s: str) -> str:
    """Lowercase, collapse punctuation/whitespace to single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def pick_signature_from_folder(page_text: str, sig_dir: Path):
    """Match signature image filenames (named after the account name) against the
    document text. Longest matching name wins so 'Corient Advisory SA' beats
    'Corient' if both files exist."""
    text = " " + normalize(page_text) + " "
    best, best_len = None, 0
    for f in sorted(sig_dir.iterdir()):
        if f.suffix.lower() not in SIG_EXTENSIONS:
            continue
        phrase = normalize(f.stem)
        if phrase and f" {phrase} " in text and len(phrase) > best_len:
            best, best_len = f, len(phrase)
    return best


def pick_signature_from_map(page_text: str, mapping: dict, base: Path):
    lowered = page_text.lower()
    for name, sig_path in mapping.items():
        if name.lower() in lowered:
            return (base / sig_path).resolve()
    return None


def sign_pdf(pdf_path: Path, out_path: Path, sig_dir: Path | None,
             mapping: dict, forced_sig: Path | None):
    """Returns (exit_code, result_dict)."""
    doc = fitz.open(pdf_path)
    # account-name matching uses the WHOLE document's text: the account holder may
    # be named on page 1 while the banking block sits on a later page
    doc_text = "".join(p.get_text() for p in doc)
    stamped, worst = [], NO_ANCHOR
    for page in doc:
        anchor = find_anchor(page)
        if not anchor:
            continue
        block = banking_block_rect(page, anchor)
        spot = find_empty_spot(page, block)
        if spot is None:
            worst = max(worst, NO_SPACE)
            print(f"  page {page.number + 1}: banking details found but no empty space to the right")
            continue
        if forced_sig:
            sig = forced_sig
        elif sig_dir:
            sig = pick_signature_from_folder(doc_text, sig_dir)
        else:
            sig = pick_signature_from_map(doc_text, mapping, pdf_path.parent)
        if sig is None or not sig.exists():
            worst = max(worst, NO_SIGNATURE)
            print(f"  page {page.number + 1}: no matching signature image")
            continue
        pix = load_signature_pixmap(sig)
        if pix is None:
            worst = max(worst, NO_SIGNATURE)
            print(f"  page {page.number + 1}: signature image unreadable/empty: {sig.name}")
            continue
        page.insert_image(spot, pixmap=pix, keep_proportion=True)
        stamped.append({"page": page.number + 1, "signature": sig.name,
                        "x": round(spot.x0), "y": round(spot.y0)})

    if stamped:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(out_path)
        for s in stamped:
            print(f"  page {s['page']}: stamped '{s['signature']}' at x={s['x']}, y={s['y']}"
                  f" -> {out_path.name}")
        code, status = OK, "stamped"
    else:
        code = worst
        status = {NO_ANCHOR: "no_banking_details", NO_SPACE: "no_empty_space",
                  NO_SIGNATURE: "no_matching_signature"}[worst]
        print(f"  nothing stamped ({status})")
    doc.close()
    return code, {"status": status, "input": str(pdf_path),
                  "output": str(out_path) if stamped else None, "stamps": stamped}


def main():
    ap = argparse.ArgumentParser(description="Stamp signature next to banking details in PDF invoices")
    ap.add_argument("input", help="PDF file or folder of PDFs")
    ap.add_argument("--signatures", help="folder of signature images named after account names")
    ap.add_argument("--signature", help="explicit signature image (overrides matching)")
    ap.add_argument("--map", default="signatures.json", help="account-name -> image mapping file")
    ap.add_argument("--out", help="output folder")
    ap.add_argument("--out-file", help="exact output file path (single-invoice mode)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"input not found: {src}")
        sys.exit(ERR_ARGS)
    files = sorted(src.glob("*.pdf")) if src.is_dir() else [src]
    files = [f for f in files if not f.stem.endswith("_signed")]
    if not files:
        print("no PDFs found")
        sys.exit(ERR_ARGS)

    sig_dir = Path(args.signatures) if args.signatures else None
    if sig_dir and not sig_dir.is_dir():
        print(f"signatures folder not found: {sig_dir}")
        sys.exit(ERR_ARGS)
    forced = Path(args.signature).resolve() if args.signature else None

    mapping = {}
    if not sig_dir and not forced:
        map_path = Path(args.map) if Path(args.map).is_absolute() else \
            (src if src.is_dir() else src.parent) / args.map
        if not map_path.exists():
            print("no --signatures/--signature given and no signatures.json found")
            sys.exit(ERR_ARGS)
        mapping = json.loads(map_path.read_text(encoding="utf-8"))

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    print(f"sign_invoice v{VERSION}")
    worst, results = OK, []
    for f in files:
        print(f"{f.name}:")
        if args.out_file and len(files) == 1:
            out = Path(args.out_file)
        else:
            out = (out_dir or f.parent) / f"{f.stem}_signed.pdf"
        try:
            code, result = sign_pdf(f, out, sig_dir, mapping, forced)
        except Exception as e:
            print(f"  error: {type(e).__name__}: {e}")
            code = ERR_ARGS
            result = {"status": "error", "input": str(f), "output": None,
                      "stamps": [], "error": f"{type(e).__name__}: {e}"}
        worst = max(worst, code)
        results.append(result)

    print("RESULT " + json.dumps(results[0] if len(results) == 1 else results))
    sys.exit(worst)


if __name__ == "__main__":
    main()
