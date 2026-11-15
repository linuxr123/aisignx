"""Echo native test plugin: manifest_extra sets the URL and muted, auto_duration is 2x."""


def manifest_extra(media, config, ctx):
    return {"url": "https://www.youtube-nocookie.com/embed/abcdefghijk?autoplay=1",
            "muted": config["mute"], "words": config["words"]}


def auto_duration(config):
    return config["seconds"] * 2
