"""Rectangle intersections, independent of content type and rendering policy."""

from collections.abc import Sequence


def bbox_intersection(
    left: Sequence[float], right: Sequence[float],
) -> tuple[float, float, float, float] | None:
    """Return the positive-area intersection of (x0, y0, x1, y1) boxes.

    Both boxes must use the same coordinates and units. Edge/corner contact,
    empty boxes and inverted boxes have no intersection; no tolerance is applied.
    """
    x0 = max(float(left[0]), float(right[0]))
    y0 = max(float(left[1]), float(right[1]))
    x1 = min(float(left[2]), float(right[2]))
    y1 = min(float(left[3]), float(right[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def bbox_intersects(left: Sequence[float], right: Sequence[float]) -> bool:
    return bbox_intersection(left, right) is not None


def bbox_overlap_area(left: Sequence[float], right: Sequence[float]) -> float:
    intersection = bbox_intersection(left, right)
    if intersection is None:
        return 0.0
    x0, y0, x1, y1 = intersection
    return (x1 - x0) * (y1 - y0)


def subtract_bbox(rect, exclusion) -> list[tuple[float, float, float, float]]:
    """Partition the uncovered area without dropping narrow source-image strips."""
    overlap = bbox_intersection(rect, exclusion)
    if overlap is None:
        return [tuple(rect)]
    x0, y0, x1, y1 = rect
    ix0, iy0, ix1, iy1 = overlap
    return [box for box in ((x0, y0, x1, iy0), (x0, iy1, x1, y1),
                           (x0, iy0, ix0, iy1), (ix1, iy0, x1, iy1))
            if box[2] > box[0] and box[3] > box[1]]
