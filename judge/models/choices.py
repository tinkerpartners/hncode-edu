from collections import defaultdict
from operator import itemgetter
from zoneinfo import available_timezones

from django.utils.translation import gettext_lazy as _


def make_timezones():
    data = defaultdict(list)
    for tz in sorted(available_timezones()):
        if "/" in tz:
            area, loc = tz.split("/", 1)
        else:
            area, loc = "Other", tz
        if not loc.startswith("GMT"):
            data[area].append((tz, loc))
    return sorted(data.items(), key=itemgetter(0))


TIMEZONE = make_timezones()
del make_timezones

ACE_THEMES = (
    ("ambiance", "Ambiance"),
    ("chaos", "Chaos"),
    ("chrome", "Chrome"),
    ("clouds", "Clouds"),
    ("clouds_midnight", "Clouds Midnight"),
    ("cobalt", "Cobalt"),
    ("crimson_editor", "Crimson Editor"),
    ("dawn", "Dawn"),
    ("dreamweaver", "Dreamweaver"),
    ("eclipse", "Eclipse"),
    ("github", "Github"),
    ("idle_fingers", "Idle Fingers"),
    ("katzenmilch", "Katzenmilch"),
    ("kr_theme", "KR Theme"),
    ("kuroir", "Kuroir"),
    ("merbivore", "Merbivore"),
    ("merbivore_soft", "Merbivore Soft"),
    ("mono_industrial", "Mono Industrial"),
    ("monokai", "Monokai"),
    ("pastel_on_dark", "Pastel on Dark"),
    ("solarized_dark", "Solarized Dark"),
    ("solarized_light", "Solarized Light"),
    ("terminal", "Terminal"),
    ("textmate", "Textmate"),
    ("tomorrow", "Tomorrow"),
    ("tomorrow_night", "Tomorrow Night"),
    ("tomorrow_night_blue", "Tomorrow Night Blue"),
    ("tomorrow_night_bright", "Tomorrow Night Bright"),
    ("tomorrow_night_eighties", "Tomorrow Night Eighties"),
    ("twilight", "Twilight"),
    ("vibrant_ink", "Vibrant Ink"),
    ("xcode", "XCode"),
)

# Code background presets (A23). "" follows the site's light/dark theme. Each
# preset is a full, contrast-checked palette in markdown.css, so there is no
# free colour choice that could make the syntax unreadable.
CODE_THEMES = (
    ("", _("Default (follow light/dark)")),
    ("white", _("White")),
    ("gray", _("Light gray")),
    ("dark", _("Dark")),
)

# The Ace theme a preset pins the editor to, so the editor matches the
# statement and submission code blocks. No preset: the user's ace_theme.
CODE_THEME_ACE = {
    "white": "github",
    "gray": "github",
    "dark": "monokai",
}
