using System;
using System.Collections.Generic;
using System.Globalization;
using System.Web.Script.Serialization;

namespace AiSignXPowerPoint.Api
{
    /// <summary>
    /// Turns an error answer into an <see cref="ApiException"/> with a message a person can act
    /// on. Decisions use the HTTP status and the "error" code, never the server's "message"
    /// text (see docs/api.md); the message is shown as extra detail.
    /// </summary>
    public static class ApiErrors
    {
        private const string TokenHelp =
            "Create a token in AiSignX under your user menu > API tokens.";

        public static ApiException FromResponse(int status, string body, TimeSpan? retryAfter,
                                                string reasonPhrase = null)
        {
            string code, message;
            TryParseError(body, out code, out message);
            string text = Describe(status, code, message, reasonPhrase);
            if (retryAfter.HasValue && (status == 429 || status == 503))
                text += " Try again in " + FormatWait(retryAfter.Value) + ".";
            return new ApiException(status, code, message, retryAfter, text);
        }

        /// <summary>Reads {"error": "...", "message": "..."}; false when the body is not that
        /// shape (an HTML page from a proxy, an empty body).</summary>
        public static bool TryParseError(string body, out string code, out string message)
        {
            code = null;
            message = null;
            if (string.IsNullOrWhiteSpace(body) || body.TrimStart()[0] != '{')
                return false;
            try
            {
                var obj = new JavaScriptSerializer().DeserializeObject(body) as Dictionary<string, object>;
                if (obj == null)
                    return false;
                object c, m;
                if (obj.TryGetValue("error", out c)) code = c as string;
                if (obj.TryGetValue("message", out m)) message = m as string;
                return !string.IsNullOrEmpty(code);
            }
            catch (ArgumentException) { return false; }
            catch (InvalidOperationException) { return false; }
        }

        private static string Describe(int status, string code, string message, string reason)
        {
            string detail = string.IsNullOrWhiteSpace(message) ? "" : " (" + message.Trim() + ")";
            switch (code)
            {
                case "invalid_token":
                    return "The server did not accept the API token: it is wrong, revoked or "
                           + "expired, or its user is disabled. " + TokenHelp;
                case "insufficient_scope":
                    return "The API token lacks a permission" + detail + ". Uploading needs "
                           + "media:write; choosing an item from the list also needs media:read. "
                           + TokenHelp;
                case "forbidden":
                    return "Your AiSignX account may no longer do this" + detail
                           + ". Uploading needs the editor or admin role.";
                case "not_found":
                    return "Not found" + detail + ". Check the media ID under Media in AiSignX.";
                case "not_replaceable":
                    return "That item is a web page or plugin, not an uploaded file, so it "
                           + "cannot be replaced. Choose a video or image item.";
                case "too_large":
                    return "The video is larger than the server accepts" + detail + ". Export "
                           + "at a lower resolution or quality, or ask the admin to raise "
                           + "AISIGNX_MAX_UPLOAD_MB.";
                case "unsupported_type":
                case "invalid_media":
                case "missing_file":
                case "invalid_duration":
                    return "The server refused the file" + detail + ".";
                case "rate_limited":
                    return "Too many failed token checks came from this computer's address, so "
                           + "the server is refusing API calls for a while.";
                case "maintenance":
                case "busy":
                case "setup_required":
                    return "The server cannot take uploads right now" + detail + ".";
                case "server_error":
                    return "The server hit an internal error. Try again; if it keeps happening, "
                           + "the admin can check the server log.";
            }
            if (code != null)
                return "The server refused the request: " + code + detail + ".";

            // Not an AiSignX API answer: a proxy, a wrong URL or another web server.
            switch (status)
            {
                case 404:
                    return "No AiSignX API answered at this address (HTTP 404). Check the server "
                           + "URL: enter only the base address, for example "
                           + "https://signage.example.com, without /api.";
                case 413:
                    return "The video is larger than the server or a proxy in front of it "
                           + "accepts (HTTP 413). Export at a lower resolution or quality, or "
                           + "raise the proxy's upload limit (nginx: client_max_body_size).";
                case 401:
                case 403:
                    return "Access was denied (HTTP " + status + ") by something that is not "
                           + "AiSignX, probably a proxy. Check the server URL.";
                case 429:
                    return "The server or a proxy is limiting requests (HTTP 429).";
                case 502:
                case 503:
                case 504:
                    return "The server, or a proxy in front of it, is unavailable (HTTP "
                           + status + ").";
            }
            string phrase = string.IsNullOrWhiteSpace(reason) ? "" : " " + reason.Trim();
            return "The server answered HTTP " + status + phrase + ".";
        }

        public static string FormatWait(TimeSpan wait)
        {
            int seconds = Math.Max(1, (int)Math.Ceiling(wait.TotalSeconds));
            if (seconds < 90)
                return seconds.ToString(CultureInfo.InvariantCulture)
                       + (seconds == 1 ? " second" : " seconds");
            int minutes = (int)Math.Ceiling(seconds / 60.0);
            return minutes.ToString(CultureInfo.InvariantCulture) + " minutes";
        }
    }
}
