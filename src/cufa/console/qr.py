"""A QR encoder, in the standard library only.

Why this exists at all: the session detail screen is the page a teacher has open
mid-lesson, and the fastest way to get thirty phones onto a form is a code on
the shared screen. Reading a Google Forms URL aloud is not a plan.

Why it is written here rather than pulled in: CU inherits this repo without a
data manager, and every dependency is inherited maintenance. Byte-mode QR with a
fixed error-correction level is a few hundred lines of table lookups and finite
field arithmetic that will not change again — the spec is frozen — so the
maintenance cost of the code is lower than the maintenance cost of the package.

Scope, deliberately narrow: byte mode, error correction level M, versions 1-10.
That covers 213 bytes, and a Google Forms responder URL is about 90. Anything
longer raises ``QrTooLong`` and the console falls back to showing the link,
because a truncated QR code is worse than no QR code.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# GF(256) arithmetic for Reed-Solomon
# --------------------------------------------------------------------------

_PRIMITIVE = 0x11D  # x^8 + x^4 + x^3 + x^2 + 1, the field polynomial QR uses

_EXP: list[int] = [0] * 512
_LOG: list[int] = [0] * 256


def _build_tables() -> None:
    value = 1
    for i in range(255):
        _EXP[i] = value
        _LOG[value] = i
        value <<= 1
        if value & 0x100:
            value ^= _PRIMITIVE
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_build_tables()


def _mul(a: int, b: int) -> int:
    """Multiply in GF(256). Zero is special-cased because log(0) is undefined."""
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator_poly(degree: int) -> list[int]:
    """(x - a^0)(x - a^1)...(x - a^(degree-1)), highest coefficient first."""
    poly = [1]
    for i in range(degree):
        shifted = poly + [0]  # multiply by x
        for j, coefficient in enumerate(poly):
            shifted[j + 1] ^= _mul(coefficient, _EXP[i])
        poly = shifted
    return poly


def error_correction_codewords(data: bytes, count: int) -> bytes:
    """The Reed-Solomon remainder for one block.

    Public because it is the piece worth testing against a published vector:
    if the field arithmetic is wrong, every code this module produces is
    unreadable and looks fine to the eye.
    """
    generator = _generator_poly(count)
    remainder = [0] * count
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        for i in range(count):
            remainder[i] ^= _mul(generator[i + 1], factor)
    return bytes(remainder)


# --------------------------------------------------------------------------
# Version tables (error correction level M only)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Block:
    """How one version splits its data into Reed-Solomon blocks."""

    ec_per_block: int
    group1_blocks: int
    group1_data: int
    group2_blocks: int
    group2_data: int

    @property
    def data_codewords(self) -> int:
        return self.group1_blocks * self.group1_data + self.group2_blocks * self.group2_data


# version -> block layout at EC level M.
_BLOCKS_M: dict[int, _Block] = {
    1: _Block(10, 1, 16, 0, 0),
    2: _Block(16, 1, 28, 0, 0),
    3: _Block(26, 1, 44, 0, 0),
    4: _Block(18, 2, 32, 0, 0),
    5: _Block(24, 2, 43, 0, 0),
    6: _Block(16, 4, 27, 0, 0),
    7: _Block(18, 4, 31, 0, 0),
    8: _Block(22, 2, 38, 2, 39),
    9: _Block(22, 3, 36, 2, 37),
    10: _Block(26, 4, 43, 1, 44),
}

# Centre coordinates of the alignment patterns, per version.
_ALIGNMENT: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}

MAX_VERSION = 10
_EC_LEVEL_BITS_M = 0b00  # the two-bit code for level M in the format information


class QrTooLong(ValueError):
    """The payload does not fit the versions this encoder supports."""


def _capacity_bytes(version: int) -> int:
    """How many payload bytes fit, after the mode and length header."""
    header_bits = 4 + (8 if version <= 9 else 16)
    return (_BLOCKS_M[version].data_codewords * 8 - header_bits) // 8


def _choose_version(length: int) -> int:
    for version in range(1, MAX_VERSION + 1):
        if length <= _capacity_bytes(version):
            return version
    raise QrTooLong(
        f"{length} bytes will not fit in a version {MAX_VERSION} QR code "
        f"(limit {_capacity_bytes(MAX_VERSION)} bytes at error correction level M)."
    )


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------


class _BitBuffer:
    def __init__(self) -> None:
        self.bits: list[int] = []

    def append(self, value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def __len__(self) -> int:
        return len(self.bits)


def _encode_codewords(payload: bytes, version: int) -> bytes:
    """Mode indicator, length, data, terminator and padding -> data codewords."""
    layout = _BLOCKS_M[version]
    capacity_bits = layout.data_codewords * 8

    buffer = _BitBuffer()
    buffer.append(0b0100, 4)  # byte mode
    buffer.append(len(payload), 8 if version <= 9 else 16)
    for byte in payload:
        buffer.append(byte, 8)

    # Terminator: up to four zero bits, then pad to a byte boundary.
    buffer.append(0, min(4, capacity_bits - len(buffer)))
    while len(buffer) % 8:
        buffer.bits.append(0)

    codewords = bytearray()
    for index in range(0, len(buffer), 8):
        codewords.append(int("".join(str(bit) for bit in buffer.bits[index : index + 8]), 2))

    # The two prescribed pad bytes, alternating, until the block is full.
    for i in range(layout.data_codewords - len(codewords)):
        codewords.append(0xEC if i % 2 == 0 else 0x11)
    return bytes(codewords)


def _interleave(codewords: bytes, version: int) -> bytes:
    """Split into blocks, add error correction, and interleave both halves."""
    layout = _BLOCKS_M[version]

    blocks: list[bytes] = []
    offset = 0
    for _ in range(layout.group1_blocks):
        blocks.append(codewords[offset : offset + layout.group1_data])
        offset += layout.group1_data
    for _ in range(layout.group2_blocks):
        blocks.append(codewords[offset : offset + layout.group2_data])
        offset += layout.group2_data

    ec_blocks = [error_correction_codewords(block, layout.ec_per_block) for block in blocks]

    result = bytearray()
    for i in range(max(len(block) for block in blocks)):
        for block in blocks:
            if i < len(block):
                result.append(block[i])
    for i in range(layout.ec_per_block):
        for block in ec_blocks:
            result.append(block[i])
    return bytes(result)


# --------------------------------------------------------------------------
# Module placement
# --------------------------------------------------------------------------


class _Canvas:
    """The module grid plus a parallel map of which modules are structural.

    Two grids rather than one tri-state grid because every later step asks the
    same question — "may I write here?" — and a separate mask makes that a
    lookup instead of a rule.
    """

    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, row: int, col: int, dark: bool) -> None:
        self.modules[row][col] = dark
        self.function[row][col] = True

    def draw_function_patterns(self) -> None:
        size = self.size

        for i in range(size):
            self.set_function(6, i, i % 2 == 0)  # horizontal timing
            self.set_function(i, 6, i % 2 == 0)  # vertical timing

        self._draw_finder(3, 3)
        self._draw_finder(3, size - 4)
        self._draw_finder(size - 4, 3)

        centres = _ALIGNMENT[self.version]
        for row in centres:
            for col in centres:
                # The three finder corners already own these positions.
                if (row, col) in {(6, 6), (6, size - 7), (size - 7, 6)}:
                    continue
                self._draw_alignment(row, col)

        # Format information areas are reserved now and filled once the mask is
        # chosen, because the mask number is part of what they encode.
        self._reserve_format_areas()

        if self.version >= 7:
            self._draw_version_info()

    def _draw_finder(self, row: int, col: int) -> None:
        """A finder pattern and its separator, centred on (row, col)."""
        for dr in range(-4, 5):
            for dc in range(-4, 5):
                r, c = row + dr, col + dc
                if not (0 <= r < self.size and 0 <= c < self.size):
                    continue
                distance = max(abs(dr), abs(dc))
                self.set_function(r, c, distance != 2 and distance <= 3)

    def _draw_alignment(self, row: int, col: int) -> None:
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                self.set_function(row + dr, col + dc, max(abs(dr), abs(dc)) != 1)

    def _reserve_format_areas(self) -> None:
        """Claim the format information strips without disturbing the timing row.

        Index 6 is skipped in both strips: those two modules belong to the
        timing patterns, which run underneath the format information and are
        already drawn. Overwriting them produces a code that looks right and
        scans intermittently.
        """
        size = self.size
        for i in range(9):
            if i == 6:
                continue
            self.set_function(8, i, False)
            self.set_function(i, 8, False)
        for i in range(8):
            self.set_function(8, size - 1 - i, False)
            self.set_function(size - 1 - i, 8, False)
        self.set_function(size - 8, 8, True)  # the module that is always dark

    def _draw_version_info(self) -> None:
        remainder = self.version
        for _ in range(12):
            remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
        bits = self.version << 12 | remainder
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a = self.size - 11 + i % 3
            b = i // 3
            self.set_function(b, a, dark)
            self.set_function(a, b, dark)

    def draw_codewords(self, data: bytes) -> None:
        """The zigzag walk: two-module columns, right to left, alternating up
        and down, skipping the vertical timing column."""
        index = 0
        total_bits = len(data) * 8
        right = self.size - 1
        while right >= 1:
            if right == 6:
                right = 5  # column 6 is the timing pattern, never data
            for vertical in range(self.size):
                for offset in range(2):
                    col = right - offset
                    upward = ((right + 1) & 2) == 0
                    row = (self.size - 1 - vertical) if upward else vertical
                    if not self.function[row][col] and index < total_bits:
                        bit = (data[index >> 3] >> (7 - (index & 7))) & 1
                        self.modules[row][col] = bit == 1
                        index += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for row in range(self.size):
            for col in range(self.size):
                if self.function[row][col]:
                    continue
                if _mask_condition(mask, row, col):
                    self.modules[row][col] = not self.modules[row][col]

    def draw_format_info(self, mask: int) -> None:
        data = _EC_LEVEL_BITS_M << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412

        size = self.size
        for i in range(6):
            self.set_function(i, 8, _bit(bits, i))
        self.set_function(7, 8, _bit(bits, 6))
        self.set_function(8, 8, _bit(bits, 7))
        self.set_function(8, 7, _bit(bits, 8))
        for i in range(9, 15):
            self.set_function(8, 14 - i, _bit(bits, i))

        for i in range(8):
            self.set_function(8, size - 1 - i, _bit(bits, i))
        for i in range(8, 15):
            self.set_function(size - 15 + i, 8, _bit(bits, i))
        self.set_function(size - 8, 8, True)

    def penalty(self) -> int:
        return (
            self._penalty_runs()
            + self._penalty_blocks()
            + self._penalty_finder_lookalikes()
            + self._penalty_balance()
        )

    def _penalty_runs(self) -> int:
        score = 0
        for line in self._rows_and_columns():
            run_colour = line[0]
            run_length = 1
            for module in line[1:]:
                if module == run_colour:
                    run_length += 1
                else:
                    if run_length >= 5:
                        score += 3 + (run_length - 5)
                    run_colour = module
                    run_length = 1
            if run_length >= 5:
                score += 3 + (run_length - 5)
        return score

    def _penalty_blocks(self) -> int:
        score = 0
        for row in range(self.size - 1):
            for col in range(self.size - 1):
                value = self.modules[row][col]
                if (
                    value == self.modules[row][col + 1]
                    and value == self.modules[row + 1][col]
                    and value == self.modules[row + 1][col + 1]
                ):
                    score += 3
        return score

    def _penalty_finder_lookalikes(self) -> int:
        """The 1:1:3:1:1 pattern with four light modules on one side of it.

        A run that looks like a finder pattern misleads a scanner about where
        the symbol is. The edge of the symbol counts as the light area, since
        the quiet zone supplies it.
        """
        pattern = [True, False, True, True, True, False, True]
        score = 0
        for line in self._rows_and_columns():
            index = _find(line, pattern, 0)
            while index != -1:
                after = index + 7
                if not any(line[max(index - 4, 0) : index]) or not any(line[after : after + 4]):
                    score += 40
                    resume = after
                else:
                    # Overlapping matches can only restart at the second dark
                    # run, so skipping ahead is safe and avoids double counting.
                    resume = index + 4
                index = _find(line, pattern, resume)
        return score

    def _penalty_balance(self) -> int:
        dark = sum(module for row in self.modules for module in row)
        total = self.size * self.size
        percent = dark * 100 / total
        return int(abs(percent - 50) // 5) * 10

    def _rows_and_columns(self) -> list[list[bool]]:
        rows = [list(row) for row in self.modules]
        columns = [list(column) for column in zip(*self.modules)]
        return rows + columns


def _bit(value: int, index: int) -> bool:
    return (value >> index) & 1 == 1


def _find(line: list[bool], pattern: list[bool], start: int) -> int:
    """Index of ``pattern`` in ``line`` at or after ``start``, or -1."""
    span = len(pattern)
    for index in range(start, len(line) - span + 1):
        if line[index : index + span] == pattern:
            return index
    return -1


def _mask_condition(mask: int, row: int, col: int) -> bool:
    if mask == 0:
        return (row + col) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return col % 3 == 0
    if mask == 3:
        return (row + col) % 3 == 0
    if mask == 4:
        return (row // 2 + col // 3) % 2 == 0
    if mask == 5:
        return (row * col) % 2 + (row * col) % 3 == 0
    if mask == 6:
        return ((row * col) % 2 + (row * col) % 3) % 2 == 0
    if mask == 7:
        return ((row + col) % 2 + (row * col) % 3) % 2 == 0
    raise ValueError(f"mask {mask} is not in 0..7")


def qr_matrix(text: str) -> list[list[bool]]:
    """Encode ``text`` and return the module grid, dark = True, no quiet zone."""
    payload = text.encode("utf-8")
    version = _choose_version(len(payload))
    codewords = _interleave(_encode_codewords(payload, version), version)

    best: _Canvas | None = None
    best_penalty = -1
    for mask in range(8):
        canvas = _Canvas(version)
        canvas.draw_function_patterns()
        canvas.draw_codewords(codewords)
        canvas.apply_mask(mask)
        canvas.draw_format_info(mask)
        penalty = canvas.penalty()
        if best is None or penalty < best_penalty:
            best, best_penalty = canvas, penalty

    assert best is not None
    return best.modules


def qr_svg(
    text: str,
    *,
    quiet_zone: int = 4,
    size_px: int = 240,
    title: str = "QR code",
) -> str:
    """Render ``text`` as a self-contained SVG string.

    Colours are hard-coded black on white rather than inherited from the page.
    A QR code read by a camera is not a themeable decoration: a dark-mode
    inversion or a low-contrast accent colour makes it unscannable, and the
    failure only shows up on someone else's phone.
    """
    modules = qr_matrix(text)
    span = len(modules) + quiet_zone * 2

    path: list[str] = []
    for row_index, row in enumerate(modules):
        for col_index, dark in enumerate(row):
            if dark:
                path.append(f"M{col_index + quiet_zone} {row_index + quiet_zone}h1v1h-1z")

    escaped = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {span} {span}" '
        f'width="{size_px}" height="{size_px}" role="img" aria-label="{escaped}" '
        f'shape-rendering="crispEdges" class="qr">'
        f"<title>{escaped}</title>"
        f'<rect width="{span}" height="{span}" fill="#ffffff"/>'
        f'<path fill="#000000" d="{"".join(path)}"/>'
        f"</svg>"
    )


__all__ = ["QrTooLong", "error_correction_codewords", "qr_matrix", "qr_svg", "MAX_VERSION"]
