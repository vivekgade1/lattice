@procedure
def scan(xs: list[Int], limit: Int) -> Int:
    total = 0
    for i, x in enumerate(xs):
        if x < 0:
            continue
        if i > limit:
            break
        total = total - x
    n = 0
    while n < limit:
        n = n + 1
        if n == 3:
            break
    while True:
        return total
    return -1
