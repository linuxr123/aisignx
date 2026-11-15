using System;
using System.Drawing;
using System.IO;
using System.Reflection;

namespace AiSignXPowerPoint
{
    /// <summary>The add-in's icons, embedded in the DLL (Resources\*.ico and *.png; tools\make_icons.py
    /// draws them). They are loaded on demand and a failure just means "no icon", never an error.</summary>
    internal static class Icons
    {
        private static Stream Open(string file) =>
            Assembly.GetExecutingAssembly().GetManifestResourceStream("AiSignXPowerPoint.Resources." + file);

        /// <summary>The icon for the add-in's windows (title bar).</summary>
        public static Icon App()
        {
            try
            {
                using (Stream s = Open("AiSignX.ico"))
                    return s == null ? null : new Icon(s);
            }
            catch (Exception ex) when (ex is ArgumentException || ex is IOException)
            {
                return null;
            }
        }

        /// <summary>The ribbon button's picture: 32 px, or 64 px on a high-DPI screen.</summary>
        public static Image Ribbon()
        {
            try
            {
                float scale;
                using (var g = Graphics.FromHwnd(IntPtr.Zero))
                    scale = g.DpiX / 96f;
                using (Stream s = Open(scale >= 1.5f ? "ribbon-upload-64.png" : "ribbon-upload-32.png"))
                {
                    if (s == null) return null;
                    using (var raw = new Bitmap(s))
                        return new Bitmap(raw); // a copy: the stream can be released
                }
            }
            catch (Exception ex) when (ex is ArgumentException || ex is IOException)
            {
                return null;
            }
        }
    }
}
