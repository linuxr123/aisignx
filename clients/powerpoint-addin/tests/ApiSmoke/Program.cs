using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using AiSignXPowerPoint.Api;

// Usage: ApiSmoke <serverUrl> <writeToken> [readOnlyToken]
// Tokens come from the web admin (user menu > API tokens). Uploads a 1x1 PNG, replaces it, checks
// the error mapping, and leaves one item named "ApiSmoke" behind (delete it in Media).
internal static class Program
{
    private static int _failed;

    private static void Check(string what, bool ok)
    {
        Console.WriteLine((ok ? "PASS " : "FAIL ") + what);
        if (!ok) _failed++;
    }

    private static async Task<ApiException> Fails(Func<Task> action)
    {
        try { await action(); }
        catch (ApiException ex) { return ex; }
        return null;
    }

    private static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.WriteLine("Usage: ApiSmoke <serverUrl> <writeToken> [readOnlyToken]");
            return 2;
        }
        try { Run(args).GetAwaiter().GetResult(); }
        catch (Exception ex) { Console.WriteLine("ERROR " + ex); return 1; }
        Console.WriteLine(_failed == 0 ? "ALL PASSED" : _failed + " FAILED");
        return _failed == 0 ? 0 : 1;
    }

    private static async Task Run(string[] args)
    {
        Uri baseUri = ServerAddress.Parse(args[0]);
        string png = Path.Combine(Path.GetTempPath(), "apismoke-" + Guid.NewGuid().ToString("N") + ".png");
        File.WriteAllBytes(png, Convert.FromBase64String(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="));
        var cancel = CancellationToken.None;
        try
        {
            using (var client = new MediaApiClient(baseUri, args[1]))
            {
                MediaPage list = await client.ListMediaAsync(1, 5, cancel);
                Check("list media", list.Page == 1);

                MediaItem made = await client.UploadAsync(png, "ApiSmoke", 7, null, cancel);
                Check("upload returns id and name", made.Id > 0 && made.Name == "ApiSmoke");
                MediaItem got = await client.GetMediaAsync(made.Id, cancel);
                Check("get uploaded item", got.Id == made.Id && got.Type == "image");

                MediaItem again = await client.ReplaceAsync(made.Id, png, null, null, null, cancel);
                Check("replace keeps id and name", again.Id == made.Id && again.Name == "ApiSmoke");

                ApiException nf = await Fails(() => client.ReplaceAsync(999999, png, null, null, null, cancel));
                Check("replace unknown id -> 404 not_found", nf != null && nf.Status == 404 && nf.Code == "not_found");

                string txt = png + ".txt";
                File.WriteAllText(txt, "x");
                ApiException ut = await Fails(() => client.UploadAsync(txt, "bad", null, null, cancel));
                Check("unsupported extension -> 415", ut != null && ut.Status == 415 && ut.Code == "unsupported_type");
                File.Delete(txt);
            }
            using (var bad = new MediaApiClient(baseUri, "asx_" + new string('x', 43)))
            {
                ApiException e = await Fails(() => bad.ListMediaAsync(1, 1, cancel));
                Check("bad token -> 401 invalid_token", e != null && e.Status == 401 && e.Code == "invalid_token");
            }
            if (args.Length > 2)
            {
                using (var ro = new MediaApiClient(baseUri, args[2]))
                {
                    await ro.ListMediaAsync(1, 1, cancel);
                    ApiException e = await Fails(() => ro.UploadAsync(png, "x", null, null, cancel));
                    Check("read-only token upload -> 403 insufficient_scope", e != null && e.Status == 403 && e.Code == "insufficient_scope");
                }
            }
        }
        finally { try { File.Delete(png); } catch (IOException) { } }
    }
}
