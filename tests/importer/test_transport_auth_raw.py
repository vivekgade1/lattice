from lattice.importer.transport import AuthDriver


def test_an_empty_scheme_sends_the_raw_token():
    d = AuthDriver(scheme="", header="X-Api-Key", mint=lambda h: "key-" + h)
    assert d.headers("k1") == {"X-Api-Key": "k1"}
    assert AuthDriver().headers("t") == {"Authorization": "Bearer t"}
