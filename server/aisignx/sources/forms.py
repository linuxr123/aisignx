from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, PasswordField, SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, NumberRange, Optional

KIND_LABELS = {
    "folder": "A folder on the server (or a network share it can read)",
    "http": "A web folder (an HTTP directory listing)",
    "webdav": "WebDAV (Nextcloud, ownCloud, a NAS)",
    "gdrive": "Google Drive (a shared folder)",
    "onedrive": "OneDrive (a shared folder link)",
    "dropbox": "Dropbox (a shared folder link)",
    "flickr": "Flickr (a public photo stream or album)",
}

# What the address box and the password box mean for each kind (shown beside the form and in the page's script).
HELP = {
    "folder": ("Folder", r"C:\Photos\Sign   or   \\nas\photos   or   /mnt/photos", False, "The server itself reads this folder, so it must be a path the AiSignX service account can read."),
    "http": ("Address of the folder", "http://nas.local/photos/", True, "A page that lists the files (nginx or Apache 'index' pages, many NAS web servers). A user name and password are optional."),
    "webdav": ("WebDAV address", "https://cloud.example.com/remote.php/dav/files/me/Photos/", True, "For a Nextcloud or ownCloud share link (…/s/abc123) paste the link; leave the user empty (add the share's password below if it has one)."),
    "gdrive": ("Folder link or id", "https://drive.google.com/drive/folders/1AbC…", True, "Share the folder with 'anyone with the link', then give a free Google API key below (Drive API turned on). Put Google Photos albums in such a folder."),
    "onedrive": ("Share link", "https://1drv.ms/f/s!…", False, "Share the folder as 'anyone with the link can view' and paste that link."),
    "dropbox": ("Shared-folder link", "https://www.dropbox.com/scl/fo/…", False, "Share the folder with a link that anyone can open. Dropbox sends the whole folder as one download, so keep it modest in size."),
    "flickr": ("User id or album link", "12345678@N00   or   https://www.flickr.com/photos/12345678@N00/albums/72177…", False, "Public photos only. Flickr's open feed shows the latest 20 photos."),
}


class SourceForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)], render_kw={"placeholder": "Family photos"})
    kind = SelectField("Kind", choices=[SelectChoice(k, v) for k, v in KIND_LABELS.items()])
    location = StringField("Address", validators=[DataRequired(), Length(max=1000)])
    username = StringField("User name", validators=[Optional(), Length(max=120)], render_kw={"autocomplete": "off"})
    secret = PasswordField("Password or API key", validators=[Optional(), Length(max=300)], render_kw={"autocomplete": "new-password"})
    recursive = BooleanField("Include sub-folders", default=True)
    mirror = BooleanField("Remove images from the library when they leave the source", default=True)
    enabled = BooleanField("Keep this source up to date", default=True)
    interval_min = IntegerField("Check every (minutes)", validators=[NumberRange(5, 1440)], default=60)
    max_images = IntegerField("At most this many images (the newest)", validators=[NumberRange(1, 1000)], default=200)
