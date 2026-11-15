"""Clock plugin (frame, no external data).

Everything happens on the screen: static/player/plugins/clock/ draws the time from the
player's server-calibrated clock (ctx.now()) in the item's time zone, so it keeps working
offline. The time zone field is resolved to the organization time zone by the framework
when it is left empty. The word clock is English only.
"""
