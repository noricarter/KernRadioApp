"""Approved on-air mascot choices; image files stay in the public frontend."""

MASCOTS = ('standard', 'cute', 'headphones', 'mischief', 'surprise', 'sad',
           'consider', 'explain', 'excited')


def validate_mascot(value):
    if not isinstance(value, str) or value not in MASCOTS:
        raise ValueError('Choose an available host mascot: ' + ', '.join(MASCOTS) + '.')
    return value
