"""Narration timing: which subtitle lines are read together, and when each reading starts and how fast.

A translation is often longer than the line it translates, so a reading may not fit between its line's start
and the next line's start. Instead of simply reading faster, in this order:
  1. short lines that belong together are read as one sentence (a sentence that goes on into the next line,
     or a line too long for its slot next to a short one): one natural reading over both slots, no pause;
  2. a reading may start a little before its line (LEAD) when the previous reading ended early, and may run
     on into the next line's time, pushing that reading later by up to MAX_DELAY; the readings after it
     catch up in the next pause;
  3. only what is left is read faster, never faster than the "Fastest reading" setting (a little more only
     when the narration has fallen far behind the picture). In a stretch of dense talk the readings ahead
     are estimated too, and the whole stretch is read evenly a little faster, instead of falling behind and
     then rushing a few lines.
The subtitles on screen keep their own timing; only the voice moves.
"""
from dataclasses import dataclass, field

LEAD = 0.4            # a reading may start this much before its line
MAX_DELAY = 0.8       # ... and push the next reading this much later
PAUSE = 0.12          # silence kept between two readings
JOIN_GAP = 0.6        # lines further apart than this are never read together
CONT_GAP = 0.35       # a sentence going on into the next line: joined when the gap is at most this
JOIN_SPAN = 12.0      # a joined reading covers at most this many seconds
JOIN_LINES = 3
LOOK_AHEAD = 15.0     # seconds of talk looked at ahead ...
BREATH = 1.0          # ... up to a pause this long, where the narration can catch up
SENTENCE_END = (".", "!", "?", "…", "。", "！", "？", '"', "”", "»", ")", "]")

# rough reading rate (syllables per second at speed 100) until the real one is measured
RATE0 = {"edge": 5.2, "kokoro": 4.6, "piper": 4.8}


def syllables(text: str) -> int:
    """Vietnamese writes one syllable per word; other languages are close enough for an estimate."""
    return max(1, len(text.split()))


@dataclass
class Unit:
    """One reading: one subtitle line, or a few read together."""
    lines: list            # indexes of the cues
    start: float           # the first line's start
    end: float             # the last line's end
    text: str
    gender: str | None
    next_start: float      # where the next reading's line starts (or the end of the video)
    slot0: float = 0.0     # the part of the timeline this reading covers (for the preview's lit timeline)
    slot1: float = 0.0
    extra: dict = field(default_factory=dict)


def join_text(a: str, b: str) -> str:
    a, b = a.rstrip(), b.strip()
    if not a:
        return b
    if a[-1].isalnum():
        return f"{a}, {b}"
    return f"{a} {b}"


class Grouper:
    """Makes the readings one by one, in order, from the cues (so the reading rate measured on the first
    readings improves the guesses for the next ones)."""

    def __init__(self, cues, genders, duration, engine="edge", speed=100, join=True):
        self.cues, self.genders, self.duration = cues, genders, duration
        self.join = join
        self.rate = RATE0.get(engine, 5.0) * speed / 100        # syllables per second
        self.i = 0

    def measured(self, text, seconds):
        """A real reading: adjust the rate (slowly, a few odd lines do not swing it)."""
        if seconds > 0.4:
            r = syllables(text) / seconds
            self.rate = 0.8 * self.rate + 0.2 * max(1.5, min(10.0, r))

    def estimate(self, text) -> float:
        return syllables(text) / self.rate

    def _next_start(self, j):
        return self.cues[j].start if j < len(self.cues) else self.duration

    def ahead(self, after: float) -> tuple[float, float]:
        """(reading time the next lines need, the time they have up to the next real pause), from the line
        after the current reading on."""
        need, j, n = 0.0, self.i, len(self.cues)
        while j < n:
            c = self.cues[j]
            need += self.estimate(c.text) + PAUSE
            nxt = self._next_start(j + 1)
            if nxt - c.end >= BREATH or nxt - after > LOOK_AHEAD:
                return need, nxt
            j += 1
        return need, self.duration

    def done(self) -> bool:
        return self.i >= len(self.cues)

    def next(self) -> Unit:
        cues, g = self.cues, self.genders
        first = self.i
        group = [first]
        text = cues[first].text
        while self.join and len(group) < JOIN_LINES:
            j = group[-1] + 1
            if j >= len(cues):
                break
            a, b = cues[group[-1]], cues[j]
            ga, gb = g[group[-1]], g[j]
            if ga and gb and ga != gb:                       # another voice
                break
            gap = b.start - a.end
            if gap > JOIN_GAP or b.end - cues[first].start > JOIN_SPAN:
                break
            goes_on = gap <= CONT_GAP and not a.text.rstrip().endswith(SENTENCE_END)
            slot_a = b.start - cues[first].start
            slot_b = self._next_start(j + 1) - b.start
            tight = self.estimate(text) > slot_a * 0.95 or self.estimate(b.text) > slot_b * 0.95
            if not (goes_on or tight):
                break
            group.append(j)
            text = join_text(text, b.text)
        self.i = group[-1] + 1
        gender = next((g[k] for k in group if g[k]), None)
        u = Unit(group, cues[first].start, cues[group[-1]].end, text, gender, self._next_start(self.i))
        u.slot0 = 0.0 if first == 0 else u.start
        u.slot1 = u.next_start
        return u


def run_on_limit(u: Unit, duration=0.0) -> float:
    """The latest a reading may end: into the next line's time by MAX_DELAY at most (the next reading waits
    that long). A reading that ends later than this did not fit even at the maximum speed: it is read late."""
    hi1 = u.next_start + MAX_DELAY - PAUSE
    return min(hi1, duration) if duration else hi1


class Scheduler:
    """Places the readings one after another: (start, speed) for each, from its natural length."""

    def __init__(self, max_speed=1.25, duration=0.0):
        self.max_speed = max(1.0, max_speed)
        self.duration = duration
        self.prev_end = -PAUSE

    def place(self, u: Unit, length: float, ahead=None) -> tuple[float, float]:
        """length: the reading at normal speed (s); ahead: Grouper.ahead() for the lines after it.
        Returns (start, speed); call `ended` with the real end."""
        start, speed = self._fit(u, length)
        if ahead:                              # dense talk ahead: share the speeding up evenly
            need, until = ahead
            room = until - start
            if room > 0:
                even = (length + PAUSE + need) / room
                if even > 1.05:
                    speed = max(speed, min(self.max_speed, even))
        return start, speed

    def _fit(self, u: Unit, length: float) -> tuple[float, float]:
        lo = max(0.0, self.prev_end + PAUSE, u.start - LEAD)        # earliest start
        natural = max(u.start, self.prev_end + PAUSE)
        hi0 = u.next_start - PAUSE                                   # end without pushing the next reading
        hi1 = run_on_limit(u, self.duration)                          # end when pushing it
        hi0 = min(hi0, hi1)
        if natural + length <= hi0:                                  # fits as it is
            return natural, 1.0
        room0 = hi0 - lo
        if room0 > 0 and length <= room0:                            # fits when started a little early
            return max(lo, min(natural, hi0 - length)), 1.0
        if room0 > 0 and length / room0 <= min(1.08, self.max_speed):   # a touch faster beats running late
            return lo, length / room0
        room1 = hi1 - lo
        if room1 > 0 and length <= room1:                            # runs on, the next reading waits
            return lo, 1.0
        # the maximum speed is a ceiling: when it is not enough the reading runs late (and the next ones wait)
        speed = min(self.max_speed, length / room1) if room1 > 0 else self.max_speed
        return lo, max(1.0, speed)

    def ended(self, end: float):
        self.prev_end = end
