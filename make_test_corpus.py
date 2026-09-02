"""Generate a 40-document test corpus of Corient-style invoices.

Varies: page count (1-4), which page holds the banking block, block layout
(two-column label/value like the real invoice, single-column, compact one-liner,
label-only with no heading), heading wording, block position, crowded pages with
no empty space to the right, and decoy pages that mention "banking details" only
in running text (must NOT be stamped).

Writes corpus/doc_001.pdf ... doc_040.pdf and corpus/manifest.json describing
what each document contains and whether a stamp is expected.
"""
import json
import random
from pathlib import Path

import fitz

HERE = Path(__file__).parent
OUT = HERE / "corpus"
OUT.mkdir(exist_ok=True)
random.seed(42)

PAGE_W, PAGE_H = 595, 842
HEADINGS = ["Banking Details", "Bank Details", "Payment Details", "Banking details"]
LAYOUTS = ["two_col", "single_col", "compact", "label_only"]

BANKS = [
    ("Barclays Bank PLC", "Stonehage Corient Ltd", "63254178", "20-45-77",
     "BARCGB22 / GB29 BARC 2045 7763 2541 78"),
    ("UBS Switzerland AG", "Corient Advisory SA", "0230-00123456.7", "0230",
     "UBSWCHZH80A / CH93 0076 2011 6238 5295 7"),
    ("HSBC Bank plc", "Corient Fleming Advisory (Monaco)", "71482935", "40-05-15",
     "HBUKGB4B / GB33 HBUK 4005 1571 4829 35"),
]


def header(page, pno, npages):
    page.insert_text((230, 55), "C O R I E N T", fontsize=20, fontname="times-roman")
    page.draw_line(fitz.Point(30, 105), fitz.Point(PAGE_W - 30, 105), width=0.7)
    page.insert_text((PAGE_W - 120, 95), f"Page {pno} of {npages}", fontsize=10)


def fees_table(page, y, rows=4):
    page.insert_text((30, y), "Time based fees by Work type", fontsize=10, fontname="hebo")
    page.insert_text((30, y + 18), "Staff/Code", fontsize=9, fontname="hebo")
    page.insert_text((120, y + 18), "Name", fontsize=9, fontname="hebo")
    page.insert_text((450, y + 18), "Hours", fontsize=9, fontname="hebo")
    page.insert_text((510, y + 18), "Amount", fontsize=9, fontname="hebo")
    data = [("JMV", "Advisory services rendered", "6.50", "2,275.00"),
            ("KLR", "Portfolio review and reporting", "3.25", "1,137.50"),
            ("TPD", "Client meeting preparation", "2.00", "700.00"),
            ("AAB", "Regulatory correspondence", "1.75", "612.50"),
            ("MNO", "Structuring analysis", "4.00", "1,400.00")]
    for i, (c, n, h, a) in enumerate(data[:rows]):
        page.insert_text((30, y + 36 + i * 15), c, fontsize=9)
        page.insert_text((120, y + 36 + i * 15), n, fontsize=9)
        page.insert_text((450, y + 36 + i * 15), h, fontsize=9)
        page.insert_text((510, y + 36 + i * 15), a, fontsize=9)
    return y + 36 + rows * 15


def disb_table(page, y):
    page.insert_text((30, y), "Disbursements", fontsize=10, fontname="hebo")
    page.draw_line(fitz.Point(30, y + 6), fitz.Point(PAGE_W - 30, y + 6), width=0.5)
    page.insert_text((30, y + 22), "Date", fontsize=9, fontname="hebo")
    page.insert_text((120, y + 22), "Staff/Code", fontsize=9, fontname="hebo")
    page.insert_text((220, y + 22), "Description", fontsize=9, fontname="hebo")
    for i, (d, s, de) in enumerate([("12/08/2026", "JMV", "Courier charges"),
                                    ("15/08/2026", "KLR", "Registry filing fee")]):
        page.insert_text((30, y + 38 + i * 15), d, fontsize=9)
        page.insert_text((120, y + 38 + i * 15), s, fontsize=9)
        page.insert_text((220, y + 38 + i * 15), de, fontsize=9)
    return y + 68


def terms_paragraph(page, y):
    """Decoy: mentions banking details in running text. Must NOT be stamped."""
    for i, line in enumerate([
        "Payment is due within 30 days by transfer to our banking details and by quoting the reference",
        "number shown above. Should we hold funds on account the invoice will be settled from those",
        "funds. For fees please go to our client portal for a full breakdown of charges.",
    ]):
        page.insert_text((30, y + i * 13), line, fontsize=9)


def banking_block(page, x, y, layout, heading, bank):
    """Draw the block; return (approx_right_edge, approx_bottom)."""
    bname, acct_name, acct_no, branch, swift = bank
    if layout == "two_col":
        page.insert_text((x, y), heading, fontsize=11, fontname="hebo")
        vx = x + 110
        rows = [("Bank", [bname]),
                ("Address", ["5 The North Colonnade", "St Helier", "Jersey JE2 3RA"]),
                ("Account Name", [acct_name]),
                ("Account Number", [acct_no]),
                ("Branch", [branch]),
                ("SWIFT Code / IBAN", [swift]),
                ("Reference", ["INV-2026-0912"])]
        yy = y + 18
        for label, values in rows:
            page.insert_text((x, yy), label, fontsize=9)
            for v in values:
                page.insert_text((vx, yy), v, fontsize=9)
                yy += 13
        return x + 110 + 200, yy
    if layout == "single_col":
        page.insert_text((x, y), heading, fontsize=11, fontname="hebo")
        for i, line in enumerate([f"Account name: {acct_name}", f"Account number: {acct_no}",
                                  f"SWIFT / IBAN: {swift}", f"Bank: {bname}"]):
            page.insert_text((x, y + 18 + i * 13), line, fontsize=9)
        return x + 240, y + 18 + 4 * 13
    if layout == "compact":
        page.insert_text((x, y), heading, fontsize=11, fontname="hebo")
        page.insert_text((x, y + 16), f"{bname}  |  {acct_no}  |  {swift.split(' / ')[1]}", fontsize=8)
        return x + 250, y + 24
    # label_only: no heading at all -> tests the IBAN/Account-Number fallback
    for i, line in enumerate([f"Account Number {acct_no}", f"IBAN {swift.split(' / ')[1]}",
                              f"Bank {bname}"]):
        page.insert_text((x, y + i * 13), line, fontsize=9)
    return x + 230, y + 3 * 13


def crowd_right(page, y0, y1):
    """Fill the right side of the block's band so there is no empty space."""
    yy = y0
    i = 0
    lines = ["Please retain this document for your records and note",
             "that all amounts are stated inclusive of applicable",
             "taxes and duties where relevant to your jurisdiction.",
             "Queries should be addressed to your usual contact",
             "within ten business days of the invoice date shown.",
             "This document is issued subject to our standard",
             "terms of business as amended from time to time."]
    while yy < y1 + 30:
        page.insert_text((330, yy), lines[i % len(lines)], fontsize=8)
        yy += 12
        i += 1


manifest = []
for n in range(1, 41):
    npages = (n - 1) % 4 + 1
    layout = LAYOUTS[(n - 1) % 4]
    heading = HEADINGS[(n - 1) % 4]
    bank = BANKS[n % 3]
    bank_page = random.randint(1, npages)
    # right-shifted narrow layouts test the "no room on the right" case
    tight = n % 8 == 0 and layout in ("single_col", "compact", "label_only")
    x = 320 if tight else random.choice([30, 57, 60])
    y = random.choice([380, 480, 600])
    crowded = (not tight) and n % 5 == 0
    est_x1 = {"two_col": x + 310, "single_col": x + 240, "compact": x + 250, "label_only": x + 230}[layout]
    expect_space = (not crowded) and (PAGE_W - est_x1 - 16 >= 186)
    distractor_page = random.randint(1, npages) if n % 2 == 0 else None

    doc = fitz.open()
    for p in range(1, npages + 1):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        header(page, p, npages)
        content_y = 150
        if p == 1:
            page.insert_text((30, 125), f"Invoice 2026-{1000 + n}  |  Date: 02/09/2026", fontsize=9)
            page.insert_text((300, 125), f"For the account of: {bank[1]}", fontsize=9)
        content_y = fees_table(page, content_y, rows=2 + (n + p) % 3) + 20
        if p % 2 == 0 or npages == 1:
            content_y = disb_table(page, content_y) + 10
        if p == bank_page:
            _, bottom = banking_block(page, x, y, layout, heading, bank)
            if crowded:
                crowd_right(page, y - 5, bottom)
        if distractor_page == p:
            terms_paragraph(page, 760)
    fname = f"doc_{n:03d}.pdf"
    doc.save(OUT / fname)
    doc.close()
    manifest.append({"file": fname, "pages": npages, "bank_page": bank_page,
                     "layout": layout, "heading": None if layout == "label_only" else heading,
                     "x": x, "y": y, "crowded": crowded, "tight": tight,
                     "expect_space": expect_space, "distractor_page": distractor_page})

(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
counts = {"total": len(manifest),
          "expect_stamp": sum(m["expect_space"] for m in manifest),
          "expect_no_space": sum(not m["expect_space"] for m in manifest),
          "with_decoy_text": sum(m["distractor_page"] is not None for m in manifest)}
print(json.dumps(counts))
