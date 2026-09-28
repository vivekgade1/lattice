@procedure
def fact(n: Int) -> Int:
    if n <= 1:
        return 1
    return n * fact(n - 1)


@procedure
def is_even(n: Int) -> Bool:
    if n == 0:
        return True
    return is_odd(n - 1)


@procedure
def is_odd(n: Int) -> Bool:
    if n == 0:
        return False
    return is_even(n - 1)
