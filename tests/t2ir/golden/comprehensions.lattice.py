@procedure
def comps(xs: list[Int], words: list[Str]) -> Int:
    evens = [x // 2 for x in xs if x % 2 == 0 if x > 0]
    uniq = {w.lower() for w in words}
    index = {w: i for i, w in enumerate(words) if w != ""}
    pairs = [(x, y) for x in xs for y in evens]
    has_big = any(x > 10 for x in xs)
    return len(evens) + len(uniq) + len(index) + len(pairs) + (1 if has_big else 0)
