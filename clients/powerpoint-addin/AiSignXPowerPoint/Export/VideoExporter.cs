using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using PowerPoint = Microsoft.Office.Interop.PowerPoint;

namespace AiSignXPowerPoint.Export
{
    /// <summary>Options for Presentation.CreateVideo.</summary>
    public sealed class VideoOptions
    {
        public static readonly int[] Resolutions = { 480, 720, 1080, 2160 };

        /// <summary>Use the slides' recorded timings and narrations where they exist.</summary>
        public bool UseTimings { get; set; } = true;
        /// <summary>Seconds per slide that has no timing (1-600).</summary>
        public int SecondsPerSlide { get; set; } = 5;
        /// <summary>Video height in pixels: 480, 720, 1080 or 2160.</summary>
        public int VerticalResolution { get; set; } = 1080;
        /// <summary>Frames per second (1-60).</summary>
        public int FramesPerSecond { get; set; } = 30;
        /// <summary>1-100; higher is larger and sharper.</summary>
        public int Quality { get; set; } = 85;

        public void Clamp()
        {
            SecondsPerSlide = Math.Max(1, Math.Min(600, SecondsPerSlide));
            FramesPerSecond = Math.Max(1, Math.Min(60, FramesPerSecond));
            Quality = Math.Max(1, Math.Min(100, Quality));
            if (Array.IndexOf(Resolutions, VerticalResolution) < 0)
                VerticalResolution = 1080;
        }
    }

    /// <summary>
    /// Exports a presentation to MP4 with Presentation.CreateVideo. CreateVideo returns at
    /// once and PowerPoint renders in the background; this polls CreateVideoStatus without
    /// blocking the UI thread (awaits between polls), under a COM message filter.
    ///
    /// Call it on the thread that owns the presentation (PowerPoint's UI thread in the add-in).
    /// PowerPoint has no way to stop a running export: on cancel this stops waiting and the
    /// caller leaves the half-written file to <see cref="TempFiles.Sweep"/>.
    /// </summary>
    public static class VideoExporter
    {
        private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(500);
        private static readonly TimeSpan FileWait = TimeSpan.FromSeconds(30);

        /// <summary>True while PowerPoint is still rendering an earlier video of this presentation.</summary>
        public static bool IsBusy(PowerPoint.Presentation presentation)
        {
            using (OleMessageFilter.Register())
            {
                PowerPoint.PpMediaTaskStatus status = presentation.CreateVideoStatus;
                return status == PowerPoint.PpMediaTaskStatus.ppMediaTaskStatusInProgress
                       || status == PowerPoint.PpMediaTaskStatus.ppMediaTaskStatusQueued;
            }
        }

        /// <param name="elapsed">Reports the time spent so far, about twice a second.</param>
        public static async Task ExportAsync(PowerPoint.Presentation presentation, string outputPath,
                                             VideoOptions options, IProgress<TimeSpan> elapsed,
                                             CancellationToken cancel)
        {
            if (presentation == null) throw new ArgumentNullException(nameof(presentation));
            if (string.IsNullOrEmpty(outputPath)) throw new ArgumentNullException(nameof(outputPath));
            options = options ?? new VideoOptions();
            options.Clamp();

            using (OleMessageFilter.Register())
            {
                if (IsBusy(presentation))
                    throw new InvalidOperationException(
                        "PowerPoint is still exporting a video of this presentation (see the status bar). "
                        + "Wait until it finishes, then try again.");
                if (SlideCount(presentation) == 0)
                    throw new InvalidOperationException("The presentation has no slides to export.");

                var clock = Stopwatch.StartNew();
                try
                {
                    presentation.CreateVideo(outputPath, options.UseTimings, options.SecondsPerSlide,
                                             options.VerticalResolution, options.FramesPerSecond, options.Quality);
                }
                catch (COMException ex)
                {
                    throw new InvalidOperationException("PowerPoint could not start the video export: " + ex.Message, ex);
                }

                while (true)
                {
                    cancel.ThrowIfCancellationRequested();
                    PowerPoint.PpMediaTaskStatus status = presentation.CreateVideoStatus;
                    if (status == PowerPoint.PpMediaTaskStatus.ppMediaTaskStatusDone)
                        break;
                    if (status == PowerPoint.PpMediaTaskStatus.ppMediaTaskStatusFailed)
                        throw new InvalidOperationException(
                            "PowerPoint could not create the video. Check that the presentation plays, "
                            + "that embedded media is available, and that there is free disk space.");
                    if (status == PowerPoint.PpMediaTaskStatus.ppMediaTaskStatusNone && clock.Elapsed > TimeSpan.FromSeconds(10))
                        throw new InvalidOperationException("PowerPoint did not start the video export.");
                    elapsed?.Report(clock.Elapsed);
                    await Task.Delay(PollInterval, cancel);
                }
                elapsed?.Report(clock.Elapsed);
            }
            await WaitForFileAsync(outputPath, cancel);
        }

        private static int SlideCount(PowerPoint.Presentation presentation)
        {
            PowerPoint.Slides slides = presentation.Slides;
            try { return slides.Count; }
            finally { Marshal.ReleaseComObject(slides); }
        }

        /// <summary>"Done" can come a moment before the file is closed; wait until it can be
        /// opened for reading and is not empty.</summary>
        private static async Task WaitForFileAsync(string path, CancellationToken cancel)
        {
            var clock = Stopwatch.StartNew();
            while (true)
            {
                try
                {
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                    {
                        if (stream.Length > 0)
                            return;
                    }
                }
                catch (FileNotFoundException) { }
                catch (IOException) { } // still locked by PowerPoint

                if (clock.Elapsed > FileWait)
                    throw new IOException("PowerPoint reported the export as finished, but the video file is missing or empty.");
                await Task.Delay(300, cancel);
            }
        }
    }
}
