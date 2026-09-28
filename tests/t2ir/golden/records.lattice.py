@record
class Point:
    x: Int
    y: Int
    tag: Str | None = None


@procedure
def shift(p: Point, dx: Int) -> Point:
    moved = Point(x=p.x + dx, y=p.y)
    return Point(moved.x, moved.y, tag="moved")
