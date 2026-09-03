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

VERSION = "1.8.0"  # ignore invisible text/images too; richer --explain

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
# Desired stamp size (pt). 1pt = 1/72". 140x50 ≈ 49x18 mm.
SIG_WIDTH, SIG_HEIGHT = 140, 50
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


def _is_invisible(color) -> bool:
    """True for no colour at all or (near-)white — nothing the eye can see."""
    return color is None or min(color) > 0.9


def _white_text_rects(page: fitz.Page):
    """Bounding boxes of (near-)white text: invisible OCR layers and hidden
    template text, which must not be treated as obstacles."""
    out = []
    for tb in page.get_text("dict")["blocks"]:
        for line in tb.get("lines", []):
            for span in line.get("spans", []):
                c = span.get("color", 0)
                rgb = (((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)
                if min(rgb) > 0.9:
                    out.append(fitz.Rect(span["bbox"]))
    return out


def occupied_rects(page: fitz.Page):
    """Everything VISIBLY printed on the page: words, images, vector drawings.

    Objects the reader cannot see must not push the signature around, so this
    skips white/uncoloured fills and strokes (invoice templates are full of
    them), (near-)white text such as invisible OCR layers, and any single
    drawing or image large enough to be a page backdrop."""
    page_area = page.rect.get_area() or 1.0
    invisible_text = _white_text_rects(page)

    rects = []
    for w in page.get_text("words"):
        r = fitz.Rect(w[:4])
        if any(r in wr for wr in invisible_text):
            continue
        rects.append(r)

    for i in page.get_image_info():
        r = fitz.Rect(i["bbox"])
        if r.get_area() > 0.6 * page_area:
            continue                                   # page-sized backdrop
        rects.append(r)

    for d in page.get_drawings():
        fill, stroke = d.get("fill"), d.get("color")
        if _is_invisible(fill) and _is_invisible(stroke):
            continue                                   # nothing visible drawn
        if d.get("type") == "f" and _is_invisible(fill):
            continue                                   # invisible background fill
        r = d["rect"]
        if r.get_area() > 0.6 * page_area:
            continue                                   # page-sized backdrop
        rects.append(r)
    return rects


# If a full-size signature has no room, retry at these scales before giving up.
SIG_SCALES = [1.0, 0.85, 0.7]
# Grid resolution for the placement search (pt).
GRID = 6
# A spot only counts as comfortable with at least this much space on all sides.
GOOD_CLEARANCE = 16
# Vertical offsets within this many pt of each other count as equally centred.
VBUCKET = 8
# Cost, in pt of virtual vertical offset, charged for shrinking the signature.
# Higher = keep the signature big; lower = shrink more readily to stay centred.
SCALE_PENALTY = 40


def _place_at_size(page, block, occupied, page_right, w, h, min_clear):
    """Best placement of a w x h signature to the right of the block.
    Vertical centring on the block's band is the PRIMARY goal; at the chosen
    height the signature is centred within the run of usable white space.
    Returns (rect, vertical_offset, clearance) or None."""
    band_y0, band_y1 = block.y0, block.y1
    x_min, x_max = block.x1 + PAD, page_right - w
    if x_max < x_min:
        return None
    if band_y1 - band_y0 >= h:
        y_min, y_max = band_y0, band_y1 - h
    else:  # block shorter than the signature: only its own row is allowed
        yc = min(max(band_y0 + (band_y1 - band_y0 - h) / 2, PAD),
                 page.rect.height - h - PAD)
        y_min = y_max = yc
    y_pref = band_y0 + (band_y1 - band_y0 - h) / 2  # perfectly centred on the band

    rel = [r for r in occupied
           if r.x1 > x_min - 80 and r.x0 < page_right + 80
           and r.y1 > y_min - 80 and r.y0 < y_max + h + 80]

    def clearance(cand):
        c = min(cand.x0 - block.x1, page_right - cand.x1)
        for r in rel:
            if cand.intersects(r):
                return -1.0
            dx = max(r.x0 - cand.x1, cand.x0 - r.x1, 0.0)
            dy = max(r.y0 - cand.y1, cand.y0 - r.y1, 0.0)
            c = min(c, (dx * dx + dy * dy) ** 0.5)
            if c < min_clear:
                return c
        return c

    best = None  # (sort key, rect, vertical offset, clearance, span)
    y = y_min
    while y <= y_max + 0.01:
        # usable left-edge positions at this height, grouped into runs
        runs, cur, x = [], [], x_min
        while x <= x_max + 0.01:
            c = clearance(fitz.Rect(x, y, x + w, y + h))
            if c >= min_clear:
                cur.append((x, c))
            elif cur:
                runs.append(cur)
                cur = []
            x += GRID
        if cur:
            runs.append(cur)
        if runs:
            run = max(runs, key=len)          # widest white space at this height
            x_at, c_at = run[len(run) // 2]   # centred within it
            voff = abs(y - y_pref)
            key = (round(voff / VBUCKET), -len(run))
            if best is None or key < best[0]:
                span = (run[0][0], run[-1][0] + w)
                best = (key, fitz.Rect(x_at, y, x_at + w, y + h), voff, c_at, span)
        y += GRID
    if best is None:
        return None
    return best[1], best[2], best[3], best[4]


def find_empty_spot(page: fitz.Page, block: fitz.Rect):
    """Place the signature in the white space to the RIGHT of the banking block.

    Vertically it is centred on the block's band (never above or below it);
    horizontally it is centred in the white space available at that height.
    Sizes are tried largest-first and a smaller signature wins only when it buys
    materially better vertical centring (see SCALE_PENALTY). A first pass demands
    comfortable clearance on all sides; a second accepts tighter spots."""
    occupied = occupied_rects(page)
    page_right = page.rect.width - PAD

    for min_clear, label in ((GOOD_CLEARANCE, "centered"), (PAD, "centered (tight)")):
        options = []
        for scale in SIG_SCALES:
            got = _place_at_size(page, block, occupied, page_right,
                                 SIG_WIDTH * scale, SIG_HEIGHT * scale, min_clear)
            if got is not None:
                rect, voff, clear, span = got
                options.append((voff + SCALE_PENALTY * (1 - scale), scale,
                                rect, voff, clear, span))
        if options:
            _, scale, rect, voff, clear, span = min(options, key=lambda o: o[0])
            print(f"  placement: {label}, v-offset={voff:.0f}pt, "
                  f"clearance={clear:.0f}pt, scale={scale:g}, "
                  f"band y=[{block.y0:.0f},{block.y1:.0f}], "
                  f"block x1={block.x1:.0f}, hspace=[{span[0]:.0f},{span[1]:.0f}], "
                  f"page w={page.rect.width:.0f}, at x={rect.x0:.0f}")
            return rect
    return None


def explain_page(page: fitz.Page, block: fitz.Rect):
    """Dump everything in the block's band right of it (--explain): the
    obstacles the search honours, and the objects it deliberately ignores."""
    print(f"  EXPLAIN page {page.number + 1}: page={page.rect.width:.0f}x"
          f"{page.rect.height:.0f}, block=[{block.x0:.0f},{block.y0:.0f},"
          f"{block.x1:.0f},{block.y1:.0f}]")

    def in_band(r):
        return r.y1 > block.y0 - 2 and r.y0 < block.y1 + 2 and r.x1 > block.x1

    words = {tuple(round(v) for v in w[:4]): w[4] for w in page.get_text("words")}
    for r in occupied_rects(page):
        if in_band(r):
            label = words.get(tuple(round(v) for v in (r.x0, r.y0, r.x1, r.y1)), "")
            print(f"    OBSTACLE {'word' if label else 'image/drawing'}: "
                  f"[{r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}] {label}")

    for w in page.get_text("words"):
        r = fitz.Rect(w[:4])
        if in_band(r) and any(r in wr for wr in _white_text_rects(page)):
            print(f"    ignored white text: [{r.x0:.0f},{r.y0:.0f},"
                  f"{r.x1:.0f},{r.y1:.0f}] {w[4]}")
    for i in page.get_image_info():
        r = fitz.Rect(i["bbox"])
        if in_band(r) and r.get_area() > 0.6 * (page.rect.get_area() or 1.0):
            print(f"    ignored big image: [{r.x0:.0f},{r.y0:.0f},{r.x1:.0f},{r.y1:.0f}]")
    for d in page.get_drawings():
        r = d["rect"]
        if in_band(r):
            print(f"    drawing type={d.get('type')} fill={d.get('fill')} "
                  f"stroke={d.get('color')} rect=[{r.x0:.0f},{r.y0:.0f},"
                  f"{r.x1:.0f},{r.y1:.0f}]")


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
             mapping: dict, forced_sig: Path | None, explain: bool = False):
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
        if explain:
            explain_page(page, block)
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
    ap.add_argument("--explain", action="store_true",
                    help="dump the page geometry the placement search sees")
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
            code, result = sign_pdf(f, out, sig_dir, mapping, forced, args.explain)
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
