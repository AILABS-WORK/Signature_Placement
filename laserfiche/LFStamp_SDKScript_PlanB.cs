// PLAN B (PRODUCTION): pure Laserfiche.RepositoryAccess version — no
// Laserfiche.DocumentServices reference needed (avoids assembly version-mismatch
// warnings). Export/import via RA edoc streams.
//
// Workflow mapping (stonehage-uat):
//   Starting rule: entry created in \Staff Folder\msilva\Invoices\Incoming, ext pdf
//   Conditional Decision on StampStatus:
//     stamped -> Move Entry to \Staff Folder\msilva\Invoices\Documents
//     else    -> Move Entry to \Staff Folder\msilva\Invoices\Needs_Review + email
//   Signature image: repo document \Staff Folder\msilva\Invoices\Signatures\signature
//
// Paste the whole file into the Workflow 11 SDK Script activity (C#), replacing
// the template. Only the CONFIG block should need editing.
//
// Version-sensitive calls (confirmed for this RA version: ReadEdoc(out mimeType)):
//   newDoc.WriteEdoc(mime, length)       -> WriteEdoc(mime)  or  WriteEdoc(null, length)
//   Document.GetDocumentInfo(path, s)    -> (DocumentInfo)Entry.GetEntryInfo(path, s)

namespace WorkflowActivity.Scripting.SDKScript
{
    using System;
    using System.Collections.Generic;
    using System.ComponentModel;
    using System.Data;
    using System.Data.SqlClient;
    using System.Diagnostics;
    using System.IO;
    using System.Text;
    using Laserfiche.RepositoryAccess;

    /// <summary>
    /// Provides one or more methods that can be run when the workflow scripting activity is performed.
    /// </summary>
    public class Script1 : RAScriptClass110
    {
        /// <summary>
        /// This method is run when the activity is performed.
        /// </summary>
        protected override void Execute()
        {
            // ---- CONFIG: stonehage-uat repository, msilva staff folder -----
            // Repository paths NEVER include the repository name — root is "\".
            const string EXE_PATH      = @"D:\Software\sign_invoice.exe";
            const string WORK_ROOT     = @"D:\Software\LFStamp";  // temp files on disk
            const string SIG_LF_PATH   = @"\Staff Folder\msilva\Invoices\Signatures\signature";
            const string SIGNED_FOLDER = @"\Staff Folder\msilva\Invoices\Signed";
            // ----------------------------------------------------------------

            string workIn  = Path.Combine(WORK_ROOT, "in");
            string workOut = Path.Combine(WORK_ROOT, "out");
            Directory.CreateDirectory(workIn);
            Directory.CreateDirectory(workOut);

            Session session = this.RASession;
            DocumentInfo doc = this.BoundEntryInfo as DocumentInfo;
            if (doc == null)
                throw new Exception("Starting entry is not a document");

            string inPdf  = Path.Combine(workIn,  doc.Id + ".pdf");
            string outPdf = Path.Combine(workOut, doc.Id + "_signed.pdf");

            try
            {
                // 1) Export the triggering invoice's electronic document (stream copy)
                string docMime;
                using (Stream es = doc.ReadEdoc(out docMime))
                using (FileStream fs = new FileStream(inPdf, FileMode.Create, FileAccess.Write))
                {
                    es.CopyTo(fs);
                }

                // 2) Export the signature image from the repository (cached on disk;
                //    re-exported only when the repository copy is newer)
                DocumentInfo sigDoc = Document.GetDocumentInfo(SIG_LF_PATH, session);
                string sigLocal = Path.Combine(WORK_ROOT, "signature." +
                    (string.IsNullOrEmpty(sigDoc.Extension) ? "png" : sigDoc.Extension));
                if (!File.Exists(sigLocal) ||
                    File.GetLastWriteTimeUtc(sigLocal) < sigDoc.LastModifiedTime.ToUniversalTime())
                {
                    string sigMime;
                    using (Stream ss = sigDoc.ReadEdoc(out sigMime))
                    using (FileStream fs = new FileStream(sigLocal, FileMode.Create, FileAccess.Write))
                    {
                        ss.CopyTo(fs);
                    }
                }
                sigDoc.Dispose();

                // 3) Run the stamper
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = EXE_PATH;
                psi.Arguments = "\"" + inPdf + "\" --signature \"" + sigLocal +
                                "\" --out-file \"" + outPdf + "\"";
                psi.UseShellExecute = false;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.CreateNoWindow = true;

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

                // 4) On success, file the signed copy into the Signed folder (stream write)
                if (exitCode == 0)
                {
                    FolderInfo signedFolder = Folder.GetFolderInfo(SIGNED_FOLDER, session);
                    DocumentInfo newDoc = new DocumentInfo(session);
                    newDoc.Create(signedFolder, doc.Name + " (signed)",
                                  doc.VolumeName, EntryNameOption.AutoRename);

                    byte[] pdfBytes = File.ReadAllBytes(outPdf);
                    using (Stream ws = newDoc.WriteEdoc("application/pdf", pdfBytes.Length))
                    {
                        ws.Write(pdfBytes, 0, pdfBytes.Length);
                    }
                    newDoc.Extension = "pdf";
                    newDoc.Save();
                    this.SetTokenValue("SignedEntryId", newDoc.Id.ToString());
                    newDoc.Dispose();
                }
            }
            finally
            {
                if (File.Exists(inPdf))  File.Delete(inPdf);
                if (File.Exists(outPdf)) File.Delete(outPdf);
            }
        }
    }
}
