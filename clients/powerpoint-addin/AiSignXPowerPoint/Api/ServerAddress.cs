using System;
using System.Net;
using System.Net.Sockets;

namespace AiSignXPowerPoint.Api
{
    /// <summary>
    /// Parses what the user typed as the server URL into a base address that ends in "/".
    /// A path is kept (AiSignX behind a proxy at /signage/ works), but API paths that people
    /// paste by mistake ("/api", "/api/v1/media", ...) are removed.
    /// </summary>
    public static class ServerAddress
    {
        private static readonly string[] StrippedSuffixes =
        {
            "/api/v1/media", "/api/media", "/api/v1", "/api", "/media", "/login", "/admin",
        };

        /// <exception cref="FormatException">With a message for the user.</exception>
        public static Uri Parse(string raw)
        {
            string s = (raw ?? "").Trim();
            if (s.Length == 0)
                throw new FormatException("Enter the server URL, for example https://signage.example.com.");

            if (s.IndexOf("://", StringComparison.Ordinal) < 0)
                s = (LooksLocal(s) ? "http://" : "https://") + s;

            if (!Uri.TryCreate(s, UriKind.Absolute, out Uri uri)
                || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
                || string.IsNullOrEmpty(uri.Host))
            {
                throw new FormatException("The server URL is not valid. Use the form "
                                          + "http://192.168.1.10:5000 or https://signage.example.com.");
            }
            if (!string.IsNullOrEmpty(uri.UserInfo))
                throw new FormatException("Do not put a user name or password in the server URL.");

            string path = uri.AbsolutePath.TrimEnd('/');
            bool stripped = true;
            while (stripped)
            {
                stripped = false;
                foreach (string suffix in StrippedSuffixes)
                {
                    if (path.EndsWith(suffix, StringComparison.OrdinalIgnoreCase))
                    {
                        path = path.Substring(0, path.Length - suffix.Length).TrimEnd('/');
                        stripped = true;
                    }
                }
            }
            var builder = new UriBuilder(uri.Scheme, uri.Host, uri.Port, path + "/");
            return builder.Uri;
        }

        /// <summary>The text to show and store for a parsed address (no trailing "/").</summary>
        public static string Display(Uri baseUri) => baseUri.GetLeftPart(UriPartial.Path).TrimEnd('/');

        /// <summary>A stable key for per-server settings (the token and pin).</summary>
        public static string Key(Uri baseUri) => Display(baseUri).ToLowerInvariant();

        /// <summary>True when an http:// address sends the token across a network unencrypted.</summary>
        public static bool IsPlainHttpOverNetwork(Uri baseUri)
        {
            return baseUri.Scheme == Uri.UriSchemeHttp && !IsLoopback(baseUri.Host);
        }

        private static bool IsLoopback(string host)
        {
            if (string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase))
                return true;
            return IPAddress.TryParse(host.Trim('[', ']'), out IPAddress ip) && IPAddress.IsLoopback(ip);
        }

        /// <summary>No scheme typed: localhost and private IP addresses get http://, names get
        /// https://.</summary>
        private static bool LooksLocal(string s)
        {
            string host = s;
            int slash = host.IndexOf('/');
            if (slash >= 0) host = host.Substring(0, slash);
            if (host.StartsWith("[", StringComparison.Ordinal))
            {
                int end = host.IndexOf(']');
                host = end > 0 ? host.Substring(1, end - 1) : host;
            }
            else
            {
                int colon = host.LastIndexOf(':');
                if (colon >= 0) host = host.Substring(0, colon);
            }
            if (IsLoopback(host))
                return true;
            if (!IPAddress.TryParse(host, out IPAddress ip))
                return false;
            if (ip.AddressFamily == AddressFamily.InterNetworkV6)
                return ip.IsIPv6LinkLocal || ip.IsIPv6SiteLocal;
            byte[] b = ip.GetAddressBytes();
            return b[0] == 10 || (b[0] == 172 && b[1] >= 16 && b[1] <= 31)
                   || (b[0] == 192 && b[1] == 168) || (b[0] == 169 && b[1] == 254);
        }
    }
}
