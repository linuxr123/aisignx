using System;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using AiSignXPowerPoint.Api;
using AiSignXPowerPoint.Export;

namespace AiSignXPowerPoint.UI
{
    /// <summary>The dialog's actions: test the connection, choose an item, export and upload.</summary>
    public sealed partial class UploadForm
    {
        private const string Caption = "AiSignX";

        private bool IsBusy => _cancel != null;

        /// <summary>The server, token and pin from the boxes; saves the token (DPAPI) and pin
        /// for that server. Shows a message and returns null when something is missing.</summary>
        private MediaApiClient CreateClient(out Uri baseUri)
        {
            baseUri = null;
            string pin;
            try
            {
                baseUri = ServerAddress.Parse(txtUrl.Text);
                pin = CertificatePin.Normalize(txtPin.Text);
            }
            catch (FormatException ex)
            {
                ShowError(ex.Message);
                return null;
            }
            string token = txtToken.Text.Trim();
            if (token.Length == 0)
            {
                ShowError("Enter the API token. " + "Create one in AiSignX under your user menu > API tokens.");
                txtToken.Focus();
                return null;
            }
            txtUrl.Text = ServerAddress.Display(baseUri);
            string key = ServerAddress.Key(baseUri);
            _settings.SaveToken(key, token);
            _settings.SavePin(key, pin);
            _secretsKey = key;
            _tokenEdited = false;
            txtPin.Text = CertificatePin.Format(pin);
            return new MediaApiClient(baseUri, token, pin);
        }

        private async Task TestConnectionAsync()
        {
            if (IsBusy) return;
            MediaApiClient client = CreateClient(out Uri baseUri);
            if (client == null) return;
            using (client)
            {
                BeginWork("Connecting to " + ServerAddress.Display(baseUri) + "...");
                try
                {
                    MediaPage page = await Task.Run(() => client.ListMediaAsync(1, 1, _cancel.Token));
                    SetStatus("Connected. The token works and can see " + page.Total + " media item(s).");
                }
                catch (ApiException ex) when (ex.Code == "insufficient_scope")
                {
                    SetStatus("Connected and the token is valid. It cannot list media (no media:read), "
                              + "which is fine for uploading as long as it has media:write.");
                }
                catch (ApiException ex)
                {
                    HandleApiError(ex, baseUri);
                }
                catch (OperationCanceledException)
                {
                    SetStatus("Cancelled.");
                }
                finally
                {
                    EndWork();
                }
            }
        }

        private void ChooseMedia()
        {
            if (IsBusy) return;
            MediaApiClient client = CreateClient(out Uri baseUri);
            if (client == null) return;
            using (client)
            using (var picker = new MediaPickerForm(client))
            {
                if (picker.ShowDialog(this) != DialogResult.OK || picker.Selected == null)
                {
                    if (picker.Error != null)
                        HandleApiError(picker.Error, baseUri);
                    return;
                }
                numId.Value = picker.Selected.Id;
                lblChosen.Text = picker.Selected.Name;
            }
        }

        private async Task RunAsync()
        {
            if (IsBusy) return;
            bool replace = rbReplace.Checked;
            int mediaId = (int)numId.Value;
            if (replace && mediaId < 1)
            {
                ShowError("Enter the ID of the media item to replace, or click Choose. The ID is shown on each card under Media in AiSignX.");
                return;
            }
            MediaApiClient client = CreateClient(out Uri baseUri);
            if (client == null) return;
            SaveOptions(baseUri);

            string videoPath = null;
            string phase = "check";
            using (client)
            {
                BeginWork("Checking the server...");
                CancellationToken cancel = _cancel.Token;
                try
                {
                    MediaItem target = await PreflightAsync(client, replace, mediaId, cancel);

                    phase = "export";
                    videoPath = TempFiles.NewVideoPath();
                    TempFiles.Sweep();
                    progress.Style = ProgressBarStyle.Marquee;
                    var elapsed = new Progress<TimeSpan>(t => SetStatus(
                        "Exporting the video in PowerPoint... " + FormatElapsed(t)
                        + ". Large presentations can take several minutes."));
                    await VideoExporter.ExportAsync(_presentation, videoPath, _options.Video, elapsed, cancel);

                    phase = "upload";
                    progress.Style = ProgressBarStyle.Continuous;
                    progress.Value = 0;
                    var sent = new Progress<UploadProgress>(p =>
                    {
                        progress.Value = p.Percent;
                        SetStatus("Uploading... " + Megabytes(p.BytesSent) + " of " + Megabytes(p.TotalBytes)
                                  + " MB (" + p.Percent + "%)");
                    });
                    string path = videoPath;
                    MediaItem result;
                    if (replace)
                    {
                        result = await Task.Run(() => client.ReplaceAsync(mediaId, path, null, null, sent, cancel), cancel);
                    }
                    else
                    {
                        string name = txtName.Text.Trim();
                        int duration = (int)numDuration.Value;
                        result = await Task.Run(() => client.UploadAsync(
                            path, name.Length == 0 ? _presentationName : name,
                            duration > 0 ? (int?)duration : null, sent, cancel), cancel);
                    }
                    progress.Value = 100;
                    ReportSuccess(result, replace, target);
                }
                catch (OperationCanceledException)
                {
                    SetStatus(phase == "export"
                        ? "Cancelled. PowerPoint may keep rendering in the background for a while; "
                          + "the unfinished file is removed later."
                        : "Cancelled.");
                }
                catch (ApiException ex)
                {
                    HandleApiError(ex, baseUri);
                }
                catch (Exception ex) when (ex is InvalidOperationException || ex is System.IO.IOException
                                           || ex is UnauthorizedAccessException
                                           || ex is System.Runtime.InteropServices.COMException)
                {
                    SetStatus("Failed.");
                    ShowError(ex.Message);
                }
                finally
                {
                    TempFiles.TryDelete(videoPath);
                    EndWork();
                }
            }
        }

        /// <summary>Checks the server and token before the (slow) export, so a typo does not
        /// cost minutes of rendering. For a replace it also checks that the item exists and is
        /// a file. A token without media:read cannot be checked further; it goes ahead.</summary>
        private static async Task<MediaItem> PreflightAsync(MediaApiClient client, bool replace, int mediaId,
                                                            CancellationToken cancel)
        {
            try
            {
                if (!replace)
                {
                    await Task.Run(() => client.ListMediaAsync(1, 1, cancel), cancel);
                    return null;
                }
                MediaItem item = await Task.Run(() => client.GetMediaAsync(mediaId, cancel), cancel);
                if (!item.IsReplaceable)
                    throw new ApiException(409, "not_replaceable", null, null,
                        "Media " + mediaId + " (\"" + item.Name + "\") is a " + item.Type
                        + ", not an uploaded file, so it cannot be replaced. Choose a video or image item.");
                return item;
            }
            catch (ApiException ex) when (ex.Code == "insufficient_scope")
            {
                return null;
            }
            catch (ApiException ex) when (ex.Code == "not_found" && replace)
            {
                throw new ApiException(404, "not_found", ex.ServerMessage, null,
                    "There is no media item with ID " + mediaId + ". Check the ID under Media in AiSignX, or click Choose.");
            }
        }

        private void ReportSuccess(MediaItem item, bool replaced, MediaItem before)
        {
            string name = string.IsNullOrEmpty(item.Name) ? "" : " \"" + item.Name + "\"";
            if (replaced)
            {
                SetStatus("Done: replaced media ID " + item.Id + name + ".");
                string note = before != null && before.Type == "image"
                    ? " It was an image before and is a video now."
                    : "";
                MessageBox.Show(this, "Replaced the file of media ID " + item.Id + name + "." + note
                                + "\n\nScreens that play it show the new version within seconds.",
                                Caption, MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            SetStatus("Done: uploaded as media ID " + item.Id + name + ".");
            DialogResult answer = MessageBox.Show(this,
                "Uploaded as media ID " + item.Id + name + ".\n\nAdd it to a playlist in AiSignX to show it "
                + "on screens.\n\nUpdate this same item (ID " + item.Id + ") the next time you upload this "
                + "presentation?", Caption, MessageBoxButtons.YesNo, MessageBoxIcon.Information,
                MessageBoxDefaultButton.Button1);
            numId.Value = item.Id;
            lblChosen.Text = item.Name ?? "";
            if (answer == DialogResult.Yes)
                rbReplace.Checked = true;
            _options.MediaId = item.Id;
            _options.ReplaceMode = rbReplace.Checked;
            try { _options.Save(_presentation); }
            catch (System.Runtime.InteropServices.COMException) { }
        }

        private void HandleApiError(ApiException ex, Uri baseUri)
        {
            SetStatus("Failed.");
            if (ex.IsCertificateRejected && !string.IsNullOrEmpty(ex.PresentedCertificateSha256))
            {
                OfferPin(ex, baseUri);
                return;
            }
            ShowError(ex.Message);
        }

        /// <summary>Trust on first use, with the user comparing the fingerprint.</summary>
        private void OfferPin(ApiException ex, Uri baseUri)
        {
            string fingerprint = CertificatePin.Format(ex.PresentedCertificateSha256);
            DialogResult answer = MessageBox.Show(this,
                ex.Message + "\n\nThe server " + baseUri.Host + " presented a certificate with this SHA-256 "
                + "fingerprint:\n\n" + fingerprint + "\n\nPin it only if it matches the fingerprint of your "
                + "server's certificate (the add-in's README shows how to read it on the server). "
                + "Pin this certificate?",
                Caption, MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes)
                return;
            txtPin.Text = fingerprint;
            _settings.SavePin(ServerAddress.Key(baseUri), ex.PresentedCertificateSha256);
            SetStatus("Pinned the certificate for " + baseUri.Host + ". Try again.");
        }

        // ---- busy state -----------------------------------------------------------------

        private void BeginWork(string status)
        {
            _cancel = new CancellationTokenSource();
            foreach (Control c in new Control[] { txtUrl, txtToken, txtPin, btnTest, btnRun, rbNew, rbReplace,
                                                  txtName, numDuration, numId, btnChoose, cmbResolution, numFps,
                                                  numQuality, chkTimings, numSlideSeconds, lnkForget })
                c.Enabled = false;
            btnClose.Text = "Cancel";
            progress.Value = 0;
            SetStatus(status);
        }

        private void EndWork()
        {
            _cancel?.Dispose();
            _cancel = null;
            foreach (Control c in new Control[] { txtUrl, txtToken, txtPin, btnTest, btnRun, rbNew, rbReplace,
                                                  cmbResolution, numFps, numQuality, chkTimings, numSlideSeconds,
                                                  lnkForget })
                c.Enabled = true;
            UpdateModeControls();
            progress.Style = ProgressBarStyle.Continuous;
            btnClose.Text = "Close";
            if (_closeWhenIdle)
                BeginInvoke(new Action(Close));
        }

        private void OnCloseClicked()
        {
            if (IsBusy)
            {
                _cancel.Cancel();
                SetStatus("Cancelling...");
                return;
            }
            Close();
        }

        private void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            if (!IsBusy)
                return;
            // Let the running task notice the cancel and clean up, then close.
            e.Cancel = true;
            _closeWhenIdle = true;
            _cancel.Cancel();
            SetStatus("Cancelling...");
        }

        private void SetStatus(string text) => lblStatus.Text = text;

        private void ShowError(string text)
        {
            MessageBox.Show(this, text, Caption, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        private static string Megabytes(long bytes) =>
            (bytes / 1048576.0).ToString("0.0", CultureInfo.CurrentCulture);

        private static string FormatElapsed(TimeSpan t) =>
            ((int)t.TotalMinutes).ToString(CultureInfo.InvariantCulture) + ":" + t.Seconds.ToString("00", CultureInfo.InvariantCulture);
    }
}
