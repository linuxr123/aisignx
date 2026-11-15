namespace AiSignXPowerPoint
{
    partial class AiSignXRibbon : Microsoft.Office.Tools.Ribbon.RibbonBase
    {
        private System.ComponentModel.IContainer components = null;

        public AiSignXRibbon()
            : base(Globals.Factory.GetRibbonFactory())
        {
            InitializeComponent();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && (components != null))
                components.Dispose();
            base.Dispose(disposing);
        }

        #region Component Designer generated code

        private void InitializeComponent()
        {
            this.tabAiSignX = this.Factory.CreateRibbonTab();
            this.grpPublish = this.Factory.CreateRibbonGroup();
            this.btnExportUpload = this.Factory.CreateRibbonButton();
            this.tabAiSignX.SuspendLayout();
            this.grpPublish.SuspendLayout();
            this.SuspendLayout();
            //
            // tabAiSignX
            //
            this.tabAiSignX.Groups.Add(this.grpPublish);
            this.tabAiSignX.Label = "AiSignX";
            this.tabAiSignX.Name = "tabAiSignX";
            //
            // grpPublish
            //
            this.grpPublish.Items.Add(this.btnExportUpload);
            this.grpPublish.Label = "Signage";
            this.grpPublish.Name = "grpPublish";
            //
            // btnExportUpload
            //
            this.btnExportUpload.ControlSize = Microsoft.Office.Core.RibbonControlSize.RibbonControlSizeLarge;
            this.btnExportUpload.Label = "Export and upload";
            this.btnExportUpload.Name = "btnExportUpload";
            this.btnExportUpload.ScreenTip = "Export and upload to AiSignX";
            this.btnExportUpload.SuperTip = "Export this presentation as an MP4 video and upload it to your AiSignX server, as a new media item or replacing an existing one.";
            this.btnExportUpload.ShowImage = true;
            this.btnExportUpload.Click += new Microsoft.Office.Tools.Ribbon.RibbonControlEventHandler(this.OnExportUploadClick);
            //
            // AiSignXRibbon
            //
            this.Name = "AiSignXRibbon";
            this.RibbonType = "Microsoft.PowerPoint.Presentation";
            this.Tabs.Add(this.tabAiSignX);
            this.Load += new Microsoft.Office.Tools.Ribbon.RibbonUIEventHandler(this.AiSignXRibbon_Load);
            this.tabAiSignX.ResumeLayout(false);
            this.tabAiSignX.PerformLayout();
            this.grpPublish.ResumeLayout(false);
            this.grpPublish.PerformLayout();
            this.ResumeLayout(false);
        }

        #endregion

        internal Microsoft.Office.Tools.Ribbon.RibbonTab tabAiSignX;
        internal Microsoft.Office.Tools.Ribbon.RibbonGroup grpPublish;
        internal Microsoft.Office.Tools.Ribbon.RibbonButton btnExportUpload;
    }

    partial class ThisRibbonCollection
    {
        internal AiSignXRibbon AiSignXRibbon => this.GetRibbon<AiSignXRibbon>();
    }
}
