"""Explicit quantities for editable fields; no expression evaluation."""
import math
import re

LENGTH = {'mm':1., 'cm':10., 'm':1000., 'in':25.4, 'inch':25.4, 'inches':25.4,
          '"':25.4, 'ft':304.8}


def quantity(text, kind='length', display='mm'):
    match = re.fullmatch(r'\s*([-+]?(?:\d+(?:\s+\d+/\d+|/\d+)|(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?))\s*(.*?)\s*', str(text))
    if not match:
        raise ValueError('Enter a number with optional units, for example 3.175 mm or 1/8 in.')
    number, unit = match.groups()
    if '/' in number:
        sign = -1 if number.startswith('-') else 1
        parts = number.lstrip('+-').split()
        num, den = parts[-1].split('/')
        if float(den) == 0:
            raise ValueError('A fraction cannot have a zero denominator.')
        value = sign*((float(parts[0]) if len(parts)==2 else 0)+float(num)/float(den))
    else:
        value = float(number)
    unit = unit.lower().replace(' ', '')
    if kind == 'length':
        factor = LENGTH.get(unit or display)
    elif kind == 'feed':
        if not unit:
            unit = display+'/min'
        split = unit.split('/')
        factor = LENGTH.get(split[0]) if len(split)==2 else None
        if factor is not None:
            factor *= {'min':1., 's':60., 'sec':60.}.get(split[1], math.nan)
    else:
        allowed = {'angle': ('','deg','°'), 'rpm': ('','rpm'), 'scalar': ('',)}[kind]
        factor = 1. if unit in allowed else None
    if factor is None or not math.isfinite(value*factor):
        raise ValueError('Unsupported units or nonfinite value: '+str(text))
    return value*factor


def formatted(value, kind='length', display='mm'):
    if kind in ('length','feed'):
        factor = LENGTH[display]
        unit = display+('/min' if kind=='feed' else '')
    else:
        factor = 1
        unit = {'angle':'deg','rpm':'rpm','scalar':''}[kind]
    return f'{value/factor:.15g} {unit}'.strip()

