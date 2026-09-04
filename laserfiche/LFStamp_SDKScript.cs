// PRODUCTION SDK Script — Laserfiche Workflow 11 (stonehage-uat, DMLWF105U03).
// This is the ONE script to deploy. Paste the whole file into the SDK Script
// activity (C#), replacing the template. Only the CONFIG block needs editing.
//
// Invoices live in the repository; the signature image lives on the server's
// disk. The signature is deliberately NOT exported from the repository: an
// image imported into Laserfiche is usually stored as TIFF pages with an EMPTY
// electronic document, so exporting it produced a 0 KB file and wiped whatever
// was already there (FileMode.Create truncates first).
//
// Flow per invoice:
//   \...\Invoices\Incoming  --trigger--> export pdf to WORK_ROOT\in
//                                        run sign_invoice.exe --signature SIG_FILE
//                                        import signed pdf into SIGNED_FOLDER
//                                        delete temp files
// Tokens: StampStatus (stamped | no_banking_details | no_empty_space |
//         no_matching_signature | not_a_pdf | not_electronic_pdf |
//         not_a_document | error), StampDetail, SignedEntryId, DebugInfo.
// Only "stamped" is success; route every other value to Needs_Review and show
// StampDetail, which always says what to do about it.
//
// No extra assembly references: RepositoryAccess only (no DocumentServices, so
// no version-mismatch warnings). ReadEdoc/WriteEdoc use this RA version's
// overloads: ReadEdoc(out mimeType), WriteEdoc(mimeType, length).

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
            // ---- CONFIG ----------------------------------------------------
            // Keep EXE_PATH as whatever this server actually uses.
            const string EXE_PATH  = @"C:\Software\sign_invoice.exe";
            const string WORK_ROOT = @"C:\Software\msilva\Projects\Signature_Placement\Work";
            // The signature PNG on disk. Keep it OUTSIDE WORK_ROOT: Work holds
            // throwaway files. Nothing in this script ever writes to it.
            const string SIG_FILE  = @"C:\Software\msilva\Projects\Signature_Placement\signature.png";
            // Repository folder that receives the signed copies.
            const string SIGNED_FOLDER = @"\Staff Folder\msilva\Invoices\Signed";
            // ----------------------------------------------------------------

            long sigBytes = File.Exists(SIG_FILE) ? new FileInfo(SIG_FILE).Length : -1;
            this.SetTokenValue("DebugInfo",
                "machine=" + Environment.MachineName +
                "; user=" + Environment.UserName +
                "; exeExists=" + File.Exists(EXE_PATH) +
                "; sigBytes=" + sigBytes);

            if (!File.Exists(EXE_PATH))
                throw new Exception("stamper exe not found at " + EXE_PATH +
                                    " on machine " + Environment.MachineName);
            if (sigBytes <= 0)
                throw new Exception("signature image missing or empty (" + sigBytes +
                                    " bytes) at " + SIG_FILE);

            string workIn  = Path.Combine(WORK_ROOT, "in");
            string workOut = Path.Combine(WORK_ROOT, "out");
            Directory.CreateDirectory(workIn);
            Directory.CreateDirectory(workOut);

            Session session = this.RASession;
            DocumentInfo doc = this.BoundEntryInfo as DocumentInfo;
            if (doc == null)
            {
                this.SetTokenValue("StampStatus", "not_a_document");
                this.SetTokenValue("StampDetail", "The entry is a folder or shortcut, not a document.");
                return;
            }

            string inPdf  = Path.Combine(workIn,  doc.Id + ".pdf");
            string outPdf = Path.Combine(workOut, doc.Id + "_signed.pdf");

            try
            {
                // 1) Export the triggering invoice's electronic document
                string docMime;
                using (Stream es = doc.ReadEdoc(out docMime))
                using (FileStream fs = new FileStream(inPdf, FileMode.Create, FileAccess.Write))
                {
                    es.CopyTo(fs);
                }

                // Anyone can drop anything into a shared folder, so check what
                // actually arrived before handing it to the stamper.
                if (!"pdf".Equals(doc.Extension, StringComparison.OrdinalIgnoreCase))
                {
                    this.SetTokenValue("StampStatus", "not_a_pdf");
                    this.SetTokenValue("StampDetail",
                        "Extension is '" + doc.Extension + "'. Only PDFs can be stamped.");
                    return;
                }
                if (new FileInfo(inPdf).Length == 0)
                {
                    this.SetTokenValue("StampStatus", "not_electronic_pdf");
                    this.SetTokenValue("StampDetail",
                        "This entry has no electronic file — it was imported as Laserfiche " +
                        "image pages. Re-import the PDF as an electronic document (do not " +
                        "let the client generate pages).");
                    return;
                }

                // 2) Run the stamper against the signature file on disk
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = EXE_PATH;
                psi.Arguments = "\"" + inPdf + "\" --signature \"" + SIG_FILE +
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

                // 3) On success, file the signed copy into the Signed folder
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
