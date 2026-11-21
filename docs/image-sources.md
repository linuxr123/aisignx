# Image sources

**Plugins > Image sources** (admin) keeps the media library filled from places that already hold your pictures. Each image is
**copied** into the library, so screens never depend on the other service being up, and the originals are never changed. A background
job checks each source every N minutes (default 60): new and changed pictures are copied, pictures that left the source are removed
(you can turn that off), and the newest *max images* (default 200, at most 1000) are kept. A big album fills in over a few minutes.
Use the **Image library** plugin item with *Images: A connected source* to show a source in a playlist; new pictures appear on screens by themselves.

| Kind | Address | Notes |
|---|---|---|
| **Folder on the server** | `C:\Photos\Sign`, `\nas\photos`, `/mnt/photos` | The server (its service account) reads it, so it must be a path that account can open. Sub-folders optional. Links that lead out of the folder are ignored. |
| **Web folder** | `http://nas.local/photos/` | A page that lists files (nginx/Apache index pages, many NAS web servers). Optional user name and password. Never leaves that folder or host. |
| **WebDAV** | `https://cloud.example.com/remote.php/dav/files/me/Photos/` | Nextcloud, ownCloud, most NASes. A Nextcloud/ownCloud **share link** (`…/s/abc123`) works as is (add the share password in the password box). |
| **Google Drive** | the folder's link or id | Share the folder with **anyone with the link**, and give a free **Google API key** (Google Cloud console, turn on the Drive API, create an API key). Put Google Photos albums in such a folder (see below). |
| **OneDrive** | the share link | Share the folder as *anyone with the link can view*. |
| **Dropbox** | the shared-folder link | Dropbox sends the whole folder as one zip, so keep it modest in size (up to about 250 MB). |
| **Flickr** | user id (`12345678@N00`) or an album link | Public photos only; Flickr's open feed gives the latest 20. |

**Google Photos:** since 2025 Google's API only lets an app see pictures that app made itself, so there is no way to read your
library or albums directly. Add the album's pictures to a Google Drive folder (or download them to a folder) and use that.

**Safety:** a source address is typed by an admin, so a LAN address (a NAS, Nextcloud at home) is allowed for folder, web folder and WebDAV
kinds; the cloud kinds must resolve to public addresses; link-local addresses (cloud metadata services) are never allowed. Images must be
real pictures (checked), at most 40 MB each. A saved password or API key is stored encrypted and never shown again.

**Where the images show:** they are ordinary library images, kept out of the main **Media** list (Media > *Image sources*, or `/media/?source=all`)
and out of the image picker, so a 500-picture album does not bury your uploads. Removing a source also removes its copies.

Tested with a real folder, a real local web server and a fake WebDAV server. The Google Drive, OneDrive, Dropbox and Flickr readers are tested against faked
answers only: they have **not** been tried against the real services (testing checklist, section P).
