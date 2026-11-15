using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.InteropServices;
using AiSignXPowerPoint.Export;
using Office = Microsoft.Office.Core;
using PowerPoint = Microsoft.Office.Interop.PowerPoint;

namespace AiSignXPowerPoint.Settings
{
    /// <summary>
    /// Per-presentation settings, kept in the file's custom document properties ("AiSignX_*")
    /// so they travel with the presentation. Nothing secret is stored here: the API token and
    /// the certificate pin are per user and per server (<see cref="UserSettings"/>), so a file
    /// someone sends you cannot redirect your token or make you trust another certificate.
    ///
    /// Writing marks the presentation as changed; the settings are kept when the user saves it.
    /// Settings of the old "PowerPointVideoUploader" add-in (PPVU_*) are read as a fallback and
    /// removed on the next save; a plain-text token it stored is removed at once.
    /// </summary>
    public sealed class PresentationOptions
    {
        private const string Prefix = "AiSignX_";
        private const string LegacyPrefix = "PPVU_";

        public string ServerUrl { get; set; } = "";
        public bool ReplaceMode { get; set; }
        /// <summary>The item this presentation replaces (or was last uploaded as); 0 = none.</summary>
        public int MediaId { get; set; }
        /// <summary>Name for a new upload; empty = the presentation's name.</summary>
        public string MediaName { get; set; } = "";
        /// <summary>Fallback duration in seconds for a new upload; 0 = server default.</summary>
        public int FallbackDuration { get; set; }
        public VideoOptions Video { get; } = new VideoOptions();

        /// <summary>Reads the settings and removes a plain-text token left by the old add-in.
        /// <paramref name="removedLegacyToken"/> tells the caller to say so.</summary>
        public static PresentationOptions Load(PowerPoint.Presentation presentation, out bool removedLegacyToken)
        {
            Dictionary<string, object> props = ReadAll(presentation);
            var o = new PresentationOptions();
            o.ServerUrl = Str(props, "ServerUrl") ?? Str(props, LegacyPrefix + "BaseUrl", raw: true) ?? "";
            string mode = Str(props, "Mode");
            o.ReplaceMode = mode != null ? mode == "replace" : Int(props, LegacyPrefix + "IsReplace", 0, raw: true) != 0;
            o.MediaId = Math.Max(0, Int(props, "MediaId", Int(props, LegacyPrefix + "LastMediaId", 0, raw: true)));
            o.MediaName = Str(props, "MediaName") ?? "";
            o.FallbackDuration = Math.Max(0, Math.Min(86400, Int(props, "Duration", 0)));
            o.Video.VerticalResolution = Int(props, "Resolution", Int(props, LegacyPrefix + "VertResolution", 1080, raw: true));
            o.Video.FramesPerSecond = Int(props, "Fps", Int(props, LegacyPrefix + "Fps", 30, raw: true));
            o.Video.Quality = Int(props, "Quality", Int(props, LegacyPrefix + "Quality", 85, raw: true));
            o.Video.UseTimings = Int(props, "UseTimings", Int(props, LegacyPrefix + "UseTimings", 1, raw: true)) != 0;
            o.Video.SecondsPerSlide = Int(props, "SlideSeconds", Int(props, LegacyPrefix + "DefaultSlideDuration", 5, raw: true));
            o.Video.Clamp();

            removedLegacyToken = false;
            if (props.ContainsKey(LegacyPrefix + "Token"))
            {
                Delete(presentation, new[] { LegacyPrefix + "Token", LegacyPrefix + "IgnoreTls" });
                removedLegacyToken = true;
            }
            return o;
        }

        /// <summary>Writes the settings into the presentation (does not save the file).</summary>
        public void Save(PowerPoint.Presentation presentation)
        {
            Video.Clamp();
            var values = new Dictionary<string, object>
            {
                { Prefix + "ServerUrl", ServerUrl ?? "" },
                { Prefix + "Mode", ReplaceMode ? "replace" : "new" },
                { Prefix + "MediaId", MediaId },
                { Prefix + "MediaName", MediaName ?? "" },
                { Prefix + "Duration", FallbackDuration },
                { Prefix + "Resolution", Video.VerticalResolution },
                { Prefix + "Fps", Video.FramesPerSecond },
                { Prefix + "Quality", Video.Quality },
                { Prefix + "UseTimings", Video.UseTimings ? 1 : 0 },
                { Prefix + "SlideSeconds", Video.SecondsPerSlide },
            };
            var legacy = new List<string>();
            foreach (string name in ReadAll(presentation).Keys)
                if (name.StartsWith(LegacyPrefix, StringComparison.Ordinal))
                    legacy.Add(name);
            Delete(presentation, legacy);
            Write(presentation, values);
        }

        // ---- custom document properties ------------------------------------------------

        private static string Str(Dictionary<string, object> props, string key, bool raw = false)
        {
            return props.TryGetValue(raw ? key : Prefix + key, out object v) && v != null
                ? Convert.ToString(v, CultureInfo.InvariantCulture)
                : null;
        }

        private static int Int(Dictionary<string, object> props, string key, int fallback, bool raw = false)
        {
            if (!props.TryGetValue(raw ? key : Prefix + key, out object v) || v == null)
                return fallback;
            try { return Convert.ToInt32(v, CultureInfo.InvariantCulture); }
            catch (FormatException) { return fallback; }
            catch (InvalidCastException) { return fallback; }
            catch (OverflowException) { return fallback; }
        }

        /// <summary>All AiSignX_ and PPVU_ properties (AiSignX_ keys keep their prefix).</summary>
        private static Dictionary<string, object> ReadAll(PowerPoint.Presentation presentation)
        {
            var result = new Dictionary<string, object>(StringComparer.Ordinal);
            ForEachProperty(presentation, (name, prop) =>
            {
                if (name.StartsWith(Prefix, StringComparison.Ordinal) || name.StartsWith(LegacyPrefix, StringComparison.Ordinal))
                {
                    try { result[name] = prop.Value; }
                    catch (COMException) { } // a property with an unreadable value: ignore it
                }
                return false;
            });
            return result;
        }

        private static void Write(PowerPoint.Presentation presentation, Dictionary<string, object> values)
        {
            var pending = new Dictionary<string, object>(values, StringComparer.Ordinal);
            ForEachProperty(presentation, (name, prop) =>
            {
                if (!pending.TryGetValue(name, out object value))
                    return false;
                if (prop.Type == TypeFor(value))
                {
                    prop.Value = value;
                    pending.Remove(name);
                }
                return false; // a property of the wrong type is deleted and added again below
            });
            if (pending.Count == 0)
                return;
            Delete(presentation, pending.Keys);
            var props = (Office.DocumentProperties)presentation.CustomDocumentProperties;
            try
            {
                foreach (KeyValuePair<string, object> entry in pending)
                {
                    Office.DocumentProperty added = props.Add(entry.Key, false, TypeFor(entry.Value), entry.Value);
                    Release(added);
                }
            }
            finally { Release(props); }
        }

        private static void Delete(PowerPoint.Presentation presentation, IEnumerable<string> names)
        {
            var doomed = new HashSet<string>(names, StringComparer.Ordinal);
            if (doomed.Count == 0)
                return;
            // Deleting while enumerating skips items, so restart after each delete.
            bool deleted = true;
            while (deleted)
            {
                deleted = false;
                ForEachProperty(presentation, (name, prop) =>
                {
                    if (!doomed.Remove(name))
                        return false;
                    prop.Delete();
                    deleted = true;
                    return true;
                });
            }
        }

        /// <summary>Visits each custom property until <paramref name="visit"/> returns true,
        /// releasing every COM object it touched.</summary>
        private static void ForEachProperty(PowerPoint.Presentation presentation,
                                            Func<string, Office.DocumentProperty, bool> visit)
        {
            var props = (Office.DocumentProperties)presentation.CustomDocumentProperties;
            try
            {
                int count = props.Count;
                for (int i = 1; i <= count; i++)
                {
                    Office.DocumentProperty prop = props[i];
                    try
                    {
                        if (visit(prop.Name, prop))
                            return;
                    }
                    finally { Release(prop); }
                }
            }
            finally { Release(props); }
        }

        private static Office.MsoDocProperties TypeFor(object value)
        {
            return value is int ? Office.MsoDocProperties.msoPropertyTypeNumber : Office.MsoDocProperties.msoPropertyTypeString;
        }

        private static void Release(object com)
        {
            if (com != null && Marshal.IsComObject(com))
                Marshal.ReleaseComObject(com);
        }
    }
}
