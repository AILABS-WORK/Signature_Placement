// SDK Script activity body for Laserfiche Workflow (self-hosted, Workflow 10/11).
//
// Paste the Execute() body into an "SDK Script" activity (C#). The activity must be
// bound to the triggering invoice entry. Requires the Laserfiche SDK (Repository
// Access) licensed on the Workflow Server, and the folder C:\LFStamp containing
// sign_invoice.exe (NTFS read/execute + read/write for the Workflow service account).
//
// PHASE 1: one signature image for every invoice, stored on the Workflow Server
// at C:\LFStamp\signature.png. (Phase 2 — per-account signatures matched by name
// from a repository folder — swaps the SIGNATURE_FILE argument for a mirrored
// signatures folder and "--signatures"; see README.)
//
// Repository layout assumed (adjust the paths marked CONFIG):
//   \Invoices\Incoming     -> workflow starting rule watches this folder
//   \Invoices\Signed       -> signed copies are filed here
//
// Tokens set for downstream routing:
//   StampStatus : stamped | no_banking_details | no_empty_space |
//                 no_matching_signature | error
//   StampDetail : stdout of the stamper (includes the RESULT json line)

namespace WorkflowActivity.Scripting.SDKScript
{
    using System;
    using System.Diagnostics;
    using System.IO;
    using Laserfiche.RepositoryAccess;

    public class Script1 : RAScriptClass104   // base class name comes from the WF template
    {
        protected override void Execute()
        {
            // ---- CONFIG ----------------------------------------------------
            const string ROOT          = @"C:\LFStamp";
            // Phase 1: ONE signature image stored IN THE REPOSITORY, e.g. an
            // image document named "signature" in the invoices folder.
            const string SIG_LF_PATH   = @"\Invoices\signature";
            const string SIGNED_FOLDER = @"\Invoices\Signed";
            // ----------------------------------------------------------------

            string workIn  = Path.Combine(ROOT, "in");
            string workOut = Path.Combine(ROOT, "out");
            string exePath = Path.Combine(ROOT, "sign_invoice.exe");
            Directory.CreateDirectory(workIn);
            Directory.CreateDirectory(workOut);

            Session session = this.RASession;
            DocumentInfo doc = Document.GetDocumentInfo(this.BoundEntryId, session);

            string inPdf  = Path.Combine(workIn,  this.BoundEntryId + ".pdf");
            string outPdf = Path.Combine(workOut, this.BoundEntryId + "_signed.pdf");

            try
            {
                // 1) Export the triggering invoice's electronic document
                DocumentExporter dex = new DocumentExporter();
                dex.ExportElecDoc(doc, inPdf);

                // 2) Export the signature image from the repository (cached on disk;
                //    re-exported only when the repository copy is newer). Phase 2
                //    swaps this for a per-account signatures folder + --signatures.
                DocumentInfo sigDoc = Document.GetDocumentInfo(SIG_LF_PATH, session);
                string sigLocal = Path.Combine(ROOT, "signature." +
                    (string.IsNullOrEmpty(sigDoc.Extension) ? "png" : sigDoc.Extension));
                if (!File.Exists(sigLocal) ||
                    File.GetLastWriteTimeUtc(sigLocal) < sigDoc.LastModifiedTime.ToUniversalTime())
                {
                    new DocumentExporter().ExportElecDoc(sigDoc, sigLocal);
                }
                sigDoc.Dispose();

                // 3) Run the stamper
                ProcessStartInfo psi = new ProcessStartInfo
                {
                    FileName = exePath,
                    Arguments = "\"" + inPdf + "\" --signature \"" + sigLocal +
                                "\" --out-file \"" + outPdf + "\"",
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true
                };
                int exitCode;
                string stdout, stderr;
                using (Process p = Process.Start(psi))
                {
                    stdout = p.StandardOutput.ReadToEnd();
                    stderr = p.StandardError.ReadToEnd();
                    if (!p.WaitForExit(120000)) { p.Kill(); throw new Exception("stamper timed out"); }
                    exitCode = p.ExitCode;
                }

                string status;
                switch (exitCode)
                {
                    case 0:  status = "stamped"; break;
                    case 2:  status = "no_banking_details"; break;
                    case 3:  status = "no_empty_space"; break;
                    case 4:  status = "no_matching_signature"; break;
                    default: status = "error"; break;
                }
                this.SetTokenValue("StampStatus", status);
                this.SetTokenValue("StampDetail", stdout + stderr);

                // 4) On success, file the signed copy into \Invoices\Signed
                if (exitCode == 0)
                {
                    FolderInfo signedFolder = Folder.GetFolderInfo(SIGNED_FOLDER, session);
                    DocumentInfo newDoc = new DocumentInfo(session);
                    newDoc.Create(signedFolder, doc.Name + " (signed)",
                                  doc.VolumeName, EntryNameOption.AutoRename);
                    DocumentImporter dim = new DocumentImporter();
                    dim.Document = newDoc;
                    dim.ImportEdoc("application/pdf", outPdf);
                    newDoc.Save();
                    this.SetTokenValue("SignedEntryId", newDoc.Id.ToString());
                    newDoc.Dispose();
                }
            }
            finally
            {
                if (File.Exists(inPdf))  File.Delete(inPdf);
                if (File.Exists(outPdf)) File.Delete(outPdf);
                doc.Dispose();
            }
        }
    }
}
