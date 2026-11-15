using System;
using AiSignXPowerPoint.Export;

namespace AiSignXPowerPoint
{
    /// <summary>VSTO entry point. The ribbon (AiSignXRibbon) is found by VSTO on its own.</summary>
    public partial class ThisAddIn
    {
        private void ThisAddIn_Startup(object sender, EventArgs e)
        {
            TempFiles.Sweep(); // exports left over from a crash or a cancelled export
        }

        private void ThisAddIn_Shutdown(object sender, EventArgs e)
        {
            TempFiles.Sweep(all: true);
        }

        #region VSTO generated code

        private void InternalStartup()
        {
            Startup += ThisAddIn_Startup;
            Shutdown += ThisAddIn_Shutdown;
        }

        #endregion
    }
}
