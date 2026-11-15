using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using AiSignXPowerPoint.Api;

namespace AiSignXPowerPoint.UI
{
    /// <summary>
    /// Lists the server's videos and images (GET /api/v1/media, newest first) so the user can
    /// pick the item to replace. Needs a token with media:read. A load error that leaves the
    /// list empty closes the dialog and is handed back in <see cref="Error"/>.
    /// </summary>
    public sealed class MediaPickerForm : Form
    {
        private const int PageSize = 100;
        private readonly MediaApiClient _client;
        private readonly List<MediaItem> _items = new List<MediaItem>();
        private readonly CancellationTokenSource _cancel = new CancellationTokenSource();
        private int _page;
        private bool _hasMore = true;

        private readonly TextBox txtFilter;
        private readonly ListView list;
        private readonly Label lblInfo;
        private readonly Button btnMore, btnOk;

        public MediaPickerForm(MediaApiClient client)
        {
            _client = client ?? throw new ArgumentNullException(nameof(client));
            Text = "Choose the media item to replace";
            Icon = Icons.App();
            Font = SystemFonts.MessageBoxFont;
            AutoScaleMode = AutoScaleMode.Font;
            StartPosition = FormStartPosition.CenterParent;
            MinimizeBox = false;
            ShowInTaskbar = false;
            Size = new Size(640, 460);
            MinimumSize = new Size(480, 320);
            Padding = new Padding(8);

            txtFilter = new TextBox { Dock = DockStyle.Top };
            txtFilter.TextChanged += (s, e) => Fill();
            var filterLabel = new Label { Text = "Filter by name or ID:", Dock = DockStyle.Top, AutoSize = true };

            list = new ListView
            {
                Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, MultiSelect = false,
                HideSelection = false,
            };
            list.Columns.Add("ID", 60, HorizontalAlignment.Right);
            list.Columns.Add("Name", 280);
            list.Columns.Add("Type", 70);
            list.Columns.Add("Size", 80, HorizontalAlignment.Right);
            list.Columns.Add("Added", 110);
            list.SelectedIndexChanged += (s, e) => btnOk.Enabled = list.SelectedItems.Count == 1;
            list.DoubleClick += (s, e) => { if (list.SelectedItems.Count == 1) Accept(); };

            lblInfo = new Label { AutoSize = true, Margin = new Padding(3, 8, 3, 3), Text = "Loading..." };
            btnMore = new Button { Text = "Load more", AutoSize = true, Enabled = false };
            btnMore.Click += async (s, e) => await LoadPageAsync();
            btnOk = new Button { Text = "Choose", AutoSize = true, Enabled = false };
            btnOk.Click += (s, e) => Accept();
            var btnCancel = new Button { Text = "Cancel", AutoSize = true, DialogResult = DialogResult.Cancel };

            var bottom = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, FlowDirection = FlowDirection.RightToLeft };
            bottom.Controls.AddRange(new Control[] { btnCancel, btnOk, btnMore, lblInfo });

            Controls.Add(list);
            Controls.Add(txtFilter);
            Controls.Add(filterLabel);
            Controls.Add(bottom);
            AcceptButton = btnOk;
            CancelButton = btnCancel;

            Shown += async (s, e) => await LoadPageAsync();
            FormClosed += (s, e) => _cancel.Cancel();
        }

        /// <summary>The chosen item when the dialog returns OK.</summary>
        public MediaItem Selected { get; private set; }

        /// <summary>Why the list could not be loaded, when the dialog closed because of it.</summary>
        public ApiException Error { get; private set; }

        private async Task LoadPageAsync()
        {
            if (!_hasMore) return;
            btnMore.Enabled = false;
            lblInfo.Text = "Loading...";
            int next = _page + 1;
            CancellationToken cancel = _cancel.Token;
            try
            {
                MediaPage page = await Task.Run(() => _client.ListMediaAsync(next, PageSize, cancel));
                if (IsDisposed) return;
                _page = next;
                _hasMore = page.HasMore && page.Items.Count > 0;
                foreach (MediaItem item in page.Items)
                    if (item.IsReplaceable)
                        _items.Add(item);
                Fill();
                lblInfo.Text = _items.Count + " video(s) and image(s)" + (_hasMore ? ", more on the server" : "");
                btnMore.Enabled = _hasMore;
            }
            catch (OperationCanceledException)
            {
            }
            catch (ApiException ex)
            {
                if (IsDisposed) return;
                if (ex.Code == "insufficient_scope")
                    ex = new ApiException(ex.Status, ex.Code, ex.ServerMessage, null,
                        "This token cannot list media: it needs the media:read scope as well. Type the ID "
                        + "instead; it is shown on each card under Media in AiSignX.");
                if (_items.Count == 0)
                {
                    Error = ex;
                    DialogResult = DialogResult.Cancel;
                    Close();
                    return;
                }
                lblInfo.Text = "Could not load more: " + ex.Message;
                btnMore.Enabled = true;
            }
        }

        private void Fill()
        {
            string filter = txtFilter.Text.Trim();
            list.BeginUpdate();
            list.Items.Clear();
            foreach (MediaItem item in _items)
            {
                string id = item.Id.ToString(CultureInfo.InvariantCulture);
                if (filter.Length > 0 && id != filter
                    && (item.Name ?? "").IndexOf(filter, StringComparison.CurrentCultureIgnoreCase) < 0)
                    continue;
                var row = new ListViewItem(id) { Tag = item };
                row.SubItems.Add(item.Name ?? "");
                row.SubItems.Add(item.Type ?? "");
                row.SubItems.Add(item.Size.HasValue ? (item.Size.Value / 1048576.0).ToString("0.0", CultureInfo.CurrentCulture) + " MB" : "");
                row.SubItems.Add(FormatDate(item.CreatedAt));
                list.Items.Add(row);
            }
            list.EndUpdate();
            btnOk.Enabled = list.SelectedItems.Count == 1;
        }

        private static string FormatDate(string iso)
        {
            return DateTime.TryParse(iso, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal, out DateTime utc)
                ? utc.ToLocalTime().ToString("g", CultureInfo.CurrentCulture)
                : "";
        }

        private void Accept()
        {
            Selected = list.SelectedItems.Count == 1 ? list.SelectedItems[0].Tag as MediaItem : null;
            if (Selected == null) return;
            DialogResult = DialogResult.OK;
            Close();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) _cancel.Dispose();
            base.Dispose(disposing);
        }
    }
}
