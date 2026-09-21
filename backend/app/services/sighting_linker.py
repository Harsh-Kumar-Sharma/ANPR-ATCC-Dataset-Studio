"""One vehicle going past the camera is one track.

ByteTrack matches by overlap, which works for something large and
slow and not at all for a number plate: at 7fps a plate has moved
clean off its own last position by the next frame, so it is reported
unconfirmed every time and every frame of the same car becomes its
own single sighting. One car past the camera, seventeen entries to
review, sixteen of them the same plate.

What links them is motion. Where a box was and how fast it was going
says roughly where it will be next, and a box near that prediction,
of about the right size, is the same thing. Two cars in the same lane
stay two tracks because each one's own prediction fits it better than
the other's - which is what "give them each their own tracking id"
needs to mean.

Deliberately not a second tracker. It runs once, over what a whole
pass already observed, so it can be simple: no state machine, no
Kalman filter, nothing to tune per stream.
"""

from dataclasses import dataclass, field
from typing import TypeVar

#: How far a box may be from where its chain predicted it, measured
#: in diagonals of the two boxes. Generous, because the prediction
#: from a single previous frame is rough and a plate is small - one
#: diagonal of a 100x50 plate is barely 5% of a 1080p frame's width.
MAX_TRAVEL_IN_DIAGONALS = 2.0

#: How different in area two boxes may be and still be one thing. A
#: plate grows as it approaches, but it does not double between
#: consecutive frames.
MIN_SIZE_RATIO = 0.4

#: How many processed frames a chain may go unseen before it is
#: closed. One missed detection in the middle of a pass should not
#: split a vehicle in two; half a second of nothing is the next car.
MAX_GAP_IN_FRAMES = 2

Bbox = tuple[float, float, float, float]
#: Anything with the fields used here - in practice
#: ``track_processor._Observation``. Named loosely on purpose: this
#: module has no business importing the processor that calls it.
Observation = TypeVar("Observation")


@dataclass
class _Chain:
    """One vehicle as it has been seen so far."""

    track_id: int
    observations: list = field(default_factory=list)
    last_frame: int = 0
    last_bbox: Bbox = (0.0, 0.0, 0.0, 0.0)
    #: Pixels per processed frame, from the last two sightings. Zero
    #: until there are two, which is why the first link is the
    #: loosest one this makes.
    velocity: tuple[float, float] = (0.0, 0.0)
    class_id: int = 0
    #: Which processed frame, counted in order rather than by index,
    #: last extended this chain. What ``MAX_GAP_IN_FRAMES`` ages.
    position: int = 0

    def predict(self, frame_index: int) -> Bbox:
        steps = frame_index - self.last_frame
        dx, dy = self.velocity[0] * steps, self.velocity[1] * steps
        x1, y1, x2, y2 = self.last_bbox
        return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)

    def extend(self, observation, frame_index: int, bbox: Bbox) -> None:
        steps = max(1, frame_index - self.last_frame)
        previous = _centre(self.last_bbox)
        current = _centre(bbox)
        self.velocity = ((current[0] - previous[0]) / steps, (current[1] - previous[1]) / steps)
        self.observations.append(observation)
        self.last_frame = frame_index
        self.last_bbox = bbox


def link_single_sightings(observations_by_track: dict[int, list]) -> dict[int, list]:
    """Join single sightings of the same vehicle into one track.

    Tracks ByteTrack managed to follow are passed through untouched -
    it saw the frames, and this has nothing to add to that judgement.
    Only the one-frame sightings are reconsidered, and only against
    each other.

    Frames are stepped through in the order they were processed, not
    by index: sampling at 5fps from a 10fps source numbers them 0, 2,
    4, and a gap measured in indexes would call every one of them a
    missed frame.
    """
    followed = {
        track_id: observations
        for track_id, observations in observations_by_track.items()
        if len(observations) > 1 or (observations and observations[0].confirmed)
    }
    loose = [
        (track_id, observations[0])
        for track_id, observations in observations_by_track.items()
        if track_id not in followed and observations
    ]
    if not loose:
        return observations_by_track

    by_frame: dict[int, list[tuple[int, object]]] = {}
    for track_id, sighting in loose:
        by_frame.setdefault(sighting.frame_index, []).append((track_id, sighting))

    open_chains: list[_Chain] = []
    closed: list[_Chain] = []
    for position, frame_index in enumerate(sorted(by_frame)):
        # Chains nothing has extended for a while are finished. Aged
        # in processed frames, not indexes, for the reason above.
        still_open = []
        for chain in open_chains:
            (still_open if position - chain.position <= MAX_GAP_IN_FRAMES else closed).append(chain)
        open_chains = still_open

        for chain, (track_id, sighting) in _pair_up(open_chains, by_frame[frame_index], frame_index):
            if chain is None:
                started = _Chain(
                    track_id=track_id,
                    observations=[sighting],
                    last_frame=frame_index,
                    last_bbox=tuple(sighting.bbox),
                    class_id=sighting.class_id,
                )
                started.position = position
                open_chains.append(started)
            else:
                chain.extend(sighting, frame_index, tuple(sighting.bbox))
                chain.position = position

    linked = dict(followed)
    for chain in closed + open_chains:
        linked[chain.track_id] = chain.observations
    return linked


def _pair_up(chains: list[_Chain], sightings: list[tuple[int, object]], frame_index: int):
    """Decide, for one frame, which sighting extends which chain.

    Greedy on the closest fit first. A frame has a handful of
    vehicles on it, so the simple thing is also the fast thing, and
    "closest first" is what keeps two cars in one lane apart: each
    one's own chain is nearer to it than the other's.

    Yields ``(chain, sighting)``, with ``chain`` None for a sighting
    that starts a new one. A chain takes at most one sighting per
    frame - a track is one thing in one place.
    """
    costs = []
    for chain in chains:
        for entry in sightings:
            cost = _cost(chain, entry[1], frame_index)
            if cost is not None:
                costs.append((cost, chain, entry))
    costs.sort(key=lambda row: row[0])

    taken_chains: set[int] = set()
    taken_sightings: set[int] = set()
    for _, chain, entry in costs:
        if id(chain) in taken_chains or entry[0] in taken_sightings:
            continue
        taken_chains.add(id(chain))
        taken_sightings.add(entry[0])
        yield chain, entry

    for entry in sightings:
        if entry[0] not in taken_sightings:
            yield None, entry


def _cost(chain: _Chain, sighting, frame_index: int) -> float | None:
    """How well this sighting fits this chain, or None for "it does not".

    None rather than a large number so that a bad fit can never win a
    greedy round just by being the only one left.
    """
    if sighting.class_id != chain.class_id:
        return None

    bbox = tuple(sighting.bbox)
    if _size_ratio(chain.last_bbox, bbox) < MIN_SIZE_RATIO:
        return None

    predicted = chain.predict(frame_index)
    reach = (_diagonal(predicted) + _diagonal(bbox)) / 2
    if reach <= 0:
        return None
    distance = _distance(_centre(predicted), _centre(bbox)) / reach
    return distance if distance <= MAX_TRAVEL_IN_DIAGONALS else None


def _centre(bbox: Bbox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def _diagonal(bbox: Bbox) -> float:
    return ((bbox[2] - bbox[0]) ** 2 + (bbox[3] - bbox[1]) ** 2) ** 0.5


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _size_ratio(a: Bbox, b: Bbox) -> float:
    areas = sorted((_area(a), _area(b)))
    return areas[0] / areas[1] if areas[1] > 0 else 0.0


def _area(bbox: Bbox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
