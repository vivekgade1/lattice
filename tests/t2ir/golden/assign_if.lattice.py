@procedure
def classify(n: Int, flag: Bool) -> Str:
    """A docstring lowers to Pass."""
    label: Str = "zero"
    limit: Int
    a = b = n - 1
    if n > 0 and flag:
        label = "positive"
    elif n < 0 or not flag:
        label = "negative"
    else:
        pass
    size = "big" if a > 100 else "small"
    return label + size if b != 0 else label
