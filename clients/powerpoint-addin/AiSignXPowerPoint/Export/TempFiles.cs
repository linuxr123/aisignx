using System;
using System.IO;

namespace AiSignXPowerPoint.Export
{
    /// <summary>
    /// Exported videos live in %TEMP%\AiSignX-PowerPoint and are deleted after the upload.
    /// A file PowerPoint is still writing (after a cancelled export) cannot be deleted at
    /// once, so stale files are swept again on the next export and when the add-in starts
    /// or stops.
    /// </summary>
    public static class TempFiles
    {
        private static readonly TimeSpan StaleAfter = TimeSpan.FromHours(2);

        public static string Folder => Path.Combine(Path.GetTempPath(), "AiSignX-PowerPoint");

        /// <summary>A fresh path for an export; the folder is created if needed.</summary>
        public static string NewVideoPath()
        {
            Directory.CreateDirectory(Folder);
            return Path.Combine(Folder, "export-" + Guid.NewGuid().ToString("N") + ".mp4");
        }

        /// <summary>Deletes the file; false (no exception) when it is missing or still locked.</summary>
        public static bool TryDelete(string path)
        {
            if (string.IsNullOrEmpty(path))
                return true;
            try
            {
                if (File.Exists(path))
                    File.Delete(path);
                return true;
            }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
        }

        /// <summary>Removes old exports. <paramref name="all"/> removes every unlocked file
        /// (used when PowerPoint closes).</summary>
        public static void Sweep(bool all = false)
        {
            try
            {
                var dir = new DirectoryInfo(Folder);
                if (!dir.Exists)
                    return;
                DateTime cutoff = DateTime.UtcNow - StaleAfter;
                foreach (FileInfo file in dir.GetFiles("export-*.mp4"))
                {
                    if (all || file.LastWriteTimeUtc < cutoff)
                        TryDelete(file.FullName);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }
}
