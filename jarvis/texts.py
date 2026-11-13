"""The words Jarvis is given: prompt templates in jarvis/texts/*.md, filled in for the person it works for.

    {{name}}                   replaced by a value
    [[if name]] ... [[end]]    kept only when the value is true (blocks may sit inside blocks)

A template that names a value nobody gave is an error, never a prompt with a hole in it.
"""
import re
from . import paths

_IF = re.compile(r'\[\[if (\w+)\]\]((?:(?!\[\[if )[\s\S])*?)\[\[end\]\]')
_VAR = re.compile(r'\{\{(\w+)\}\}')


def render_text(text, name='text', **values):
    while True:                                      # innermost blocks first, until none is left
        new = _IF.sub(lambda m: m.group(2) if values.get(m.group(1)) else '', text)
        if new == text:
            break
        text = new
    if '[[if ' in text or '[[end]]' in text:
        raise ValueError(f'{name}: an [[if]] block is not closed')

    def var(m):
        if m.group(1) not in values:
            raise KeyError(f'{name}: no value for {m.group(1)}')
        return str(values[m.group(1)])
    return _VAR.sub(var, text)


def render(name, **values):
    """The template jarvis/texts/<name>.md with its values filled in."""
    return render_text(paths.repo('jarvis', 'texts', name + '.md').read_text(encoding='utf-8'), name, **values)
