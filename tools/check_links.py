#!/usr/bin/env python3
"""Check every markdown link in the repo, and check that the one generated
figure is still the figure its source generates.

    uv run tools/check_links.py

Why this exists as a script rather than as care
-----------------------------------------------
On 2026-08-01 the README was split into three files. A heading anchor that
survives the move looks identical to one that does not: GitHub renders a dead
`](#some-heading)` as ordinary blue text that silently goes nowhere. There were
151 of them, and the numbering hazard below makes a hand-check unreliable even
in principle.

On 2026-08-11 it was split again -- README, architecture.md, building.md,
history.md -- with this script in place, which is the only reason that one was
routine. rtl/README.md joined MD_FILES at the same time, so a link into the
Efinity flow is checked like everything else, and slides/README.md joined when
the deck was added.

**The deck itself is not checked.** slides/index.html links into docs/ by
relative path and loads docs/img/wire.svg, and nothing here reads HTML -- so
moving that figure breaks a slide silently. Grep it by hand when docs/img/
changes.

**GitHub numbers duplicate headings per file.** `### What the board did` appears
several times, so the anchors are `#what-the-board-did`, `-1`, `-2`. Move two of
them into different files and each becomes `-0` in its own file: every `-1` link
now points at nothing while still *looking* right. That is the failure this
script exists to catch, so it computes anchors the way GitHub does rather than
the way that reads better:

  lowercase -> delete [^\\w\\s-] -> replace each space with '-' (per character,
  NOT collapsed) -> append -1, -2 ... on repeats, per file.

There is no trim anywhere in that pipeline, and it matters: `## ⚠️ Pad number ≠
silkscreen number` in docs/pinmap.md loses the emoji, keeps the space that
followed it, and so its anchor is `#-pad-number--silkscreen-number` with a
*leading* dash. A slugger that trims declares that working link broken.

The `--check-figures` half is the same argument applied to `docs/img/wire.svg`:
a committed artifact that nothing regenerates is a claim nobody is checking.
"""
import os
import re
import subprocess
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MD_FILES = ['README.md', 'docs/architecture.md', 'docs/building.md',
            'docs/history.md', 'docs/milestones.md', 'docs/bring-up-log.md',
            'docs/pinmap.md', 'docs/fit.md', 'rtl/README.md',
            'slides/README.md', 'bench/README.md', 'bench/stills/README.md',
            'bench/stills/20260822-synth-book-crop2/README.md']


def slug(heading):
    s = re.sub(r'[^\w\s-]', '', heading.rstrip('\r\n').lower())
    return s.replace(' ', '-')


def anchors(text):
    """Every anchor GitHub would emit for this file, in order, deduped its way.
    Headings inside fenced code blocks do not count -- two of this repo's fences
    open with a `# ` comment line and would otherwise be read as H1s."""
    out, seen, fence = set(), {}, False
    for line in text.split('\n'):
        if re.match(r'^\s*```', line):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r'^(#{1,6})[ \t]+(.*)$', line)
        if not m:
            continue
        base = slug(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f'{base}-{n}')
    # explicit <a name="..."> / id="..." anchors, if any ever get added
    out |= set(re.findall(r'<a\s+(?:name|id)="([^"]+)"', text))
    return out


def links(text):
    """(target, line_no) for every inline link, skipping fenced code."""
    out, fence = [], False
    for i, line in enumerate(text.split('\n'), 1):
        if re.match(r'^\s*```', line):
            fence = not fence
            continue
        if fence:
            continue
        for m in re.finditer(r'\]\(([^)\s]+)(?:\s+"[^"]*")?\)', line):
            out.append((m.group(1), i))
    return out


def check_links():
    texts = {}
    for f in MD_FILES:
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            with open(p) as fh:
                texts[f] = fh.read()

    anc = {f: anchors(t) for f, t in texts.items()}
    bad, n = [], 0

    for f, text in texts.items():
        here = os.path.dirname(f)
        for target, line in links(text):
            if target.startswith(('http://', 'https://', 'mailto:')):
                continue
            n += 1
            path, _, frag = target.partition('#')
            frag = urllib.parse.unquote(frag)

            if not path:                       # same-file anchor
                dest = f
            else:
                dest = os.path.normpath(os.path.join(here, path))
                if not os.path.exists(os.path.join(ROOT, dest)):
                    bad.append(f'{f}:{line}  missing file: {target}')
                    continue

            if frag:
                if dest not in anc:
                    bad.append(f'{f}:{line}  anchor into unparsed file: {target}')
                elif frag not in anc[dest]:
                    bad.append(f'{f}:{line}  dead anchor: {target}')
    return n, bad


def check_figures():
    """Re-render docs/img/wire.svg from its source and demand it match."""
    src = os.path.join(ROOT, 'docs/diagrams/wire.json')
    svg = os.path.join(ROOT, 'docs/img/wire.svg')
    if not os.path.exists(src):
        return ['docs/diagrams/wire.json is missing']
    if not os.path.exists(svg):
        return ['docs/img/wire.svg is missing; run `make -C docs`']
    tmp = '/tmp/wire.check.svg'
    r = subprocess.run(['npx', '--yes', 'wavedrom-cli', '-i', src, '-s', tmp],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        return [f'wavedrom-cli failed (is node installed?): {r.stderr.strip()[:200]}']
    with open(tmp) as a, open(svg) as b:
        if a.read() != b.read():
            return ['docs/img/wire.svg is stale -- run `make -C docs` and commit it']
    return []


def main():
    n, bad = check_links()
    print(f'links checked: {n}')
    print('broken links:', 'none' if not bad else '\n  ' + '\n  '.join(bad))

    figs = []
    if '--check-figures' in sys.argv:
        figs = check_figures()
        print('figures:', 'up to date' if not figs else '\n  ' + '\n  '.join(figs))
    else:
        print('figures: skipped (pass --check-figures; it shells out to npx)')

    if n < 100:
        # A checker that finds nothing to check passes trivially. This repo has
        # ~150 internal links; if that collapses, the parser broke, not the docs.
        print(f'!! only {n} links found -- the parser is probably broken')
        return 1
    return 1 if (bad or figs) else 0


if __name__ == '__main__':
    sys.exit(main())
