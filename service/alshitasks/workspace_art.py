"""Stable workspace appearance contract; mirrored in the standalone desktop service."""
import hashlib
import re

ICON_IDS = ('home', 'layers', 'cube', 'rocket', 'globe', 'code', 'leaf', 'spark',
            'compass', 'briefcase', 'terminal', 'network', 'book', 'calendar',
            'camera', 'chart', 'cloud', 'flag', 'heart', 'music', 'palette',
            'puzzle', 'shield', 'star')
COLORS = ('#2563eb', '#0891b2', '#0d9488', '#16a34a', '#65a30d', '#ca8a04',
          '#ea580c', '#dc2626', '#db2777', '#9333ea', '#7c3aed', '#64748b')


def defaults(identity):
    digest = hashlib.sha256(str(identity).lower().encode('utf-8')).digest()
    return {'icon': ICON_IDS[int.from_bytes(digest[:4], 'big') % len(ICON_IDS)],
            'icon_color': COLORS[int.from_bytes(digest[4:8], 'big') % len(COLORS)]}


def color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('Choose a color in #RRGGBB format.')
    return value.lower()


def appearance(identity, icon=None, icon_color=None):
    value = defaults(identity)
    if icon in ICON_IDS:
        value['icon'] = icon
    try:
        value['icon_color'] = color(icon_color)
    except ValueError:
        pass
    return value
