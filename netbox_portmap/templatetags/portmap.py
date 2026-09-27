import hashlib
from functools import cache

from django import template
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.templatetags.static import static

register = template.Library()


@cache
def _static_hash(path):
    """Short content hash of a static file, so a new release gets a new URL.

    NetBox serves /static with a long max-age; without a version in the URL a
    browser keeps the previous plugin JS/CSS after an upgrade.
    """
    location = finders.find(path)
    if location is None:
        try:
            location = staticfiles_storage.path(path)
        except NotImplementedError:
            return ""
    try:
        with open(location, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return ""


@register.simple_tag
def static_v(path):
    url = static(path)
    digest = _static_hash(path)
    return f"{url}?v={digest}" if digest else url
