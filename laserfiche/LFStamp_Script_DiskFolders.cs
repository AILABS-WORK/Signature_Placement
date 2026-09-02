// DISK-FOLDER VERSION (current production setup): invoices, signature and output
// folders are plain Windows folders on the server — no repository access at all.
//
//   D:\msilva\Projects\Signature_Placement\Invoices\Incoming      <- drop PDFs here
//   D:\msilva\Projects\Signature_Placement\Invoices\Signature     <- one signature image
//   D:\msilva\Projects\Signature_Placement\Invoices\Signed        <- stamped copies
//   D:\msilva\Projects\Signature_Placement\Invoices\Needs Review  <- failures
//   D:\Software\sign_invoice.exe                                  <- the stamper
//
// Paste into the Workflow SDK Script activity (C#) — no extra assembly references
// needed (remove Laserfiche.DocumentServices if it was added). Because nothing
// arrives in the repository, use a SCHEDULED starting rule (e.g. every 5 minutes).
//
// Tokens set: StampStatus (ok|attention), StampOk, StampFailed, StampDetail.

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
            const string EXE_PATH   = @"D:\Software\sign_invoice.exe";
            const string BASE       = @"D:\msilva\Projects\Signature_Placement\Invoices";
            string incoming  = Path.Combine(BASE, "Incoming");
            string signed    = Path.Combine(BASE, "Signed");
            string review    = Path.Combine(BASE, "Needs Review");
            string sigFolder = Path.Combine(BASE, "Signature");
            string processed = Path.Combine(incoming, "Processed"); // originals after success
            // ----------------------------------------------------------------

            Directory.CreateDirectory(signed);
            Directory.CreateDirectory(review);
            Directory.CreateDirectory(processed);

            // pick the signature image: first image file in the Signature folder
            string sigFile = null;
            string[] sigExts = new string[] { ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff" };
            foreach (string f in Directory.GetFiles(sigFolder))
            {
                string ext = Path.GetExtension(f).ToLowerInvariant();
                if (Array.IndexOf(sigExts, ext) >= 0) { sigFile = f; break; }
            }
            if (sigFile == null)
                throw new Exception("No signature image found in " + sigFolder);

            StringBuilder log = new StringBuilder();
            int okCount = 0, failCount = 0;

            foreach (string pdf in Directory.GetFiles(incoming, "*.pdf"))
            {
                string name   = Path.GetFileNameWithoutExtension(pdf);
                string outPdf = UniquePath(signed, name + "_signed", ".pdf");

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = EXE_PATH;
                psi.Arguments = "\"" + pdf + "\" --signature \"" + sigFile +
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
                    if (!p.WaitForExit(120000)) { p.Kill(); exitCode = -1; }
                    else exitCode = p.ExitCode;
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

                if (exitCode == 0)
                {
                    File.Move(pdf, UniquePath(processed, name, ".pdf"));
                    okCount++;
                }
                else
                {
                    File.Move(pdf, UniquePath(review, name, ".pdf"));
                    failCount++;
                }
                log.AppendLine(Path.GetFileName(pdf) + " -> " + status);
                log.AppendLine(stdout + stderr);
            }

            this.SetTokenValue("StampStatus", failCount == 0 ? "ok" : "attention");
            this.SetTokenValue("StampOk", okCount.ToString());
            this.SetTokenValue("StampFailed", failCount.ToString());
            this.SetTokenValue("StampDetail", log.ToString());
        }

        private static string UniquePath(string folder, string baseName, string ext)
        {
            string path = Path.Combine(folder, baseName + ext);
            int i = 1;
            while (File.Exists(path))
            {
                path = Path.Combine(folder, baseName + " (" + i + ")" + ext);
                i++;
            }
            return path;
        }
    }
}
