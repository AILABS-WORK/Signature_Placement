# Signature Placement

Stamps a signature image onto PDF invoices, always to the **right of the Banking
Details block** — wherever that block sits (any page, any position) — and strictly
level with it (inside the block's horizontal row, never above or below). Placement
is verified against everything already on the page, so the signature never overlaps
existing content; if there is no clear space in the row, the document is skipped
with a clear status instead.

## What's here

| Path | Purpose |
|---|---|
| `sign_invoice.py` | The engine (Python + PyMuPDF). Anchor detection, row-constrained empty-space search, stamping. |
| `dist/sign_invoice.exe` | The engine compiled into a single self-contained Windows exe (no Python needed) — this is what gets deployed to the Laserfiche Workflow Server. |
| `laserfiche/LFStamp_SDKScript.cs` | C# body for the Laserfiche Workflow SDK Script activity: exports invoice + signature from the repository, runs the exe, imports the signed PDF, sets routing tokens. |
| `laserfiche/README_LASERFICHE.md` | Full server + repository + Workflow Designer setup guide. |
| `miguel_villax_sig.png` | Test signature image (transparent background). |
| `signatures/` | Per-account signature images for Phase 2 (filename = account name). |
| `make_test_corpus.py` | Generates the 40-document synthetic test corpus (1–4 pages, four banking-block layouts, crowded pages, decoy text). |
| `verify_corpus.py` | Verifies every stamp: right of block, inside the row, zero overlap, no decoy-page stamps. |
| `check_matching.py` | Verifies Phase-2 signature-to-account matching. |

## Usage

```
# one invoice, one signature (Phase 1 / Laserfiche mode)
sign_invoice.exe invoice.pdf --signature signature.png --out-file signed.pdf

# batch a folder
sign_invoice.exe in_folder --signature signature.png --out out_folder

# Phase 2: per-account signatures matched by filename against document text
sign_invoice.exe invoice.pdf --signatures sig_folder --out-file signed.pdf
```

Exit codes: `0` stamped · `2` no banking details found · `3` no empty space in the
row · `4` no matching signature · `1` bad arguments. The last stdout line is
`RESULT {json}` with pages/coordinates for machine consumption.

## Test status

40-document corpus: 28/28 expected stamps placed inside the row with zero overlaps,
12/12 no-space documents correctly skipped, 0 false stamps on decoy pages,
28/28 correct Phase-2 signature matches.

## Rebuilding the exe

```
python -m venv venv && venv\Scripts\pip install pymupdf pyinstaller
venv\Scripts\python -m PyInstaller --onefile --name sign_invoice sign_invoice.py
```

Build from a clean venv (building from a global Python drags unrelated packages in).
