using System;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;

namespace AiSignXPowerPoint.Api
{
    /// <summary>
    /// HTTPS trust: Windows' certificate store by default, or one pinned certificate.
    ///
    /// Without a pin, a certificate is accepted only when Windows trusts it (a public CA or a
    /// private CA installed on this PC). With a pin, exactly the certificate whose SHA-256
    /// fingerprint matches is accepted, whether or not Windows trusts it, and every other
    /// certificate is refused. That makes a self-signed server certificate usable without
    /// switching checks off. A pin must be updated when the server's certificate is renewed.
    /// </summary>
    public static class CertificatePin
    {
        /// <summary>The pin as 64 upper-case hex digits, or null for "no pin". Colons,
        /// spaces and dashes are ignored, so "AB:CD:..." from openssl works.</summary>
        /// <exception cref="FormatException">With a message for the user.</exception>
        public static string Normalize(string pin)
        {
            if (string.IsNullOrWhiteSpace(pin))
                return null;
            var sb = new StringBuilder(64);
            foreach (char c in pin.Trim())
            {
                if (c == ':' || c == ' ' || c == '-')
                    continue;
                if (!Uri.IsHexDigit(c))
                    throw new FormatException("The certificate fingerprint may contain only hex digits (0-9, A-F) and colons.");
                sb.Append(char.ToUpperInvariant(c));
            }
            if (sb.Length != 64)
                throw new FormatException("A SHA-256 certificate fingerprint has 64 hex digits; this one has " + sb.Length + ".");
            return sb.ToString();
        }

        /// <summary>SHA-256 over the DER certificate, 64 upper-case hex digits.</summary>
        public static string Sha256(X509Certificate certificate)
        {
            if (certificate == null)
                throw new ArgumentNullException(nameof(certificate));
            using (var sha = SHA256.Create())
            {
                byte[] hash = sha.ComputeHash(certificate.GetRawCertData());
                var sb = new StringBuilder(hash.Length * 2);
                foreach (byte b in hash)
                    sb.Append(b.ToString("X2"));
                return sb.ToString();
            }
        }

        /// <summary>"AB:CD:..." for display.</summary>
        public static string Format(string normalized)
        {
            if (string.IsNullOrEmpty(normalized))
                return "";
            var sb = new StringBuilder(normalized.Length * 3 / 2);
            for (int i = 0; i < normalized.Length; i += 2)
            {
                if (i > 0) sb.Append(':');
                sb.Append(normalized, i, Math.Min(2, normalized.Length - i));
            }
            return sb.ToString();
        }

        /// <summary>The trust decision for one TLS handshake.</summary>
        /// <param name="normalizedPin">From <see cref="Normalize"/>; null means "Windows decides".</param>
        public static bool IsTrusted(X509Certificate certificate, SslPolicyErrors errors, string normalizedPin)
        {
            if (normalizedPin == null)
                return errors == SslPolicyErrors.None;
            if (certificate == null)
                return false;
            return string.Equals(Sha256(certificate), normalizedPin, StringComparison.Ordinal);
        }
    }
}
