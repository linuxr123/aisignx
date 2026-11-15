using System;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;
using AiSignXPowerPoint.Api;
using AiSignXPowerPoint.Export;
using AiSignXPowerPoint.Settings;
using PowerPoint = Microsoft.Office.Interop.PowerPoint;

namespace AiSignXPowerPoint.UI
{
    /// <summary>
    /// "Export and upload" dialog: server and token, new item or replace, video options,
    /// progress. This file builds the controls and moves settings in and out of them; the
    /// actions (test, choose, export and upload) are in UploadForm.Actions.cs.
    /// </summary>
    public sealed partial class UploadForm : Form
    {
        private readonly PowerPoint.Presentation _presentation;
        private readonly UserSettings _settings;
        private readonly PresentationOptions _options;
        private readonly string _presentationName;

        private CancellationTokenSource _cancel;
        private bool _closeWhenIdle;
        private bool _tokenEdited;
        private string _secretsKey; // server whose token and pin are in the boxes

        private TextBox txtUrl, txtToken, txtPin, txtName;
        private Label lblUrlHint, lblChosen, lblStatus;
        private CheckBox chkShowToken, chkTimings;
        private LinkLabel lnkForget;
        private RadioButton rbNew, rbReplace;
        private NumericUpDown numDuration, numId, numFps, numQuality, numSlideSeconds;
        private ComboBox cmbResolution;
        private Button btnTest, btnChoose, btnRun, btnClose;
        private ProgressBar progress;

        public UploadForm(PowerPoint.Presentation presentation, UserSettings settings)
        {
            _presentation = presentation ?? throw new ArgumentNullException(nameof(presentation));
            _settings = settings ?? throw new ArgumentNullException(nameof(settings));
            _presentationName = SafePresentationName(presentation);
            BuildControls();

            _options = PresentationOptions.Load(presentation, out bool removedLegacyToken);
            ShowOptions();
            if (removedLegacyToken)
                SetStatus("Removed a plain-text token the old add-in had saved in this presentation. "
                          + "Save the presentation to keep it out of the file.");
        }

        private static string SafePresentationName(PowerPoint.Presentation presentation)
        {
            try { return Path.GetFileNameWithoutExtension(presentation.Name); }
            catch (System.Runtime.InteropServices.COMException) { return "Presentation"; }
        }

        // ---- layout ---------------------------------------------------------------------

        private void BuildControls()
        {
            Text = "AiSignX: export and upload";
            Icon = Icons.App();
            Font = SystemFonts.MessageBoxFont;
            AutoScaleMode = AutoScaleMode.Font;
            FormBorderStyle = FormBorderStyle.Sizable;
            MaximizeBox = false;
            MinimizeBox = false;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.CenterScreen;
            Padding = new Padding(10);

            // The settings scroll; the progress bar, status line and buttons stay pinned at the
            // bottom. (The dialog used to grow with its content, so on a small or scaled screen the
            // status line and the buttons were off the bottom edge and nothing could be reached.)
            var content = new TableLayoutPanel { ColumnCount = 1, AutoSize = true, Dock = DockStyle.Top };
            content.Controls.Add(BuildServerGroup());
            content.Controls.Add(BuildTargetGroup());
            content.Controls.Add(BuildVideoGroup());
            var scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
            scroll.Controls.Add(content);

            progress = new ProgressBar { Width = 520, Height = 18, Margin = new Padding(3, 10, 3, 3) };
            lblStatus = new Label { AutoSize = true, MaximumSize = new Size(520, 0), Text = "Ready." };

            btnRun = new Button { Text = "Export and upload", AutoSize = true, Padding = new Padding(6, 2, 6, 2) };
            btnClose = new Button { Text = "Close", AutoSize = true, Padding = new Padding(6, 2, 6, 2) };
            btnRun.Click += (s, e) => RunSafe(RunAsync);
            btnClose.Click += (s, e) => OnCloseClicked();
            var buttons = new FlowLayoutPanel
            {
                FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Dock = DockStyle.Fill,
                Margin = new Padding(0, 8, 0, 0),
            };
            buttons.Controls.Add(btnClose);
            buttons.Controls.Add(btnRun);

            var bottom = new TableLayoutPanel { ColumnCount = 1, AutoSize = true, Dock = DockStyle.Bottom };
            bottom.Controls.Add(progress);
            bottom.Controls.Add(lblStatus);
            bottom.Controls.Add(buttons);

            Controls.Add(scroll);
            Controls.Add(bottom);
            scroll.BringToFront(); // docked last, so it takes what the bottom bar leaves

            // As tall as the content needs, but never taller than the screen's usable area.
            Rectangle area = Screen.FromPoint(Cursor.Position).WorkingArea;
            int wanted = content.PreferredSize.Height + bottom.PreferredSize.Height + Padding.Vertical + 8;
            int width = Math.Max(content.PreferredSize.Width, bottom.PreferredSize.Width)
                        + SystemInformation.VerticalScrollBarWidth + Padding.Horizontal + 8;
            int caption = SystemInformation.CaptionHeight + 2 * SystemInformation.FrameBorderSize.Height;
            ClientSize = new Size(Math.Min(width, area.Width - 20),
                                  Math.Min(wanted, Math.Max(300, area.Height - caption - 40)));
            MinimumSize = new Size(Math.Min(width, area.Width - 20), Math.Min(320, area.Height - 40));

            AcceptButton = btnRun;
            FormClosing += OnFormClosing;
        }

        private GroupBox BuildServerGroup()
        {
            var grid = NewGrid();
            txtUrl = new TextBox { Width = 360 };
            txtUrl.Leave += (s, e) => OnServerChanged();
            lblUrlHint = Hint("Base address only, for example http://192.168.1.10:5000 or https://signage.example.com.");
            AddRow(grid, "Server URL", txtUrl);
            AddRow(grid, null, lblUrlHint);

            txtToken = new TextBox { Width = 360, UseSystemPasswordChar = true };
            txtToken.TextChanged += (s, e) => _tokenEdited = true;
            chkShowToken = new CheckBox { Text = "Show", AutoSize = true };
            chkShowToken.CheckedChanged += (s, e) => txtToken.UseSystemPasswordChar = !chkShowToken.Checked;
            lnkForget = new LinkLabel { Text = "Forget saved token", AutoSize = true, Margin = new Padding(3, 6, 3, 3) };
            lnkForget.LinkClicked += (s, e) => ForgetToken();
            AddRow(grid, "API token", Flow(txtToken, chkShowToken));
            AddRow(grid, null, Flow(Hint("Create one in AiSignX: your user menu > API tokens, scope media:write."), lnkForget));

            txtPin = new TextBox { Width = 360 };
            AddRow(grid, "Certificate SHA-256", txtPin);
            AddRow(grid, null, Hint("Optional. Only for a self-signed HTTPS certificate; leave empty to let Windows decide."));

            btnTest = new Button { Text = "Test connection", AutoSize = true };
            btnTest.Click += (s, e) => RunSafe(TestConnectionAsync);
            AddRow(grid, null, btnTest);
            return Group("AiSignX server", grid);
        }

        private GroupBox BuildTargetGroup()
        {
            var grid = NewGrid();
            rbNew = new RadioButton { Text = "Upload as a new media item", AutoSize = true };
            rbReplace = new RadioButton { Text = "Replace an existing item (keeps its ID and its place in playlists)", AutoSize = true };
            rbNew.CheckedChanged += (s, e) => UpdateModeControls();
            rbReplace.CheckedChanged += (s, e) => UpdateModeControls();

            txtName = new TextBox { Width = 300 };
            numDuration = new NumericUpDown { Minimum = 0, Maximum = 86400, Width = 90 };
            numId = new NumericUpDown { Minimum = 0, Maximum = int.MaxValue, Width = 110 };
            numId.ValueChanged += (s, e) => lblChosen.Text = "";
            btnChoose = new Button { Text = "Choose...", AutoSize = true };
            btnChoose.Click += (s, e) => ChooseMedia();
            lblChosen = new Label { AutoSize = true, Margin = new Padding(3, 8, 3, 3), ForeColor = SystemColors.GrayText };

            AddRow(grid, null, rbNew, span: true);
            AddRow(grid, "Name", txtName);
            AddRow(grid, "Fallback duration", Flow(numDuration, Hint("seconds; 0 = server default. A video plays for its own length.")));
            AddRow(grid, null, rbReplace, span: true);
            AddRow(grid, "Media ID", Flow(numId, btnChoose, lblChosen));
            return Group("Upload as", grid);
        }

        private GroupBox BuildVideoGroup()
        {
            var grid = NewGrid();
            cmbResolution = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 160 };
            foreach (int height in VideoOptions.Resolutions)
                cmbResolution.Items.Add(new Resolution(height));
            numFps = new NumericUpDown { Minimum = 1, Maximum = 60, Width = 70 };
            numQuality = new NumericUpDown { Minimum = 1, Maximum = 100, Width = 70 };
            chkTimings = new CheckBox { Text = "Use recorded slide timings and narrations", AutoSize = true };
            numSlideSeconds = new NumericUpDown { Minimum = 1, Maximum = 600, Width = 70 };

            AddRow(grid, "Resolution", cmbResolution);
            AddRow(grid, "Frames per second", numFps);
            AddRow(grid, "Quality (1-100)", numQuality);
            AddRow(grid, null, chkTimings);
            AddRow(grid, "Seconds per slide", Flow(numSlideSeconds, Hint("for slides without a recorded timing")));
            return Group("Video", grid);
        }

        private sealed class Resolution
        {
            public Resolution(int height) { Height = height; }
            public int Height { get; }
            public override string ToString()
            {
                switch (Height)
                {
                    case 480: return "480p (854 x 480)";
                    case 720: return "720p HD";
                    case 1080: return "1080p Full HD";
                    case 2160: return "2160p 4K";
                    default: return Height + "p";
                }
            }
        }

        private static TableLayoutPanel NewGrid()
        {
            var grid = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, Dock = DockStyle.Fill };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            return grid;
        }

        private static GroupBox Group(string title, Control content)
        {
            var box = new GroupBox { Text = title, AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(8) };
            box.Controls.Add(content);
            return box;
        }

        private static void AddRow(TableLayoutPanel grid, string label, Control control, bool span = false)
        {
            int row = grid.RowCount++;
            grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            if (span)
            {
                grid.Controls.Add(control, 0, row);
                grid.SetColumnSpan(control, 2);
                return;
            }
            if (label != null)
                grid.Controls.Add(new Label { Text = label, AutoSize = true, Margin = new Padding(3, 7, 8, 3) }, 0, row);
            grid.Controls.Add(control, 1, row);
        }

        private static FlowLayoutPanel Flow(params Control[] controls)
        {
            var flow = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = Padding.Empty };
            flow.Controls.AddRange(controls);
            return flow;
        }

        private static Label Hint(string text)
        {
            return new Label
            {
                Text = text, AutoSize = true, MaximumSize = new Size(380, 0),
                ForeColor = SystemColors.GrayText, Margin = new Padding(3, 4, 3, 3),
            };
        }

        // ---- settings in and out of the controls ----------------------------------------

        private void ShowOptions()
        {
            txtUrl.Text = string.IsNullOrWhiteSpace(_options.ServerUrl) ? _settings.LastServerUrl : _options.ServerUrl;
            LoadServerSecrets(force: true);

            rbReplace.Checked = _options.ReplaceMode;
            rbNew.Checked = !_options.ReplaceMode;
            txtName.Text = string.IsNullOrWhiteSpace(_options.MediaName) ? _presentationName : _options.MediaName;
            numDuration.Value = _options.FallbackDuration;
            numId.Value = _options.MediaId;
            lblChosen.Text = "";

            VideoOptions v = _options.Video;
            cmbResolution.SelectedIndex = Math.Max(0, Array.IndexOf(VideoOptions.Resolutions, v.VerticalResolution));
            numFps.Value = v.FramesPerSecond;
            numQuality.Value = v.Quality;
            chkTimings.Checked = v.UseTimings;
            numSlideSeconds.Value = v.SecondsPerSlide;
            UpdateModeControls();
        }

        /// <summary>Copies the controls into _options and writes them into the presentation.</summary>
        private void SaveOptions(Uri baseUri)
        {
            _options.ServerUrl = ServerAddress.Display(baseUri);
            _options.ReplaceMode = rbReplace.Checked;
            _options.MediaId = (int)numId.Value;
            string name = txtName.Text.Trim();
            _options.MediaName = name == _presentationName ? "" : name;
            _options.FallbackDuration = (int)numDuration.Value;
            _options.Video.VerticalResolution = ((Resolution)cmbResolution.SelectedItem).Height;
            _options.Video.FramesPerSecond = (int)numFps.Value;
            _options.Video.Quality = (int)numQuality.Value;
            _options.Video.UseTimings = chkTimings.Checked;
            _options.Video.SecondsPerSlide = (int)numSlideSeconds.Value;
            try
            {
                _options.Save(_presentation);
            }
            catch (System.Runtime.InteropServices.COMException)
            {
                // A read-only or protected presentation: the upload still works, the settings
                // are just not remembered in the file.
            }
            _settings.LastServerUrl = _options.ServerUrl;
        }

        private void UpdateModeControls()
        {
            bool replace = rbReplace.Checked;
            txtName.Enabled = !replace;
            numDuration.Enabled = !replace;
            numId.Enabled = replace;
            btnChoose.Enabled = replace && !IsBusy;
        }

        /// <summary>Puts the saved token and pin of the server in the URL box into the boxes.
        /// A token the user typed is kept unless the new server has its own.</summary>
        private void LoadServerSecrets(bool force)
        {
            Uri uri;
            try { uri = ServerAddress.Parse(txtUrl.Text); }
            catch (FormatException) { UpdateUrlHint(null); return; }
            UpdateUrlHint(uri);

            string key = ServerAddress.Key(uri);
            if (!force && key == _secretsKey)
                return;
            string token = _settings.GetToken(key);
            if (force || token.Length > 0 || !_tokenEdited)
            {
                txtToken.Text = token;
                _tokenEdited = false;
            }
            txtPin.Text = CertificatePin.Format(_settings.GetPin(key));
            _secretsKey = key;
        }

        private void OnServerChanged()
        {
            if (!IsBusy)
                LoadServerSecrets(force: false);
        }

        private void UpdateUrlHint(Uri uri)
        {
            bool plain = uri != null && ServerAddress.IsPlainHttpOverNetwork(uri);
            lblUrlHint.ForeColor = plain ? Color.DarkRed : SystemColors.GrayText;
            lblUrlHint.Text = plain
                ? "Plain HTTP: the token and video cross the network unencrypted. Use HTTPS outside your own network."
                : "Base address only, for example http://192.168.1.10:5000 or https://signage.example.com.";
        }

        private void ForgetToken()
        {
            try
            {
                Uri uri = ServerAddress.Parse(txtUrl.Text);
                _settings.ForgetToken(ServerAddress.Key(uri));
                txtToken.Text = "";
                _tokenEdited = false;
                SetStatus("Forgot the saved token for " + ServerAddress.Display(uri) + ".");
            }
            catch (FormatException ex)
            {
                ShowError(ex.Message);
            }
        }
    }
}
