import os
from PIL import Image
import subprocess
from playwright.sync_api import sync_playwright

def create_image_thumbnail(image_path, thumb_path, size=(300, 300)):
    with Image.open(image_path) as img:
        img.thumbnail(size)
        img.save(thumb_path, "PNG")

def create_video_thumbnail(video_path, thumb_path, time_offset=1):
    # ffmpeg must be installed
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-ss", str(time_offset), "-vframes", "1", thumb_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def create_webpage_thumbnail(url, output_path, width=1280, height=720):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-setuid-sandbox',
            ]
        )
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": width, "height": height})
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                # If navigation times out or errors, still attempt a screenshot
                pass
            # Short wait for JS/CSS to finish rendering
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
            page.screenshot(path=output_path, full_page=False)
        finally:
            browser.close()