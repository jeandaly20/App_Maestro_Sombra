"""{% asset 'js/students.js' %}: the static URL plus a version that changes when the file changes,
so browsers download the new CSS or JavaScript instead of reusing an old cached copy."""
import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path):
    found = finders.find(path)
    if not found:
        return static(path)
    return f'{static(path)}?v={int(os.path.getmtime(found))}'
