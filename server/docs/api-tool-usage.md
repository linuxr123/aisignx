# Server API Tools: Updating One or Multiple Media Files

This guide shows admins and automations how to use the Server API Tools to update existing media items (single or batch), including file replacement, replacement by URL, and metadata updates.

Works with:
- Authenticated admin sessions (via browser)
- Bearer tokens you create on the API Tools page (recommended for automation/CI)

Base URL used below: https://your-server.example.com
Replace with your server’s actual URL.

---

## 1) Access the API Tools and Create a Token

1. Log in as an admin and open:
   - Settings → API Tools (or go directly to /settings/api)
2. Create a new token:
   - Give it a name (e.g., “CI updater”, “daily-sync”)
   - Select scopes:
     - media:read (read media and list)
     - media:write (upload/replace/delete/update)
     - playlist:* (optional if you script playlists)
3. Click Create Token. Copy the token immediately; it will not be shown again.
4. You can revoke tokens at any time on this page.

Notes:
- The API Tools page lists your recent media and provides ready-to-copy IDs, URLs, and cURL templates.
- The Authorization header for all API calls:
  Authorization: Bearer YOUR_TOKEN

---

## 2) Find the Media IDs You Want to Update

Options:
- In the web UI: Media page shows the numeric ID per item.
- On the API Tools page: copy the ID or API URL next to each media row.
- Via API:
  - List and filter:
    GET /api/media?type=image
    GET /api/media?name=promo
    GET /api/media?external_id=SOME_KEY

Example:
```bash
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/media?type=image"
```

Tip: Use external_id to target records with a stable key in automations. You can set external_id when adding media from URL.

---

## 3) Replace a Single Media File (Upload a New File)

Endpoint:
- POST /api/media/{id}/replace
- Scope required: media:write

Linux/macOS (bash):
```bash
BASE="https://your-server.example.com"
TOKEN="YOUR_TOKEN"
MEDIA_ID=123
curl -X POST "$BASE/api/media/$MEDIA_ID/replace" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/new-file.jpg"
```

Windows PowerShell:
```powershell
$BASE = "https://your-server.example.com"
$TOKEN = "YOUR_TOKEN"
$MEDIA_ID = 123
curl -Method POST "$BASE/api/media/$MEDIA_ID/replace" `
  -Headers @{ Authorization = "Bearer $TOKEN" } `
  -Form @{ file = Get-Item "C:\path\to\new-file.jpg" }
```

Rules:
- You can only replace images with images and videos with videos.
- Supported images: .jpg .jpeg .png .gif
- Supported videos: .mp4 .webm .ogg

---

## 4) Replace a Single Media File by Source URL (Server Downloads It)

Endpoint:
- POST /api/media/{id}/replace_by_url
- Scope required: media:write

```bash
BASE="https://your-server.example.com"
TOKEN="YOUR_TOKEN"
MEDIA_ID=123
curl -X POST "$BASE/api/media/$MEDIA_ID/replace_by_url" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_url": "https://example.com/new-file.jpg"}'
```

Notes:
- The server downloads the file directly from source_url.
- Content type/extension must match the original media type (image vs video).

---

## 5) Update Media Metadata (Name, Description, Duration, Webpage URL)

Endpoint:
- PUT /api/media/{id}
- Scope required: media:write

Examples:
- Image/video metadata:
```bash
curl -X PUT "$BASE/api/media/123" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"New Name","description":"Updated desc","duration":15}'
```

- Webpage media: update URL and options
```bash
curl -X PUT "$BASE/api/media/456" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "url": "https://news.example.com",
        "duration": 45,
        "refresh_interval": 60,
        "scrolling": true,
        "description": "Homepage"
      }'
```

---

## 6) Batch Replacements (Multiple Media)

A) Bash: replace many by mapping IDs to files
```bash
BASE="https://your-server.example.com"
TOKEN="YOUR_TOKEN"

# csv: id,path
cat updates.csv | while IFS=, read -r ID PATH; do
  [ -z "$ID" ] && continue
  echo "Replacing $ID with $PATH"
  curl -s -S -X POST "$BASE/api/media/$ID/replace" \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@${PATH}" \
    || echo "Failed: $ID"
done
```

B) PowerShell: replace many by mapping IDs to files
```powershell
$BASE  = "https://your-server.example.com"
$TOKEN = "YOUR_TOKEN"

Import-Csv -Path ".\updates.csv" | ForEach-Object {
  $id = $_.id
  $path = $_.path
  Write-Host "Replacing $id with $path"
  curl -Method POST "$BASE/api/media/$id/replace" `
       -Headers @{ Authorization = "Bearer $TOKEN" } `
       -Form @{ file = Get-Item $path }
}
```

C) Batch replace by URL
```bash
BASE="https://your-server.example.com"
TOKEN="YOUR_TOKEN"

# csv: id,source_url
cat url_updates.csv | while IFS=, read -r ID SRC; do
  [ -z "$ID" ] && continue
  echo "Replacing $ID with $SRC"
  curl -s -S -X POST "$BASE/api/media/$ID/replace_by_url" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"source_url\":\"$SRC\"}" \
    || echo "Failed: $ID"
done
```

D) Target by external_id (stable keys)
1. Find the record by external_id:
   GET /api/media?external_id=SOME_KEY
2. Use the returned id to call /replace or /replace_by_url.

Example (bash):
```bash
BASE="https://your-server.example.com"
TOKEN="YOUR_TOKEN"
KEY="DAILY_BANNER"

ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/media?external_id=$KEY" | jq -r '.media[0].id')

if [ "$ID" != "null" ] && [ -n "$ID" ]; then
  curl -X POST "$BASE/api/media/$ID/replace" \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@/path/to/new/banner.jpg"
else
  echo "No media with external_id=$KEY"
fi
```

Tip: When creating media from URL you can set external_id once:
```bash
curl -X POST "$BASE/api/media/from_url" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_url":"https://example.com/banner.jpg","name":"Banner","external_id":"DAILY_BANNER"}'
```

---

## 7) Add New Media (If You Need to Add Before Updating)

- File upload:
  POST /api/media  (multipart form)
  Scope: media:write

```bash
curl -X POST "$BASE/api/media" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/file.jpg" \
  -F "name=My Slide" \
  -F "duration=10" \
  -F "description=Welcome slide"
```

- From URL:
  POST /api/media/from_url
  Scope: media:write

```bash
curl -X POST "$BASE/api/media/from_url" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_url":"https://example.com/file.mp4","name":"Promo","duration":20,"external_id":"PROMO_2025"}'
```

---

## 8) Authentication Summary

- All API calls require either:
  - Logged-in admin session (browser), or
  - Authorization: Bearer YOUR_TOKEN (recommended for scripts)
- Scopes:
  - media:read → list/get media
  - media:write → upload/replace/delete/update

---

## 9) Troubleshooting

- 401 Unauthorized:
  - Missing/invalid token, or no admin session.
  - Ensure header is exactly: Authorization: Bearer YOUR_TOKEN
- 403 Forbidden:
  - Token exists but lacks scope (e.g., using media:read for a write).
- 404 Not Found:
  - Media ID doesn’t exist; verify ID with GET /api/media or the Media page.
- “Unsupported file type”:
  - Images: .jpg .jpeg .png .gif; Videos: .mp4 .webm .ogg only.
- Thumbnail generation failed:
  - The update still succeeds; the server logs an error and keeps old thumbnail if possible.
- Windows curl tips:
  - In PowerShell, -F "file=@..." works as shown with Get-Item. If you use Git Bash on Windows, use the bash example.

---

## 10) Quick API Reference (for updates)

- GET /api/media
  - Filters: ?type=image|video|webpage, ?name=foo, ?external_id=KEY
  - Scope: media:read
- GET /api/media/{id}
  - Scope: media:read
- POST /api/media/{id}/replace
  - Scope: media:write
- POST /api/media/{id}/replace_by_url
  - Scope: media:write
- PUT /api/media/{id}
  - Scope: media:write
- DELETE /api/media/{id}
  - Scope: media:write

Tip: You can copy ready-made cURL examples from the API Tools page next to each media item.

---

# C# examples: Replace file(s), replace by URL, update metadata

Requirements
- .NET 6+ (or .NET Core 3.1+)
- Namespaces:
  using System.Net.Http;
  using System.Net.Http.Headers;
  using System.Text;
  using System.Text.Json;

Optional: If your dev server uses a self-signed certificate, see the “Dev certificate” note at the end.

## Helper class (recommended)

```csharp
using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

public static class MediaApi
{
    public static HttpClient CreateClient(string baseUrl, string token, bool ignoreTlsErrorsInDev = false)
    {
        HttpClient client;
        if (ignoreTlsErrorsInDev)
        {
            var handler = new HttpClientHandler
            {
                ServerCertificateCustomValidationCallback = HttpClientHandler.DangerousAcceptAnyServerCertificateValidator
            };
            client = new HttpClient(handler, disposeHandler: true);
        }
        else
        {
            client = new HttpClient();
        }

        client.BaseAddress = new Uri(baseUrl.TrimEnd('/'));
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client;
    }

    public static async Task ReplaceFileAsync(HttpClient client, int mediaId, string filePath, string? contentType = null)
    {
        if (!File.Exists(filePath)) throw new FileNotFoundException(filePath);
        var fileName = Path.GetFileName(filePath);
        contentType ??= GuessContentType(fileName);

        using var fs = File.OpenRead(filePath);
        using var fileContent = new StreamContent(fs);
        fileContent.Headers.ContentType = new MediaTypeHeaderValue(contentType);

        using var form = new MultipartFormDataContent();
        form.Add(fileContent, "file", fileName);

        using var resp = await client.PostAsync($"/api/media/{mediaId}/replace", form);
        var body = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode)
            throw new InvalidOperationException($"Replace failed ({resp.StatusCode}): {body}");
    }

    public static async Task ReplaceByUrlAsync(HttpClient client, int mediaId, string sourceUrl)
    {
        var payload = JsonSerializer.Serialize(new { source_url = sourceUrl });
        using var content = new StringContent(payload, Encoding.UTF8, "application/json");
        using var resp = await client.PostAsync($"/api/media/{mediaId}/replace_by_url", content);
        var body = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode)
            throw new InvalidOperationException($"Replace by URL failed ({resp.StatusCode}): {body}");
    }

    public static async Task UpdateMetadataAsync(HttpClient client, int mediaId, object metadata)
    {
        var payload = JsonSerializer.Serialize(metadata);
        using var content = new StringContent(payload, Encoding.UTF8, "application/json");
        using var resp = await client.PutAsync($"/api/media/{mediaId}", content);
        var body = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode)
            throw new InvalidOperationException($"Update failed ({resp.StatusCode}): {body}");
    }

    public static async Task<int?> FindMediaIdByExternalIdAsync(HttpClient client, string externalId)
    {
        using var resp = await client.GetAsync($"/api/media?external_id={Uri.EscapeDataString(externalId)}");
        var body = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode)
            throw new InvalidOperationException($"Lookup failed ({resp.StatusCode}): {body}");

        using var doc = JsonDocument.Parse(body);
        if (!doc.RootElement.TryGetProperty("media", out var mediaArr) || mediaArr.GetArrayLength() == 0)
            return null;

        var id = mediaArr[0].GetProperty("id").GetInt32();
        return id;
    }

    private static string GuessContentType(string fileName)
    {
        var ext = Path.GetExtension(fileName).ToLowerInvariant();
        return ext switch
        {
            ".jpg" or ".jpeg" => "image/jpeg",
            ".png" => "image/png",
            ".gif" => "image/gif",
            ".mp4" => "video/mp4",
            ".webm" => "video/webm",
            ".ogg" => "video/ogg",
            _ => "application/octet-stream"
        };
    }
}
```

## Replace a single file (local path)

```csharp
using System;
using System.Net.Http;
using System.Threading.Tasks;

class Program
{
    static async Task Main()
    {
        string baseUrl = "https://your-server.example.com";
        string token = "YOUR_TOKEN";
        int mediaId = 123;
        string filePath = @"C:\assets\new-slide.jpg"; // or "/home/user/new-slide.jpg"

        using var client = MediaApi.CreateClient(baseUrl, token /*, ignoreTlsErrorsInDev: true */);
        await MediaApi.ReplaceFileAsync(client, mediaId, filePath);

        Console.WriteLine("Replace complete.");
    }
}
```

## Replace by URL

```csharp
using System;
using System.Net.Http;
using System.Threading.Tasks;

class Program
{
    static async Task Main()
    {
        string baseUrl = "https://your-server.example.com";
        string token = "YOUR_TOKEN";
        int mediaId = 123;
        string sourceUrl = "https://example.com/new-file.jpg";

        using var client = MediaApi.CreateClient(baseUrl, token);
        await MediaApi.ReplaceByUrlAsync(client, mediaId, sourceUrl);

        Console.WriteLine("Replace-by-URL complete.");
    }
}
```

## Update metadata (name, description, duration; or webpage fields)

```csharp
using System;
using System.Net.Http;
using System.Threading.Tasks;

class Program
{
    static async Task Main()
    {
        string baseUrl = "https://your-server.example.com";
        string token = "YOUR_TOKEN";
        int mediaId = 123;

        using var client = MediaApi.CreateClient(baseUrl, token);

        // Example: image/video metadata
        await MediaApi.UpdateMetadataAsync(client, mediaId, new
        {
            name = "New Name",
            description = "Updated desc",
            duration = 15
        });

        // Example: for webpage media (different mediaId)
        int webpageId = 456;
        await MediaApi.UpdateMetadataAsync(client, webpageId, new
        {
            url = "https://news.example.com",
            duration = 45,
            refresh_interval = 60,
            scrolling = true,
            description = "Homepage"
        });

        Console.WriteLine("Metadata updated.");
    }
}
```

## Batch: replace many by mapping IDs to files

```csharp
using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Threading.Tasks;

class Program
{
    static async Task Main()
    {
        string baseUrl = "https://your-server.example.com";
        string token = "YOUR_TOKEN";

        // id -> local file path
        var updates = new List<(int id, string path)>
        {
            (101, @"C:\assets\slide1.jpg"),
            (102, @"C:\assets\slide2.jpg"),
            (205, @"C:\assets\promo.mp4"),
        };

        using var client = MediaApi.CreateClient(baseUrl, token);

        foreach (var (id, path) in updates)
        {
            try
            {
                Console.WriteLine($"Replacing {id} with {path}...");
                await MediaApi.ReplaceFileAsync(client, id, path);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Failed {id}: {ex.Message}");
            }
        }

        Console.WriteLine("Batch replace complete.");
    }
}
```

## Batch: target by external_id (stable keys)

```csharp
using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Threading.Tasks;

class Program
{
    static async Task Main()
    {
        string baseUrl = "https://your-server.example.com";
        string token = "YOUR_TOKEN";

        var updates = new List<(string externalId, string path)>
        {
            ("DAILY_BANNER", @"C:\assets\banner.jpg"),
            ("WEEKLY_PROMO", @"C:\assets\promo.mp4"),
        };

        using var client = MediaApi.CreateClient(baseUrl, token);

        foreach (var (externalId, path) in updates)
        {
            try
            {
                var id = await MediaApi.FindMediaIdByExternalIdAsync(client, externalId);
                if (id is null)
                {
                    Console.WriteLine($"No media with external_id={externalId}");
                    continue;
                }

                Console.WriteLine($"Replacing {externalId} (id={id}) with {path}...");
                await MediaApi.ReplaceFileAsync(client, id.Value, path);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"Failed {externalId}: {ex.Message}");
            }
        }
    }
}
```

## Dev certificate note (optional)
If your server uses a self-signed TLS cert in development, you can allow it temporarily:
```csharp
using var client = MediaApi.CreateClient(baseUrl, token, ignoreTlsErrorsInDev: true);
```
Only use this in non-production environments.

---