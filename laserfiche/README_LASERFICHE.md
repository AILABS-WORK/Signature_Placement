# Invoice Signature Stamper — Laserfiche Workflow integration

Stamps a signature image to the right of the Banking Details block of a PDF invoice,
wherever that block sits (any page, any position). The signature always stays inside
the block's horizontal band — level with the banking details, never above or below.

**Phase 1 (current):** one signature image stored IN THE REPOSITORY (an image
document named `signature` in the invoices folder) applied to every invoice; the
workflow exports it to disk automatically before stamping.
**Phase 2 (later):** per-account signatures — a repository folder with one image per
account name, matched automatically against the document text (`--signatures` mode,
already built into the exe).

## Architecture

Laserfiche Workflow's script activities run C#/VB only, so the stamping engine ships
as a standalone `sign_invoice.exe` (no Python install needed on the server). The
workflow exports the invoice + signature images to a working folder, runs the exe,
and imports the signed PDF back into the repository.

```
\Invoices\Incoming  --(workflow trigger)--> SDK Script activity
                                             |  export invoice -> C:\LFStamp\in
                                             |  run sign_invoice.exe --signature C:\LFStamp\signature.png
                                             |  import signed pdf -> \Invoices\Signed
                                             '--> tokens: StampStatus / StampDetail / SignedEntryId
```

## Server setup (once, on the Workflow Server machine)

1. Create `C:\LFStamp\` and copy in `sign_invoice.exe` — the ONLY file that must
   live on the server's disk, because Windows cannot execute a program stored
   inside the Laserfiche repository (`in\` and `out\` are created automatically;
   the signature image is exported from the repository at run time).
2. Give the **Workflow service account** Modify rights on `C:\LFStamp`.
3. Sanity-check from a command prompt on the server with any pdf + png:
   `C:\LFStamp\sign_invoice.exe some_invoice.pdf --signature sig.png --out-file signed.pdf`
   Exit code 0 and a `RESULT {...}` line on stdout means it works.

## Repository setup

1. `\Invoices\Incoming` — where new invoices land (workflow watches this;
   the starting rule filters on extension `pdf`, so the signature image never
   triggers it).
2. `\Invoices\signature` — the signature: one image document (png with
   transparent background works best). Update it in place any time; the
   workflow re-exports it automatically when it changes.
3. `\Invoices\Signed` — where signed copies get filed.
4. *(Phase 2 only)* a signatures folder with one image document per account,
   named exactly like the account name (e.g. `Corient Advisory SA`); the exe's
   `--signatures <folder>` mode matches names against the document text.

## Workflow design (Designer)

1. **Starting rule**: entry created in `\Invoices\Incoming`, entry type Document,
   extension `pdf`.
2. **SDK Script activity** (C#): paste the body of `LFStamp_SDKScript.cs`.
   Requires the SDK Script activity (Laserfiche SDK / Repository Access licensed).
3. **Conditional decision** on token `StampStatus`:
   - `stamped` → move original to an archive folder / set field "Signed = yes".
   - `no_empty_space` / `no_banking_details` / `no_matching_signature` → route to a
     manual-review folder and/or email the team, with `StampDetail` in the message.
4. Optional: instead of a new document in `\Invoices\Signed`, the script can import
   the signed PDF as a **new version of the same entry** (keeps metadata/audit
   trail) — swap step 4 in the script for `DocumentImporter` targeting `doc` while
   it is version-controlled.

## Exit codes / statuses

| Code | StampStatus            | Meaning                                                |
|------|------------------------|--------------------------------------------------------|
| 0    | stamped                | Signature placed; signed PDF written                   |
| 2    | no_banking_details     | No banking heading/labels found on any page            |
| 3    | no_empty_space         | Block found, but nothing fits to its right             |
| 4    | no_matching_signature  | No signature image name matches the document text      |
| 1    | error                  | Bad arguments / IO error                               |

## Notes

- The exe stamps **every page** that has a banking block; multi-page invoices with
  the block on page 3 of 4 work the same as single-page ones.
- Row rule: the signature is placed strictly inside the banking block's horizontal
  band (between its top and bottom edges). If the block is shorter than the
  signature (a one-line block), the signature is centred on that row.
- Sentences that merely mention "banking details" in running text are ignored —
  only heading-style blocks (or `IBAN` / `Account Number` label lines) count.
- Placement is verified against everything on the page (text, images, drawn lines):
  the signature is never stamped over existing content.
- Tune stamp size via `SIG_WIDTH` / `SIG_HEIGHT` in `sign_invoice.py` and rebuild
  (`python -m PyInstaller --onefile sign_invoice.py`).
- The class/base names in the SDK Script template vary slightly between Workflow
  versions — keep the template's own class declaration and paste the `Execute()`
  body if it differs.
- Tested against a 40-document synthetic corpus (1–4 pages, four block layouts,
  crowded pages, decoy text): 28/28 expected stamps placed with zero overlaps,
  12/12 no-space documents correctly skipped, 28/28 correct signature matches.
