using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Threading;
using System.Threading.Tasks;

namespace AiSignXPowerPoint.Api
{
    /// <summary>Bytes of the file sent so far.</summary>
    public struct UploadProgress
    {
        public UploadProgress(long sent, long total)
        {
            BytesSent = sent;
            TotalBytes = total;
        }

        public long BytesSent { get; }
        public long TotalBytes { get; }
        public int Percent => TotalBytes <= 0 ? 0 : (int)Math.Min(100, BytesSent * 100 / TotalBytes);
    }

    /// <summary>
    /// The file part of a multipart upload, streamed from disk in chunks (never loaded into
    /// memory as a whole) with progress reports and cancellation.
    /// </summary>
    internal sealed class ProgressFileContent : HttpContent
    {
        private const int ChunkSize = 64 * 1024;
        private readonly string _path;
        private readonly long _length;
        private readonly IProgress<UploadProgress> _progress;
        private readonly CancellationToken _cancel;

        public ProgressFileContent(string path, string mediaType, IProgress<UploadProgress> progress,
                                   CancellationToken cancel)
        {
            _path = path;
            _length = new FileInfo(path).Length;
            _progress = progress;
            _cancel = cancel;
            Headers.ContentType = new MediaTypeHeaderValue(mediaType);
        }

        protected override async Task SerializeToStreamAsync(Stream stream, TransportContext context)
        {
            var buffer = new byte[ChunkSize];
            long sent = 0;
            int lastPercent = -1;
            using (var file = new FileStream(_path, FileMode.Open, FileAccess.Read, FileShare.Read,
                                             ChunkSize, useAsync: true))
            {
                int read;
                while ((read = await file.ReadAsync(buffer, 0, buffer.Length, _cancel).ConfigureAwait(false)) > 0)
                {
                    await stream.WriteAsync(buffer, 0, read, _cancel).ConfigureAwait(false);
                    sent += read;
                    int percent = _length == 0 ? 100 : (int)(sent * 100 / _length);
                    if (percent != lastPercent)
                    {
                        lastPercent = percent;
                        _progress?.Report(new UploadProgress(sent, _length));
                    }
                }
            }
        }

        protected override bool TryComputeLength(out long length)
        {
            length = _length;
            return true;
        }
    }
}
