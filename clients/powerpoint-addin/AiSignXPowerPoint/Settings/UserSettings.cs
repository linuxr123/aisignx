using System;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32;

namespace AiSignXPowerPoint.Settings
{
    /// <summary>
    /// Per-user settings in HKEY_CURRENT_USER\Software\AiSignX\PowerPointAddIn.
    ///
    /// Each server (keyed by its base URL) has its own API token and optional certificate pin,
    /// so a token is only ever sent to the server it was saved for. Tokens are encrypted with
    /// Windows DPAPI for the current user: only this Windows account on this PC can read them.
    /// </summary>
    public sealed class UserSettings
    {
        public const string DefaultRoot = @"Software\AiSignX\PowerPointAddIn";
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("AiSignX.PowerPoint.token.v1");

        private readonly string _root;

        /// <param name="root">Registry path under HKCU; tests pass their own.</param>
        public UserSettings(string root = DefaultRoot)
        {
            _root = root;
        }

        /// <summary>The server used last, offered for presentations that have none yet.</summary>
        public string LastServerUrl
        {
            get { return ReadString(_root, "LastServerUrl") ?? ""; }
            set { WriteValue(_root, "LastServerUrl", value ?? "", RegistryValueKind.String); }
        }

        public string GetToken(string serverKey)
        {
            byte[] sealedBytes = ReadValue(ServerPath(serverKey), "Token") as byte[];
            if (sealedBytes == null || sealedBytes.Length == 0)
                return "";
            try
            {
                byte[] plain = ProtectedData.Unprotect(sealedBytes, Entropy, DataProtectionScope.CurrentUser);
                return Encoding.UTF8.GetString(plain);
            }
            catch (CryptographicException)
            {
                return ""; // saved by another Windows account or PC: ask again
            }
        }

        public void SaveToken(string serverKey, string token)
        {
            if (string.IsNullOrWhiteSpace(token))
            {
                ForgetToken(serverKey);
                return;
            }
            byte[] sealedBytes = ProtectedData.Protect(Encoding.UTF8.GetBytes(token.Trim()), Entropy,
                                                       DataProtectionScope.CurrentUser);
            WriteValue(ServerPath(serverKey), "Server", serverKey, RegistryValueKind.String);
            WriteValue(ServerPath(serverKey), "Token", sealedBytes, RegistryValueKind.Binary);
        }

        public void ForgetToken(string serverKey)
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(ServerPath(serverKey), writable: true))
                key?.DeleteValue("Token", throwOnMissingValue: false);
        }

        /// <summary>The pinned SHA-256 certificate fingerprint for a server, or "".</summary>
        public string GetPin(string serverKey) => ReadString(ServerPath(serverKey), "CertificateSha256") ?? "";

        public void SavePin(string serverKey, string normalizedPin)
        {
            if (string.IsNullOrEmpty(normalizedPin))
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(ServerPath(serverKey), writable: true))
                    key?.DeleteValue("CertificateSha256", throwOnMissingValue: false);
                return;
            }
            WriteValue(ServerPath(serverKey), "Server", serverKey, RegistryValueKind.String);
            WriteValue(ServerPath(serverKey), "CertificateSha256", normalizedPin, RegistryValueKind.String);
        }

        /// <summary>Removes everything under the root (used by tests).</summary>
        public void DeleteAll() => Registry.CurrentUser.DeleteSubKeyTree(_root, throwOnMissingSubKey: false);

        /// <summary>Server keys become registry key names via a hash, so any URL is safe.</summary>
        private string ServerPath(string serverKey)
        {
            if (string.IsNullOrEmpty(serverKey))
                throw new ArgumentException("A server is required.", nameof(serverKey));
            using (var sha = SHA256.Create())
            {
                byte[] hash = sha.ComputeHash(Encoding.UTF8.GetBytes(serverKey));
                var sb = new StringBuilder(32);
                for (int i = 0; i < 16; i++)
                    sb.Append(hash[i].ToString("x2"));
                return _root + @"\Servers\" + sb;
            }
        }

        private static object ReadValue(string path, string name)
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(path))
                return key?.GetValue(name);
        }

        private static string ReadString(string path, string name)
        {
            string value = ReadValue(path, name) as string;
            return string.IsNullOrEmpty(value) ? null : value;
        }

        private static void WriteValue(string path, string name, object value, RegistryValueKind kind)
        {
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(path))
                key.SetValue(name, value, kind);
        }
    }
}
