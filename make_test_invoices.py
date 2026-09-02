"""Generate test invoices (banking details in a different spot each time) and fake signature PNGs."""
from pathlib import Path
import fitz

HERE = Path(__file__).parent
OUT = HERE / "test"
OUT.mkdir(exist_ok=True)

BANKS = {
    "invoice_1.pdf": ("Acme Holdings Ltd", 60, 620),    # bottom-left
    "invoice_2.pdf": ("Northwind Trading SA", 60, 320), # middle-left
    "invoice_3.pdf": ("Acme Holdings Ltd", 60, 480),    # lower-middle
}

for name, (account, bx, by) in BANKS.items():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((60, 60), "INVOICE", fontsize=24, fontname="helv")
    page.insert_text((60, 90), f"Invoice #{name[-5]}  |  Date: 2026-09-02", fontsize=10)
    page.insert_text((400, 60), "Corient AG", fontsize=11)
    page.insert_text((400, 75), "Zurich, Switzerland", fontsize=9)
    # line items
    y = 150
    page.insert_text((60, y), "Description", fontsize=10)
    page.insert_text((420, y), "Amount (CHF)", fontsize=10)
    for i, (d, a) in enumerate([("Consulting services", "12,500.00"),
                                ("Platform licence", "3,200.00"),
                                ("Support retainer", "1,800.00")]):
        page.insert_text((60, y + 20 + i * 16), d, fontsize=10)
        page.insert_text((420, y + 20 + i * 16), a, fontsize=10)
    page.insert_text((60, y + 90), "Total: CHF 17,500.00", fontsize=12)
    # banking block at a different position per invoice
    page.insert_text((bx, by), "Banking Details", fontsize=11, fontname="helv")
    for i, line in enumerate([f"Account name: {account}",
                              "IBAN: CH93 0076 2011 6238 5295 7",
                              "BIC: UBSWCHZH80A",
                              "Bank: UBS Switzerland AG"]):
        page.insert_text((bx, by + 18 + i * 14), line, fontsize=9)
    doc.save(OUT / name)
    doc.close()
    print("wrote", OUT / name)

# fake signature PNGs (transparent background, scribble-ish curves)
for fname, color in [("sig_acme.png", (0.1, 0.1, 0.5)), ("sig_northwind.png", (0.3, 0.1, 0.1))]:
    d = fitz.open()
    p = d.new_page(width=340, height=120)
    shape = p.new_shape()
    pts = [(20, 80), (60, 30), (100, 90), (140, 40), (180, 85), (230, 35), (280, 75), (320, 50)]
    for a, b in zip(pts, pts[1:]):
        mid = ((a[0] + b[0]) / 2, min(a[1], b[1]) - 15)
        shape.draw_curve(fitz.Point(a), fitz.Point(mid), fitz.Point(b))
    shape.finish(color=color, width=3)
    shape.commit()
    pix = p.get_pixmap(alpha=True)
    # make white transparent-ish by rendering with alpha (background is already transparent)
    pix.save(OUT / fname)
    d.close()
    print("wrote", OUT / fname)

import json
(OUT / "signatures.json").write_text(json.dumps({
    "Acme Holdings Ltd": "sig_acme.png",
    "Northwind Trading SA": "sig_northwind.png",
}, indent=2), encoding="utf-8")
print("wrote", OUT / "signatures.json")
