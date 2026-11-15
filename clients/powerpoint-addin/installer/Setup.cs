// "Install AiSignX for PowerPoint.exe": a small front door for the ClickOnce installer.
// It (1) trusts the AiSignX signing certificate for the current user, so Windows installs the
// add-in without an "unknown publisher" block, then (2) runs setup.exe from the same folder.
// Built by tools/Build-Installer.ps1 (csc /target:winexe /win32icon:AiSignX.ico).
using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography.X509Certificates;
using System.Windows.Forms;

internal static class Setup
{
    private const string Title = "AiSignX for PowerPoint";

    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string cert = Path.Combine(dir, "AiSignX-addin.cer");
        string setup = Path.Combine(dir, "setup.exe");
        if (!File.Exists(cert) || !File.Exists(setup))
        {
            MessageBox.Show("This installer needs AiSignX-addin.cer and setup.exe in the same folder.\n\n" +
                            "Unzip the whole download first (do not run it from inside the zip), then try again.",
                            Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return 2;
        }
        if (Process.GetProcessesByName("POWERPNT").Length > 0)
        {
            MessageBox.Show("PowerPoint is open. Please save your work and close PowerPoint, then run this again.",
                            Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 3;
        }
        DialogResult go = MessageBox.Show(
            "This installs the AiSignX add-in for PowerPoint.\n\n" +
            "1. It first trusts the AiSignX certificate for your Windows account. Windows asks you to confirm: click Yes.\n" +
            "2. Then the setup runs. Click Install when it asks.\n\nContinue?",
            Title, MessageBoxButtons.OKCancel, MessageBoxIcon.Information);
        if (go != DialogResult.OK) return 1;
        try
        {
            var c = new X509Certificate2(cert);
            Trust(StoreName.TrustedPublisher, c);
            Trust(StoreName.Root, c); // Windows shows its own security prompt for this one
        }
        catch (Exception ex)
        {
            MessageBox.Show("Could not trust the certificate:\n\n" + ex.Message + "\n\n" +
                            "If you clicked No on the Windows prompt, run this again and click Yes.",
                            Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 4;
        }
        try
        {
            using (Process p = Process.Start(new ProcessStartInfo(setup) { UseShellExecute = true, WorkingDirectory = dir }))
            {
                p.WaitForExit();
                if (p.ExitCode != 0)
                {
                    MessageBox.Show("The setup did not finish (code " + p.ExitCode + ").\n\nSee the Troubleshooting part of the guide.",
                                    Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return 5;
                }
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show("Could not start the setup:\n\n" + ex.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 6;
        }
        MessageBox.Show("Done. Open PowerPoint and look for the AiSignX tab on the ribbon.\n\n" +
                        "The first time, open it and enter your server address and your API token.",
                        Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
        return 0;
    }

    private static void Trust(StoreName name, X509Certificate2 cert)
    {
        var store = new X509Store(name, StoreLocation.CurrentUser);
        store.Open(OpenFlags.ReadWrite);
        try { store.Add(cert); } finally { store.Close(); }
    }
}
