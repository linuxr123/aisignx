using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
using AiSignXPowerPoint.Settings;
using AiSignXPowerPoint.UI;
using Microsoft.Office.Tools.Ribbon;
using PowerPoint = Microsoft.Office.Interop.PowerPoint;

namespace AiSignXPowerPoint
{
    /// <summary>The "AiSignX" ribbon tab with the "Export and upload" button.</summary>
    public partial class AiSignXRibbon
    {
        private void OnExportUploadClick(object sender, RibbonControlEventArgs e)
        {
            PowerPoint.Application app = Globals.ThisAddIn.Application;
            PowerPoint.Presentation presentation = null;
            try
            {
                presentation = ActivePresentation(app);
                if (presentation == null)
                {
                    MessageBox.Show("Open a presentation first. Presentations in Protected View must be "
                                    + "enabled for editing before they can be exported.", "AiSignX",
                                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return;
                }
                using (var form = new UploadForm(presentation, new UserSettings()))
                {
                    form.ShowDialog(new OwnerWindow(app));
                }
            }
            catch (Exception ex) when (ex is COMException || ex is InvalidOperationException
                                       || ex is UnauthorizedAccessException || ex is System.IO.IOException
                                       || ex is System.Security.SecurityException)
            {
                MessageBox.Show("AiSignX could not continue: " + ex.Message, "AiSignX",
                                MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                if (presentation != null)
                    Marshal.ReleaseComObject(presentation);
            }
        }

        /// <summary>The presentation in the active window, or null when there is none (no
        /// window open, or only a Protected View window).</summary>
        private static PowerPoint.Presentation ActivePresentation(PowerPoint.Application app)
        {
            PowerPoint.Presentations open = app.Presentations;
            try
            {
                if (open.Count == 0)
                    return null;
            }
            finally { Marshal.ReleaseComObject(open); }
            try { return app.ActivePresentation; }
            catch (COMException) { return null; }
        }

        /// <summary>Makes PowerPoint's main window the owner, so the dialog is modal to it.</summary>
        private sealed class OwnerWindow : IWin32Window
        {
            public OwnerWindow(PowerPoint.Application app)
            {
                try { Handle = new IntPtr(app.HWND); }
                catch (COMException) { Handle = IntPtr.Zero; }
            }

            public IntPtr Handle { get; }
        }
    }
}
