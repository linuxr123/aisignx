# AiSignX for PowerPoint

A PowerPoint add-in (VSTO, C#) that exports the open presentation as an MP4 video and uploads it
to your AiSignX server, either as a new media item or as a replacement for an existing one. It
talks to the media API ([docs/api.md](../../docs/api.md)) with a personal API token.

## Requirements

- Windows 10 or 11 with desktop PowerPoint 2016 or newer (Microsoft 365 included), 32 or 64 bit.
  PowerPoint for the web, Mac and the Microsoft Store app do not run VSTO add-ins.
- .NET Framework 4.8 (part of current Windows) and the Visual Studio 2010 Tools for Office
  Runtime (VSTO runtime; the installer offers it, and Office usually installs it).
- An AiSignX server reachable from the PC, and an editor or admin account on it.

To build: Visual Studio 2022 with the **Office/SharePoint development** workload, which supplies
the VSTO build targets and the Office interop assemblies.

## Build

1. Open `AiSignXPowerPoint\AiSignXPowerPoint.csproj` in Visual Studio (File > Open > Project).
2. Once per developer PC, create a manifest signing certificate (PowerShell):
   `.\clients\powerpoint-addin\tools\New-DevCert.ps1`. It writes
   `AiSignXPowerPoint\signing.local.props` (gitignored). Without it the build stops with an error
   when the Office tools are installed.
3. Press **F5** to debug (starts PowerPoint with the add-in), or build from a Developer Command
   Prompt: `msbuild AiSignXPowerPoint\AiSignXPowerPoint.csproj /p:Configuration=Release`.

Without the Office workload the project still compiles the DLL (with a warning), which is enough
to catch code errors but produces no installable package.

### API smoke test (no Office needed)

`tests\ApiSmoke` compiles the add-in's `Api\*.cs` into a console app and runs it against a real
server. It needs the .NET SDK and Windows (it runs on .NET Framework 4.8):

```powershell
cd clients\powerpoint-addin\tests\ApiSmoke
dotnet run -- http://127.0.0.1:5000 <token with media:read+write> [<read-only token>]
```

It uploads and replaces a 1x1 image named "ApiSmoke" (delete it in Media afterwards) and checks
the 401, 403, 404 and 415 error handling. Use a test server, not production.

## Publish and install (ClickOnce)

1. In Visual Studio: **Build > Publish AiSignXPowerPoint**, or in the project properties open
   the **Publish** tab. Publish to a folder (the default is `publish\`) or a network share. Keep
   the default prerequisites (.NET Framework 4.8 and the VSTO runtime).
2. On each PC, run `setup.exe` or double-click the `.vsto` file from that location.
3. The manifests are signed with your certificate. A self-signed one is "from an unknown
   publisher": export it (`Export-Certificate`, the script prints the command) and import it on
   each PC under **Trusted Publishers** and **Trusted Root Certification Authorities**, or sign
   with a real code-signing certificate. Windows then installs without a warning.
4. Restart PowerPoint. An **AiSignX** tab appears on the ribbon. To remove it: Windows
   **Settings > Apps** ("AiSignX for PowerPoint"). The `.vsto` also updates itself every 7 days.

## First run

1. In AiSignX open your user menu > **API tokens** and create a token with the **media:write**
   scope (and **media:read** if you want the **Choose** list and checks before the export).
   Copy it; it is shown only once.
2. In PowerPoint click **AiSignX > Export and upload**.
3. Enter the **Server URL**, the base address only, for example `http://192.168.1.10:5000` or
   `https://signage.example.com`, and paste the token. Click **Test** to check the connection.
   The token is stored encrypted for your Windows account (DPAPI), per server.

## Use

1. Open and save your presentation, then click **Export and upload**.
2. **New item:** set a name and a fallback duration, then **Export and upload**. The item
   appears in **Media**; add it to a playlist.
3. **Replace:** enter the media ID (shown on each card in Media) or click **Choose**. The item keeps
   its id and its playlists, and screens show the new file within seconds. After a new upload
   the add-in offers to remember the ID for next time (stored in the presentation).
4. Video options (resolution, frames per second, quality, use of recorded timings, seconds per
   slide) are kept in the presentation. Export can take minutes; **Cancel** stops waiting and
   the unfinished file is removed later. PowerPoint cannot stop a running export.

## Troubleshooting

| Problem | What to do |
|---|---|
| No AiSignX tab | File > Options > Add-ins > Manage COM Add-ins > Go; if the add-in is listed as disabled, enable it. Check Disabled Items for crashes. |
| "Open a presentation first" | The window is in Protected View; click Enable Editing. Exports need an editable presentation. |
| Certificate not trusted | A self-signed or private-CA certificate. Install the CA on this PC (best), or compare the fingerprint shown with the server's (`openssl x509 -noout -fingerprint -sha256 -in cert.pem`) and choose **Yes** to pin it. A renewed certificate must be pinned again. |
| 401 | Token wrong, revoked or expired, or its user is disabled. Make a new token. Many 401s lock the PC's address out for a while (429). |
| 403 | The token lacks `media:write`, or the account is now a viewer. |
| 413 | The video exceeds the server's limit (`AISIGNX_MAX_UPLOAD_MB`, default 2048) or a proxy's (nginx `client_max_body_size`). Export at 720p or lower quality. |
| 415 or "refused the file" | Only mp4 videos are produced; check the server log if it persists (for example a missing ffmpeg is fine, a corrupt video is not). |
| 404 on replace | No such media ID, or the item is a web page, which cannot be replaced. |
| Upload cuts off | Large videos over a weak link. Try a faster network or a smaller export. Uploads have no time limit; Cancel stops them. |

## Security notes

- The token is encrypted with Windows DPAPI in `HKCU\Software\AiSignX\PowerPointAddIn`; it is
  never written into the presentation. Use **Forget saved token** to delete it. Give the token
  only `media:write`, a name like "PowerPoint, my laptop" and an expiry, and revoke it in
  AiSignX if the PC is lost.
- A token is sent only to the server it was saved for. Plain `http://` to a non-local address
  sends it unencrypted; the add-in warns, so use HTTPS ([docs/https.md](../../docs/https.md)).
- Certificate checks are never turned off: either Windows trusts the certificate, or it matches
  the SHA-256 fingerprint you pinned.
- Exported videos are written to `%TEMP%\AiSignX-PowerPoint` and deleted after the upload.
- Signing keys (`*.pfx`, `signing.local.props`) stay out of git.
