using System;

namespace AiSignXPowerPoint.Api
{
    /// <summary>
    /// A failed API call: an error answer from the server ({"error": code, "message": text}),
    /// a TLS certificate the add-in would not trust, or no answer at all.
    /// <see cref="Exception.Message"/> is a complete sentence meant for the user.
    /// </summary>
    public sealed class ApiException : Exception
    {
        public ApiException(int status, string code, string serverMessage, TimeSpan? retryAfter,
                            string userMessage, Exception inner = null)
            : base(userMessage, inner)
        {
            Status = status;
            Code = code;
            ServerMessage = serverMessage;
            RetryAfter = retryAfter;
        }

        /// <summary>HTTP status, or 0 when the server never answered.</summary>
        public int Status { get; }

        /// <summary>The API's "error" code (for example "invalid_token"), or null when the
        /// answer was not an AiSignX API error (a proxy page, a network failure).</summary>
        public string Code { get; }

        /// <summary>The API's "message" text, or null.</summary>
        public string ServerMessage { get; }

        /// <summary>From the Retry-After header (429 and 503 answers).</summary>
        public TimeSpan? RetryAfter { get; }

        /// <summary>Set when the TLS certificate was rejected: the SHA-256 fingerprint of the
        /// certificate the server presented, so the user can choose to pin it.</summary>
        public string PresentedCertificateSha256 { get; internal set; }

        public bool IsCertificateRejected => PresentedCertificateSha256 != null;
    }
}
