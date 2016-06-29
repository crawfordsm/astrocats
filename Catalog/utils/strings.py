"""
"""

__all__ = ['rep_chars', 'single_spaces', 'get_event_filename']


def rep_chars(string, chars, rep=''):
    for c in chars:
        if c in string:
            string = string.replace(c, rep)
    return string


def single_spaces(string):
    return ' '.join(list(filter(None, string.split())))


def get_event_filename(name):
    return name.replace('/', '_')