@record
class Box:
    w: Float
    h: Float = 1


@procedure
def half(x: Float) -> Float:
    return x / 2


@procedure
def sites(n: Int, m: Int | None, flag: Bool, i: Int, xs: list[Int]) -> Float:
    x: Float = n
    o: Float | None = m
    x = y = n
    a = 0.5
    a, b = n, i
    for a in xs:
        pass
    ys = [n, 2.5]
    d = {n: n, 2.5: 0.5}
    e = {**{"k": n}, "j": 2.5}
    ys[0] = n
    d[n] = n
    z = (x or n) + (2.5 if flag else n) + (1, 2.5, 3)[i] + half(n) + Box(n).h
    t = [v for v in (n, 2.5)]
    found = n in ys
    s = sum(ys) + sum(ys, n) + max(n, 2.5) + round(n, 1) + n ** i + round(n)
    return n
