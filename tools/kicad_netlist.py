# /// script
# requires-python = ">=3.10"
# ///
"""Extract a symbol's pin -> net table from a KiCad 7/8 .kicad_sch file.

The Forgix schematic is published as KiCad source but no netlist, and
kicad-cli is not installed here, so connectivity has to be recovered
geometrically: wires join their two endpoints, labels name the point they sit
on, and a symbol pin belongs to whatever net its absolute position lands on.

    uv run tools/kicad_netlist.py sch/RP2354A.kicad_sch --ref U2

Pin coordinates in `lib_symbols` are Y-up; schematic coordinates are Y-down.
The transform below is validated against pins we already know from the vendor
PDF (GPIO1=FPGA.CS, GPIO12=UART0_TX, ...) - see --check.
"""

import argparse
import itertools
import sys
from pathlib import Path

# ---------------------------------------------------------------- s-expression

def parse_sexpr(text: str):
    """Return the top-level s-expression as nested lists of str."""
    out, stack, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            node = []
            stack.append(node)
            i += 1
        elif c == ")":
            node = stack.pop()
            (stack[-1] if stack else out).append(node)
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while text[j] != '"' or text[j - 1] == "\\":
                buf.append(text[j])
                j += 1
            tok = "".join(buf).replace("\\\\", "\\").replace('\\"', '"')
            (stack[-1] if stack else out).append(tok)
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "()\"":
                j += 1
            (stack[-1] if stack else out).append(text[i:j])
            i = j
    return out[0]


def kids(node, tag):
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def kid(node, tag):
    got = kids(node, tag)
    return got[0] if got else None


# ---------------------------------------------------------------- geometry

def rnd(p):
    # KiCad writes 0.01 mm precision; round so endpoints compare equal.
    return (round(p[0], 2) + 0.0, round(p[1], 2) + 0.0)


def place(px, py, sx, sy, angle, mirror):
    """Library pin (px,py) -> absolute schematic point."""
    x, y = px, -py                      # lib is Y-up, sheet is Y-down
    if angle == 90:
        x, y = y, -x
    elif angle == 180:
        x, y = -x, -y
    elif angle == 270:
        x, y = -y, x
    if mirror == "x":
        y = -y
    elif mirror == "y":
        x = -x
    return rnd((sx + x, sy + y))


class Union:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


# ---------------------------------------------------------------- sheet model

class Sheet:
    def __init__(self, path: Path):
        self.root = parse_sexpr(path.read_text())
        self.libs = self._lib_pins()
        self.u = Union()
        self.names = {}                 # point -> set of net names
        self._wires()
        self._labels()
        self._sheet_pins()
        self._power_labels()

    def _lib_pins(self):
        """(lib_id, unit) -> [(number, name, x, y)]

        Multi-unit parts (the 10k resistor packs) place each unit separately
        with its own `(unit N)`. Merging all units onto one instance puts three
        quarters of the pins at fabricated coordinates, which invents nets.
        """
        out = {}
        block = kid(self.root, "lib_symbols")
        if not block:
            return out
        for sym in kids(block, "symbol"):
            # Instances reference a local override by `lib_name` when present,
            # so key on the raw name here and resolve it the same way below.
            for unit in kids(sym, "symbol"):        # named "Name_<unit>_<style>"
                try:
                    n = int(unit[1].rsplit("_", 2)[-2])
                except (ValueError, IndexError):
                    n = 0
                bucket = out.setdefault((sym[1], n), [])
                for pin in kids(unit, "pin"):
                    at = kid(pin, "at")
                    bucket.append(
                        (kid(pin, "number")[1], kid(pin, "name")[1],
                         float(at[1]), float(at[2]))
                    )
        return out

    def _wires(self):
        for tag in ("wire", "bus"):
            for w in kids(self.root, tag):
                pts = [rnd((float(p[1]), float(p[2]))) for p in kids(kid(w, "pts"), "xy")]
                for a, b in itertools.pairwise(pts):
                    self.u.join(a, b)

    def _add_name(self, pt, name):
        self.names.setdefault(pt, set()).add(name)
        self.u.find(pt)

    def _labels(self):
        for tag in ("label", "global_label", "hierarchical_label"):
            for lab in kids(self.root, tag):
                at = kid(lab, "at")
                self._add_name(rnd((float(at[1]), float(at[2]))), lab[1])

    def _sheet_pins(self):
        for sh in kids(self.root, "sheet"):
            for pin in kids(sh, "pin"):
                at = kid(pin, "at")
                self._add_name(rnd((float(at[1]), float(at[2]))), pin[1])

    def _power_labels(self):
        """A power symbol is an implicit global label carrying its Value.

        Without this, every rail reads as an unnamed net and a 10k pull-up is
        indistinguishable from a 10k pull-down.
        """
        for _ref, value, lib_id, pins in self.symbols():
            if lib_id.startswith("power:") and not lib_id.endswith("PWR_FLAG"):
                for _num, _nam, pt in pins:
                    self._add_name(pt, value)

    def net_of(self, pt, exclude=None):
        """Net name from labels; falls back to the other pins sharing the net.

        Plenty of nets here carry no label at all (the RP<->PSRAM QSPI bus, for
        one), so without the fallback they read as unconnected.
        """
        root = self.u.find(pt)
        got = set()
        for p, names in self.names.items():
            if self.u.find(p) == root:
                got |= names
        if got:
            return " / ".join(sorted(got))
        peers = [
            f"{ref}.{num}({nam})"
            for ref, _v, _l, pins in self.symbols()
            for num, nam, p in pins
            if self.u.find(p) == root and p != pt and ref != exclude
        ]
        return "= " + ", ".join(sorted(peers)) if peers else ""

    def symbols(self):
        """(ref, value, lib_id, [(number, name, point)])"""
        out = []
        for sym in kids(self.root, "symbol"):
            lib_id = kid(sym, "lib_id")
            if not lib_id:
                continue
            at = kid(sym, "at")
            sx, sy, angle = float(at[1]), float(at[2]), int(float(at[3]))
            mir = kid(sym, "mirror")
            mirror = mir[1] if mir else None
            props = {p[1]: p[2] for p in kids(sym, "property")}
            unit = kid(sym, "unit")
            u = int(unit[1]) if unit else 1
            lib_name = kid(sym, "lib_name")
            key = lib_name[1] if lib_name else lib_id[1]
            defs = self.libs.get((key, u), []) + self.libs.get((key, 0), [])
            pins = [
                (num, nam, place(px, py, sx, sy, angle, mirror))
                for num, nam, px, py in defs
            ]
            ref = props.get("Reference", "?")
            if self.libs.get((key, 2)) or u > 1:
                ref = f"{ref}.{u}"
            out.append((ref, props.get("Value", ""), lib_id[1], pins))
        return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", type=Path)
    ap.add_argument("--ref", help="only this reference designator")
    ap.add_argument("--list", action="store_true", help="just list symbols")
    ap.add_argument("--all", action="store_true", help="include unconnected pins")
    args = ap.parse_args()

    sheet = Sheet(args.sheet)
    for ref, value, lib_id, pins in sheet.symbols():
        if args.list:
            print(f"{ref:8} {value:24} {lib_id}  ({len(pins)} pins)")
            continue
        if args.ref and ref != args.ref:
            continue
        print(f"== {ref}  {value}  [{lib_id}]")
        for num, nam, pt in sorted(pins, key=lambda p: (len(p[0]), p[0])):
            net = sheet.net_of(pt)
            if net or args.all:
                print(f"  pin {num:>4}  {nam:<22} -> {net or '(unconnected)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
