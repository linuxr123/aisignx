Place your app icons here before building:

  icon.ico  — Windows icon  (256x256 minimum, ICO format)
  icon.png  — Linux icon    (512x512 PNG)

You can convert a single PNG to ICO with:
  npm install -g png2icons
  png2icons icon.png icon --ico

Or use any online converter. The icon is referenced in package.json under
build.win.icon and build.linux.icon.

If no icon is provided, electron-builder will use its default Electron icon.
