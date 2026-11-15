using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Web.Script.Serialization;

namespace AiSignXPowerPoint.Api
{
    /// <summary>A media item as the API returns it (docs/api.md, "The media item").</summary>
    public sealed class MediaItem
    {
        public int Id { get; set; }
        public string Name { get; set; }
        /// <summary>"image", "video", "web" or "plugin".</summary>
        public string Type { get; set; }
        public string Mime { get; set; }
        public long? Size { get; set; }
        public int? Width { get; set; }
        public int? Height { get; set; }
        /// <summary>Seconds the item plays when a playlist item does not set its own.</summary>
        public double? Duration { get; set; }
        public string ChecksumSha256 { get; set; }
        public string CreatedAt { get; set; }

        /// <summary>Only uploaded files (images and videos) can be replaced.</summary>
        public bool IsReplaceable => Type == "image" || Type == "video";

        public static MediaItem Parse(string json)
        {
            var obj = Deserialize(json) as Dictionary<string, object>;
            if (obj == null)
                throw new FormatException("The server's answer is not a media item.");
            return FromDictionary(obj);
        }

        internal static object Deserialize(string json)
        {
            try
            {
                return new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 }.DeserializeObject(json ?? "");
            }
            catch (ArgumentException ex)
            {
                throw new FormatException("The server's answer is not valid JSON.", ex);
            }
            catch (InvalidOperationException ex)
            {
                throw new FormatException("The server's answer is not valid JSON.", ex);
            }
        }

        internal static MediaItem FromDictionary(Dictionary<string, object> d)
        {
            int? id = ToInt(Get(d, "id"));
            if (!id.HasValue)
                throw new FormatException("The server's answer has no media id.");
            return new MediaItem
            {
                Id = id.Value,
                Name = Get(d, "name") as string,
                Type = Get(d, "type") as string,
                Mime = Get(d, "mime") as string,
                Size = ToLong(Get(d, "size")),
                Width = ToInt(Get(d, "width")),
                Height = ToInt(Get(d, "height")),
                Duration = ToDouble(Get(d, "duration")),
                ChecksumSha256 = Get(d, "checksum_sha256") as string,
                CreatedAt = Get(d, "created_at") as string,
            };
        }

        private static object Get(Dictionary<string, object> d, string key)
        {
            object v;
            return d.TryGetValue(key, out v) ? v : null;
        }

        private static int? ToInt(object v)
        {
            long? l = ToLong(v);
            return l.HasValue && l.Value >= int.MinValue && l.Value <= int.MaxValue ? (int?)l.Value : null;
        }

        private static long? ToLong(object v)
        {
            if (v is int i) return i;
            if (v is long l) return l;
            if (v is decimal m) return (long)m;
            if (v is double dd) return (long)dd;
            return null;
        }

        private static double? ToDouble(object v)
        {
            if (v == null || v is string || v is bool) return null;
            try { return Convert.ToDouble(v, CultureInfo.InvariantCulture); }
            catch (InvalidCastException) { return null; }
        }

        public override string ToString() => "#" + Id + " " + Name;
    }

    /// <summary>One page of GET /api/v1/media.</summary>
    public sealed class MediaPage
    {
        public IList<MediaItem> Items { get; } = new List<MediaItem>();
        public int Page { get; set; }
        public int PerPage { get; set; }
        public int Total { get; set; }

        public bool HasMore => Page * PerPage < Total;

        public static MediaPage Parse(string json)
        {
            var obj = MediaItem.Deserialize(json) as Dictionary<string, object>;
            if (obj == null || !(obj.TryGetValue("items", out object raw) && raw is IEnumerable))
                throw new FormatException("The server's answer is not a media list.");
            var page = new MediaPage();
            foreach (object entry in (IEnumerable)raw)
            {
                if (entry is Dictionary<string, object> d)
                    page.Items.Add(MediaItem.FromDictionary(d));
            }
            page.Page = AsInt(obj, "page", 1);
            page.PerPage = AsInt(obj, "per_page", page.Items.Count);
            page.Total = AsInt(obj, "total", page.Items.Count);
            return page;
        }

        private static int AsInt(Dictionary<string, object> d, string key, int fallback)
        {
            return d.TryGetValue(key, out object v) && v is int i ? i : fallback;
        }
    }
}
