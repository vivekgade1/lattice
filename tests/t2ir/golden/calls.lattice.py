@procedure
def helper(x: Int) -> Int:
    return x - 1


@procedure
def calls(xs: list[Int], ys: list[Int]) -> Int:
    s = sum(xs)
    ordered = sorted(xs, key=lambda v: -v)
    lo = min(s, 3)
    hi = max(xs)
    zs = list(ys)
    zs.extend(xs)
    zs.append(helper(lo))
    seen = set(zs)
    seen.update(ys)
    return abs(hi) + len(ordered) + len(seen) + helper(x=s)
