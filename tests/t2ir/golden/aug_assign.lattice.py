@procedure
def aug(xs: list[Int], k: Int) -> Int:
    ys = list(xs)
    ys += [k]
    ys[0] -= 1
    ys[1] *= k
    s = "a"
    s *= k
    n = 2
    n **= 2
    n <<= 1
    n //= 3
    return n + len(s) + len(ys)
