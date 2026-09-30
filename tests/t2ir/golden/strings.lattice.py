@procedure
def strings(name: Str, n: Int, ratio: Float) -> Str:
    head = f"{name!r}: {n:>5} {ratio:.2f} {name}"
    width = f"{name:>{n}}"
    cleaned = name.replace("-", " ")
    joined = ", ".join([cleaned, head])
    padded = joined.ljust(n, ".") + name.zfill(3) + name.center(n)
    return padded + width + name.upper()
