@procedure
def subs(xs: list[Int], d: dict[Str, Int], pair: tuple[Int, Str]) -> Int:
    e: dict[Str, Int] = {}
    e.update(d)
    first = xs[0]
    last = xs[-1]
    middle = xs[1:-1]
    stepped = xs[::2]
    e["k"] = first
    e["j"] = pair[0]
    return first + last + len(middle) + len(stepped) + e["k"] + len(pair[1])
