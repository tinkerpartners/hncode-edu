"""Locally generated identicon avatars.

Replaces the `//www.gravatar.com/avatar/<md5>?d=identicon` URLs that every
avatar used to point at. Those are the only images a page pulls from a
third-party host, so they are also the only thing that breaks visibly when the
site is served on a network that allows nothing else -- which is how contests
are run.

The image is a deterministic function of the digest alone, in the same spirit
as Gravatar's identicon: a five-by-five grid mirrored down the middle, one
colour on a light ground. Same digest, same picture, forever -- so it is safe
to cache immutably and there is nothing to store.
"""

from django.http import Http404, HttpResponse
from django.views.decorators.cache import cache_control

# Gravatar's identicons are 5x5 with the right half mirroring the left, which
# is what makes them read as a face rather than as noise.
GRID = 5
HALF = (GRID + 1) // 2
CELL = 16
PAD = 4

BACKGROUND = "#f0f0f0"


def _colour(digest):
    """A mid-tone colour from the tail of the digest.

    Clamped away from both extremes so the result stays legible on the light
    ground and never comes out near-white or near-black.
    """
    r, g, b = (int(digest[i : i + 2], 16) for i in (26, 28, 30))
    return "#{:02x}{:02x}{:02x}".format(
        60 + r * 140 // 255, 60 + g * 140 // 255, 60 + b * 140 // 255
    )


def identicon_svg(digest):
    """Render the identicon for a 32-character hex digest."""
    colour = _colour(digest)
    size = GRID * CELL + PAD * 2

    cells = []
    for column in range(HALF):
        for row in range(GRID):
            # One nibble per cell of the left half; even means filled.
            if int(digest[column * GRID + row], 16) % 2:
                continue
            for x in {column, GRID - 1 - column}:
                cells.append(
                    '<rect x="{}" y="{}" width="{}" height="{}"/>'.format(
                        PAD + x * CELL, PAD + row * CELL, CELL, CELL
                    )
                )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        'viewBox="0 0 {size} {size}" role="img" aria-label="avatar">'
        '<rect width="{size}" height="{size}" fill="{bg}"/>'
        '<g fill="{fg}">{cells}</g>'
        "</svg>"
    ).format(size=size, bg=BACKGROUND, fg=colour, cells="".join(cells))


@cache_control(public=True, max_age=31536000, immutable=True)
def identicon(request, digest):
    """Serve the identicon for a digest.

    The digest is already a hash of an email address, so nothing here exposes
    an address that the page did not already carry. It is validated rather than
    trusted: the URL pattern constrains it, and this re-checks so the view is
    safe if it is ever routed differently.
    """
    digest = digest.lower()
    if len(digest) != 32 or any(c not in "0123456789abcdef" for c in digest):
        raise Http404("not a digest")

    response = HttpResponse(identicon_svg(digest), content_type="image/svg+xml")
    response["Vary"] = "Accept-Encoding"
    return response

