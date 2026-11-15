using System;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Security;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace AiSignXPowerPoint.Api
{
    /// <summary>
    /// Client for the AiSignX media API (docs/api.md): list, upload and replace media with a
    /// personal API token. Every failure is an <see cref="ApiException"/> with a message for
    /// the user, except cancellation (<see cref="OperationCanceledException"/>).
    /// Methods are thread-safe and do not need the UI thread.
    /// </summary>
    public sealed class MediaApiClient : IDisposable
    {
        public const string UserAgent = "AiSignX-PowerPoint/3.0";
        private static readonly TimeSpan ShortCallTimeout = TimeSpan.FromSeconds(30);

        private readonly HttpClient _http;
        private readonly string _pin;
        private volatile string _rejectedCertificate;

        /// <param name="baseUri">From <see cref="ServerAddress.Parse"/> (ends in "/").</param>
        /// <param name="token">The raw API token ("asx_...").</param>
        /// <param name="pinnedSha256">Optional pinned certificate (see <see cref="CertificatePin"/>).</param>
        public MediaApiClient(Uri baseUri, string token, string pinnedSha256 = null)
        {
            if (baseUri == null) throw new ArgumentNullException(nameof(baseUri));
            if (string.IsNullOrWhiteSpace(token))
                throw new ArgumentException("Enter the API token.", nameof(token));
            _pin = CertificatePin.Normalize(pinnedSha256);
            EnableModernTls();

            var handler = new HttpClientHandler
            {
                AllowAutoRedirect = false, // a redirect would drop the Authorization header or the body
                UseCookies = false,
                ServerCertificateCustomValidationCallback = ValidateCertificate,
            };
            _http = new HttpClient(handler, disposeHandler: true)
            {
                BaseAddress = baseUri,
                Timeout = Timeout.InfiniteTimeSpan, // uploads can take long; callers cancel instead
            };
            _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token.Trim());
            _http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
            _http.DefaultRequestHeaders.UserAgent.ParseAdd(UserAgent);
            _http.DefaultRequestHeaders.ExpectContinue = false;
        }

        public Uri BaseAddress => _http.BaseAddress;

        /// <summary>GET /api/v1/media (needs media:read). Newest first.</summary>
        public async Task<MediaPage> ListMediaAsync(int page, int perPage, CancellationToken cancel)
        {
            string path = string.Format(CultureInfo.InvariantCulture, "api/v1/media?page={0}&per_page={1}",
                                        Math.Max(1, page), Math.Max(1, Math.Min(100, perPage)));
            string body = await SendShortAsync(HttpMethod.Get, path, cancel).ConfigureAwait(false);
            return MediaPage.Parse(body);
        }

        /// <summary>GET /api/v1/media/&lt;id&gt; (needs media:read).</summary>
        public async Task<MediaItem> GetMediaAsync(int mediaId, CancellationToken cancel)
        {
            string body = await SendShortAsync(HttpMethod.Get, "api/v1/media/" + mediaId.ToString(CultureInfo.InvariantCulture), cancel)
                .ConfigureAwait(false);
            return MediaItem.Parse(body);
        }

        /// <summary>POST /api/v1/media: a new item (needs media:write). Returns it, with its id.</summary>
        /// <param name="name">Display name; null keeps the server default (the file name).</param>
        /// <param name="durationSeconds">Fallback duration, 1-86400; null lets the server decide.</param>
        public Task<MediaItem> UploadAsync(string filePath, string name, int? durationSeconds,
                                           IProgress<UploadProgress> progress, CancellationToken cancel)
        {
            return SendFileAsync("api/v1/media", filePath, name, durationSeconds, progress, cancel);
        }

        /// <summary>POST /api/v1/media/&lt;id&gt;/replace: new file for an existing item (needs
        /// media:write). The id and playlists stay; name and duration change only when given.</summary>
        public Task<MediaItem> ReplaceAsync(int mediaId, string filePath, string name, int? durationSeconds,
                                            IProgress<UploadProgress> progress, CancellationToken cancel)
        {
            if (mediaId < 1) throw new ArgumentOutOfRangeException(nameof(mediaId), "Enter the media ID to replace.");
            string path = "api/v1/media/" + mediaId.ToString(CultureInfo.InvariantCulture) + "/replace";
            return SendFileAsync(path, filePath, name, durationSeconds, progress, cancel);
        }

        /// <summary>The multipart body used for uploads (exposed for tests).</summary>
        internal static MultipartFormDataContent BuildForm(string filePath, string name, int? durationSeconds,
                                                           IProgress<UploadProgress> progress, CancellationToken cancel)
        {
            if (!File.Exists(filePath))
                throw new FileNotFoundException("The exported video was not found.", filePath);
            if (durationSeconds.HasValue && (durationSeconds.Value < 1 || durationSeconds.Value > 86400))
                throw new ArgumentOutOfRangeException(nameof(durationSeconds), "Duration must be 1 to 86400 seconds.");

            var form = new MultipartFormDataContent();
            string extension = Path.GetExtension(filePath).ToLowerInvariant();
            string fileName = AsciiFileName(name ?? Path.GetFileNameWithoutExtension(filePath)) + extension;
            var file = new ProgressFileContent(filePath, MimeFor(extension), progress, cancel);
            form.Add(file, "file", fileName);
            if (!string.IsNullOrWhiteSpace(name))
                form.Add(new StringContent(name.Trim(), Encoding.UTF8), "name");
            if (durationSeconds.HasValue)
                form.Add(new StringContent(durationSeconds.Value.ToString(CultureInfo.InvariantCulture)), "duration");
            return form;
        }

        /// <summary>The server takes the media type from the file name's extension, and some
        /// servers mangle non-ASCII file names, so the name field carries the real name and the
        /// file name is kept to plain ASCII.</summary>
        internal static string AsciiFileName(string name)
        {
            var sb = new StringBuilder();
            foreach (char c in name ?? "")
            {
                bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
                          || c == '-' || c == '_' || c == '.';
                char next = ok ? c : '_';
                if (next == '_' && sb.Length > 0 && sb[sb.Length - 1] == '_')
                    continue;
                sb.Append(next);
                if (sb.Length >= 80) break;
            }
            string result = sb.ToString().Trim('_', '.');
            return result.Length == 0 ? "presentation" : result;
        }

        private static string MimeFor(string extension)
        {
            switch (extension)
            {
                case ".mp4": return "video/mp4";
                case ".m4v": return "video/x-m4v";
                case ".mov": return "video/quicktime";
                case ".wmv": return "video/x-ms-wmv";
                case ".png": return "image/png";
                case ".jpg":
                case ".jpeg": return "image/jpeg";
                default: return "application/octet-stream";
            }
        }

        private async Task<MediaItem> SendFileAsync(string path, string filePath, string name, int? duration,
                                                    IProgress<UploadProgress> progress, CancellationToken cancel)
        {
            using (var form = BuildForm(filePath, name, duration, progress, cancel))
            using (var request = new HttpRequestMessage(HttpMethod.Post, path) { Content = form })
            {
                string body = await SendAsync(request, cancel, isUpload: true).ConfigureAwait(false);
                return MediaItem.Parse(body);
            }
        }

        private async Task<string> SendShortAsync(HttpMethod method, string path, CancellationToken cancel)
        {
            using (var timeout = new CancellationTokenSource(ShortCallTimeout))
            using (var linked = CancellationTokenSource.CreateLinkedTokenSource(cancel, timeout.Token))
            using (var request = new HttpRequestMessage(method, path))
            {
                try
                {
                    return await SendAsync(request, linked.Token, isUpload: false).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (timeout.IsCancellationRequested && !cancel.IsCancellationRequested)
                {
                    throw new ApiException(0, null, null, null,
                        "The server did not answer within " + (int)ShortCallTimeout.TotalSeconds + " seconds.");
                }
            }
        }

        /// <summary>Sends the request; returns the body of a 2xx answer, otherwise throws.</summary>
        private async Task<string> SendAsync(HttpRequestMessage request, CancellationToken cancel, bool isUpload)
        {
            _rejectedCertificate = null;
            HttpResponseMessage response;
            try
            {
                response = await _http.SendAsync(request, HttpCompletionOption.ResponseContentRead, cancel)
                                      .ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                cancel.ThrowIfCancellationRequested();
                throw;
            }
            catch (HttpRequestException ex)
            {
                cancel.ThrowIfCancellationRequested();
                throw TransportError(ex, isUpload);
            }
            catch (IOException ex) // stream errors that escape HttpClient on .NET Framework
            {
                cancel.ThrowIfCancellationRequested();
                throw TransportError(ex, isUpload);
            }

            using (response)
            {
                string body = response.Content == null ? "" : await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                int status = (int)response.StatusCode;
                if (status >= 200 && status < 300)
                    return body;
                if (status >= 300 && status < 400)
                {
                    string target = response.Headers.Location == null ? "another address" : response.Headers.Location.ToString();
                    throw new ApiException(status, null, null, null,
                        "The server redirected to " + target + ". Enter that address as the server URL "
                        + "(for example https:// instead of http://).");
                }
                throw ApiErrors.FromResponse(status, body, RetryAfter(response), response.ReasonPhrase);
            }
        }

        private static TimeSpan? RetryAfter(HttpResponseMessage response)
        {
            RetryConditionHeaderValue value = response.Headers.RetryAfter;
            if (value == null) return null;
            if (value.Delta.HasValue) return value.Delta.Value;
            if (value.Date.HasValue)
            {
                TimeSpan wait = value.Date.Value - DateTimeOffset.UtcNow;
                return wait > TimeSpan.Zero ? wait : TimeSpan.Zero;
            }
            return null;
        }

        private ApiException TransportError(Exception ex, bool isUpload)
        {
            string host = _http.BaseAddress.Host + ":" + _http.BaseAddress.Port.ToString(CultureInfo.InvariantCulture);
            string rejected = _rejectedCertificate;
            if (rejected != null)
            {
                string text = _pin == null
                    ? "Windows does not trust the server's HTTPS certificate. Use a certificate from a "
                      + "trusted CA, install your private CA on this PC, or pin this server's certificate."
                    : "The server's HTTPS certificate does not match the pinned fingerprint. If the "
                      + "certificate was renewed, check the new fingerprint and update the pin.";
                return new ApiException(0, null, null, null, text, ex) { PresentedCertificateSha256 = rejected };
            }

            WebException web = Find<WebException>(ex);
            WebExceptionStatus status = web?.Status ?? WebExceptionStatus.UnknownError;
            string message;
            switch (status)
            {
                case WebExceptionStatus.NameResolutionFailure:
                    message = "The server name \"" + _http.BaseAddress.Host + "\" could not be found. Check the server URL.";
                    break;
                case WebExceptionStatus.ConnectFailure:
                    message = "Could not connect to " + host + ". Check the server URL, that AiSignX is "
                              + "running, and that a firewall is not blocking the port.";
                    break;
                case WebExceptionStatus.TrustFailure:
                case WebExceptionStatus.SecureChannelFailure:
                    message = "The secure (HTTPS) connection to " + host + " failed. If the server uses "
                              + "plain HTTP, start the URL with http://.";
                    break;
                case WebExceptionStatus.Timeout:
                    message = "The server did not answer in time.";
                    break;
                default:
                    message = isUpload
                        ? "The connection closed during the upload. The server or a proxy may have refused a "
                          + "file this large, or the network dropped. (" + Innermost(ex).Message + ")"
                        : "The request to " + host + " failed: " + Innermost(ex).Message;
                    break;
            }
            if (Find<AuthenticationException>(ex) != null && status != WebExceptionStatus.TrustFailure)
                message = "The secure (HTTPS) connection to " + host + " failed: " + Innermost(ex).Message;
            return new ApiException(0, null, null, null, message, ex);
        }

        private bool ValidateCertificate(HttpRequestMessage request, X509Certificate2 certificate,
                                         X509Chain chain, SslPolicyErrors errors)
        {
            bool trusted = CertificatePin.IsTrusted(certificate, errors, _pin);
            if (!trusted && certificate != null)
                _rejectedCertificate = CertificatePin.Sha256(certificate);
            return trusted;
        }

        private static T Find<T>(Exception ex) where T : Exception
        {
            for (Exception e = ex; e != null; e = e.InnerException)
                if (e is T match) return match;
            return null;
        }

        private static Exception Innermost(Exception ex)
        {
            while (ex.InnerException != null) ex = ex.InnerException;
            return ex;
        }

        private static void EnableModernTls()
        {
            // .NET Framework 4.8 uses the OS defaults (TLS 1.2/1.3) unless something in the
            // process pinned an explicit protocol list. Leave the OS default alone (adding a
            // flag to it would switch TLS 1.3 off); only add TLS 1.2 to an explicit old list.
            try
            {
                SecurityProtocolType current = ServicePointManager.SecurityProtocol;
                if (current != SecurityProtocolType.SystemDefault && (current & SecurityProtocolType.Tls12) == 0)
                    ServicePointManager.SecurityProtocol = current | SecurityProtocolType.Tls12;
            }
            catch (NotSupportedException) { }
        }

        public void Dispose() => _http.Dispose();
    }
}
