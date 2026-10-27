#!/usr/bin/env python3
"""Generate the small Lottie animations used by the interface (lib/lottie/*.json).

They are drawn from primitives (circles, rounded boxes, paths) in the Property
palette, so there is no third-party asset or licence to track. Re-run after editing:

    python3 scripts/generate-lottie.py
"""
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'lib' / 'lottie'
FPS = 30

NAVY = [0.031, 0.09, 0.275, 1]
BLUE = [0.145, 0.388, 0.922, 1]
CYAN = [0.024, 0.714, 0.831, 1]
SKY = [0.859, 0.906, 1.0, 1]
PALE = [0.929, 0.949, 1.0, 1]
WHITE = [1, 1, 1, 1]
GREEN = [0.063, 0.725, 0.506, 1]
AMBER = [0.961, 0.62, 0.043, 1]
RED = [0.937, 0.267, 0.267, 1]


# --- value helpers -----------------------------------------------------------
def val(value):
    return {'a': 0, 'k': value}


def ease(frames, easing=None):
    """Animated property. ``frames`` = [(time, value), ...]; values are lists or numbers."""
    out = []
    easing = easing or ((0.42, 0.0), (0.58, 1.0))
    for index, (time, value) in enumerate(frames):
        entry = {'t': time, 's': value if isinstance(value, list) else [value]}
        if index < len(frames) - 1:
            entry['i'] = {'x': [easing[1][0]], 'y': [easing[1][1]]}
            entry['o'] = {'x': [easing[0][0]], 'y': [easing[0][1]]}
        out.append(entry)
    return {'a': 1, 'k': out}


def transform(p=(0, 0), a=(0, 0), s=(100, 100), r=0, o=100):
    return {
        'ty': 'tr',
        'p': p if isinstance(p, dict) else val(list(p)),
        'a': a if isinstance(a, dict) else val(list(a)),
        's': s if isinstance(s, dict) else val(list(s)),
        'r': r if isinstance(r, dict) else val(r),
        'o': o if isinstance(o, dict) else val(o),
        'sk': val(0), 'sa': val(0),
    }


def fill(color, opacity=100):
    return {'ty': 'fl', 'c': val(color), 'o': val(opacity), 'r': 1}


def stroke(color, width, opacity=100, cap=2, join=2):
    return {'ty': 'st', 'c': val(color), 'o': val(opacity), 'w': width if isinstance(width, dict) else val(width), 'lc': cap, 'lj': join}


def ellipse(cx, cy, w, h=None):
    return {'ty': 'el', 'p': val([cx, cy]), 's': val([w, h if h is not None else w]), 'd': 1}


def rect(cx, cy, w, h, r=0):
    return {'ty': 'rc', 'p': val([cx, cy]), 's': val([w, h]), 'r': val(r), 'd': 1}


def path(points, closed=False, curve=None):
    """Polyline (straight segments) unless ``curve`` supplies in/out tangents."""
    zero = [[0, 0] for _ in points]
    return {'ty': 'sh', 'ks': val({'i': (curve or (zero, zero))[0], 'o': (curve or (zero, zero))[1], 'v': [list(p) for p in points], 'c': closed})}


def trim(end, start=0):
    return {'ty': 'tm', 's': start if isinstance(start, dict) else val(start), 'e': end if isinstance(end, dict) else val(end), 'o': val(0), 'm': 1}


def group(items, tr=None, name='g'):
    return {'ty': 'gr', 'it': [*items, tr or transform()], 'nm': name}


def layer(index, shapes, name, op, position=(0, 0), scale=(100, 100), rotation=0, opacity=100, anchor=(0, 0)):
    def prop(value, default):
        return value if isinstance(value, dict) else val(default if value is None else value)
    return {
        'ddd': 0, 'ind': index, 'ty': 4, 'nm': name, 'sr': 1,
        'ks': {'o': prop(opacity, 100), 'r': prop(rotation, 0),
               'p': position if isinstance(position, dict) else val([*position, 0]),
               'a': anchor if isinstance(anchor, dict) else val([*anchor, 0]),
               's': scale if isinstance(scale, dict) else val([*scale, 100])},
        'ao': 0, 'shapes': shapes, 'ip': 0, 'op': op, 'st': 0, 'bm': 0,
    }


def composition(name, op, layers, size=240):
    return {'v': '5.7.4', 'fr': FPS, 'ip': 0, 'op': op, 'w': size, 'h': size, 'nm': name, 'ddd': 0, 'assets': [], 'layers': layers}


def sparkle(cx, cy, size, color, t0, loop):
    """Four-point star that pops in and out."""
    s = size
    pts = [(0, -s), (s * 0.22, -s * 0.22), (s, 0), (s * 0.22, s * 0.22), (0, s), (-s * 0.22, s * 0.22), (-s, 0), (-s * 0.22, -s * 0.22)]
    return group([path(pts, closed=True), fill(color)],
                 transform(p=(cx, cy), s=ease([(t0, [0, 0]), (t0 + 10, [100, 100]), (t0 + 22, [0, 0]), (loop, [0, 0])]),
                           r=ease([(t0, 0), (t0 + 22, 90), (loop, 90)])), name='sparkle')


# --- animations --------------------------------------------------------------
def searching():
    loop = 96
    house = group([
        path([(66, 120), (110, 80), (154, 120)], closed=False),
        stroke(NAVY, 7),
    ], name='roof')
    body = group([rect(110, 148, 70, 54, 6), fill(WHITE), stroke(NAVY, 7)], name='body')
    door = group([rect(110, 160, 16, 30, 4), fill(SKY), stroke(NAVY, 5)], name='door')
    lens = group([ellipse(0, 0, 54), fill(WHITE, 55), stroke(BLUE, 8)], name='lens')
    handle = group([path([(19, 19), (40, 40)]), stroke(BLUE, 11)], name='handle')
    magnifier = group([lens, handle], transform(
        p=ease([(0, [92, 126]), (24, [150, 108]), (48, [128, 158]), (72, [78, 150]), (loop, [92, 126])]),
        r=ease([(0, -8), (36, 8), (72, -12), (loop, -8)])), name='magnifier')
    backdrop = group([ellipse(120, 126, 188), fill(PALE)], name='backdrop')
    return composition('searching', loop, [
        layer(1, [magnifier], 'magnifier', loop),
        layer(2, [sparkle(176, 74, 12, AMBER, 6, loop), sparkle(60, 90, 8, CYAN, 40, loop)], 'sparkles', loop),
        layer(3, [house, body, door], 'house', loop),
        layer(4, [backdrop], 'backdrop', loop),
    ])


def empty_box():
    loop = 96
    bob = ease([(0, [120, 120, 0]), (48, [120, 110, 0]), (loop, [120, 120, 0])])
    front = group([rect(120, 142, 92, 62, 8), fill(SKY), stroke(NAVY, 6)], name='front')
    label = group([rect(120, 150, 34, 8, 4), fill(WHITE)], name='label')
    left_flap = group([path([(0, 0), (-44, 0), (-44, -16), (0, -16)], closed=True), fill(PALE), stroke(NAVY, 6)],
                      transform(p=(120, 111), r=ease([(0, -4), (48, -28), (loop, -4)])), name='left flap')
    right_flap = group([path([(0, 0), (44, 0), (44, -16), (0, -16)], closed=True), fill(PALE), stroke(NAVY, 6)],
                       transform(p=(120, 111), r=ease([(0, 4), (48, 28), (loop, 4)])), name='right flap')
    dots = []
    for index, (dx, color) in enumerate([(-26, CYAN), (4, AMBER), (30, BLUE)]):
        start = index * 14
        dots.append(group([ellipse(0, 0, 9), fill(color)], transform(
            p=ease([(start, [120 + dx, 100]), (start + 44, [120 + dx, 52]), (loop, [120 + dx, 52])]),
            o=ease([(start, 0), (start + 10, 100), (start + 44, 0), (loop, 0)])), name='dot'))
    shadow = group([ellipse(120, 196, 98, 12), fill(NAVY, 12)], transform(
        s=ease([(0, [100, 100]), (48, [86, 100]), (loop, [100, 100])]), p=(0, 0), a=(0, 0)), name='shadow')
    return composition('empty-box', loop, [
        layer(1, [*dots], 'dots', loop),
        layer(2, [left_flap, right_flap, front, label], 'box', loop, position=bob, anchor=(120, 120)),
        layer(3, [shadow], 'shadow', loop),
    ])


def success():
    loop = 90
    pop = group([ellipse(0, 0, 124), fill(GREEN)], transform(
        p=(120, 120), s=ease([(0, [0, 0]), (14, [112, 112]), (22, [100, 100]), (loop, [100, 100])])), name='pop')
    ring = group([ellipse(120, 120, 150), trim(ease([(0, 0), (22, 100), (loop, 100)])), stroke(GREEN, 7, 40)], name='ring')
    check = group([path([(88, 122), (111, 145), (155, 98)]), trim(ease([(20, 0), (38, 100), (loop, 100)])), stroke(WHITE, 13)], name='check')
    burst = []
    for index in range(8):
        angle = math.radians(index * 45 + 22)
        near, far = 82, 112
        burst.append(group([ellipse(0, 0, 9), fill([AMBER, CYAN, BLUE, GREEN][index % 4])], transform(
            p=ease([(26, [120 + math.cos(angle) * near, 120 + math.sin(angle) * near]), (52, [120 + math.cos(angle) * far, 120 + math.sin(angle) * far]), (loop, [120 + math.cos(angle) * far, 120 + math.sin(angle) * far])]),
            s=ease([(26, [0, 0]), (34, [100, 100]), (52, [0, 0]), (loop, [0, 0])])), name='burst'))
    return composition('success', loop, [
        layer(1, [check], 'check', loop),
        layer(2, [pop], 'pop', loop, position=(0, 0)),
        layer(3, [ring], 'ring', loop),
        layer(4, burst, 'burst', loop, position=(0, 0)),
    ])


def match_found():
    loop = 120
    left = group([ellipse(0, 0, 92), fill(BLUE, 92)], transform(
        p=ease([(0, [38, 120]), (30, [92, 120]), (96, [92, 120]), (loop, [38, 120])]),
        s=ease([(30, [100, 100]), (40, [112, 112]), (52, [100, 100]), (loop, [100, 100])])), name='buyer')
    right = group([ellipse(0, 0, 92), fill(CYAN, 92)], transform(
        p=ease([(0, [202, 120]), (30, [148, 120]), (96, [148, 120]), (loop, [202, 120])]),
        s=ease([(30, [100, 100]), (40, [112, 112]), (52, [100, 100]), (loop, [100, 100])])), name='property')
    star_pts = []
    for index in range(10):
        radius = 22 if index % 2 == 0 else 9
        angle = math.radians(-90 + index * 36)
        star_pts.append((math.cos(angle) * radius, math.sin(angle) * radius))
    star = group([path(star_pts, closed=True), fill(WHITE)], transform(
        p=(120, 120), s=ease([(30, [0, 0]), (44, [120, 120]), (54, [100, 100]), (96, [100, 100]), (108, [0, 0]), (loop, [0, 0])]),
        r=ease([(30, -40), (54, 0), (loop, 0)])), name='star')
    pulse = group([ellipse(120, 120, 150), stroke(AMBER, 6), ], transform(
        s=ease([(32, [30, 30]), (70, [100, 100]), (loop, [100, 100])]), p=(0, 0), a=(0, 0),
        o=ease([(32, 90), (70, 0), (loop, 0)])), name='pulse')
    pulse['it'][-1]['p'] = val([0, 0])
    sparks = []
    for index in range(10):
        angle = math.radians(index * 36 + 10)
        sparks.append(group([ellipse(0, 0, 8), fill([AMBER, WHITE, RED, GREEN, BLUE][index % 5])], transform(
            p=ease([(34, [120, 120]), (68, [120 + math.cos(angle) * 100, 120 + math.sin(angle) * 100]), (loop, [120 + math.cos(angle) * 100, 120 + math.sin(angle) * 100])]),
            s=ease([(34, [0, 0]), (42, [100, 100]), (68, [0, 0]), (loop, [0, 0])])), name='spark'))
    return composition('match-found', loop, [
        layer(1, [star], 'star', loop, position=(0, 0)),
        layer(2, sparks, 'sparks', loop, position=(0, 0)),
        layer(3, [pulse], 'pulse', loop, position=(120, 120), anchor=(120, 120)),
        layer(4, [left, right], 'circles', loop, position=(0, 0)),
    ])


def upload():
    loop = 72
    cloud = group([ellipse(96, 128, 56), ellipse(132, 114, 72), ellipse(166, 130, 52), rect(130, 142, 110, 36, 18), fill(SKY)], name='cloud')
    arrow = group([
        path([(0, 32), (0, -26)]), stroke(BLUE, 12),
    ], name='shaft')
    head = group([path([(-24, -4), (0, -30), (24, -4)]), stroke(BLUE, 12)], name='head')
    moving = group([arrow, head], transform(
        p=ease([(0, [128, 172]), (36, [128, 110]), (loop, [128, 110])]),
        o=ease([(0, 0), (8, 100), (48, 100), (66, 0), (loop, 0)])), name='arrow')
    base = group([path([(78, 192), (178, 192)]), stroke(NAVY, 7, 80)], name='base')
    dots = []
    for index, x in enumerate([92, 128, 164]):
        dots.append(group([ellipse(x, 214, 8), fill(CYAN)], transform(
            s=ease([(index * 8, [60, 60]), (index * 8 + 12, [110, 110]), (index * 8 + 24, [60, 60]), (loop, [60, 60])]), p=(0, 0), a=(0, 0)), name='dot'))
        dots[-1]['it'][-1]['p'] = val([0, 0])
    return composition('upload', loop, [
        layer(1, [moving], 'arrow', loop, position=(0, 0)),
        layer(2, [cloud], 'cloud', loop, position=(0, 0)),
        layer(3, [base, *dots], 'base', loop, position=(0, 0)),
    ])


def loading():
    loop = 60
    bars = []
    for index in range(5):
        x = 68 + index * 26
        bars.append(group([rect(0, 0, 14, 44, 7), fill([BLUE, CYAN, BLUE, CYAN, BLUE][index])], transform(
            p=(x, 120), s=ease([(index * 5, [100, 100]), (index * 5 + 15, [100, 220]), (index * 5 + 30, [100, 100]), (loop, [100, 100])])), name='bar'))
    return composition('loading', loop, [layer(1, bars, 'bars', loop, position=(0, 0))])


def padlock():
    loop = 90
    # An elliptical shackle whose lower half is hidden behind the body reads as a padlock.
    shackle = group([ellipse(0, -16, 54, 74), stroke(NAVY, 12)], name='shackle')
    body = group([rect(0, 20, 92, 70, 14), fill(AMBER), stroke(NAVY, 7)], name='body')
    hole = group([ellipse(0, 14, 15), fill(NAVY)], name='hole')
    stem = group([rect(0, 30, 8, 22, 4), fill(NAVY)], name='stem')
    shake = ease([(0, 0), (6, -9), (14, 9), (22, -6), (30, 4), (38, 0), (loop, 0)])
    return composition('padlock', loop, [
        layer(1, [body, hole, stem], 'body', loop, position=(120, 116), rotation=shake),
        layer(2, [shackle], 'shackle', loop, position=(120, 116), rotation=shake),
        layer(3, [group([ellipse(120, 206, 90, 10), fill(NAVY, 12)])], 'shadow', loop),
    ])


ANIMATIONS = {
    'searching': searching, 'empty-box': empty_box, 'success': success, 'match-found': match_found,
    'upload': upload, 'loading': loading, 'padlock': padlock,
}

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in ANIMATIONS.items():
        data = build()
        (OUT / f'{name}.json').write_text(json.dumps(data, separators=(',', ':')))
        print(f'{name}.json  {len(json.dumps(data, separators=(",", ":"))) / 1024:.1f} KB')
