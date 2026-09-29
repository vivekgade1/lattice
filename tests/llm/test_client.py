import types

import pytest

from lattice.llm.client import (
    ClaudeGenerator,
    Generation,
    GenerationRefused,
    ScriptedGenerator,
    Usage,
    extract_code,
)


def _text_block(text: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(type="text", text=text)


def _thinking_block(thinking: str) -> types.SimpleNamespace:
    # Deliberately has no `.text` attribute: generate() must never access
    # `.text` on a block whose type isn't "text".
    return types.SimpleNamespace(type="thinking", thinking=thinking)


def _fake_client(response, capture=None):
    def create(**kwargs):
        if capture is not None:
            capture.append(kwargs)
        return response

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def _usage(input_tokens=10, output_tokens=20, cache_read=0, cache_creation=0):
    return types.SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )


def _response(content, stop_reason="end_turn", usage=None):
    return types.SimpleNamespace(content=content, usage=usage or _usage(), stop_reason=stop_reason)


class TestClaudeGeneratorRequestShape:
    def test_sends_exact_kwargs(self):
        calls = []
        client = _fake_client(_response([_text_block("hi")]), capture=calls)
        gen = ClaudeGenerator(client=client)

        gen.generate("you are helpful", [{"role": "user", "content": "hello"}])

        assert len(calls) == 1
        kwargs = calls[0]
        assert kwargs["model"] == "claude-opus-5"
        assert kwargs["max_tokens"] == 16000
        assert "betas" not in kwargs and "fallbacks" not in kwargs  # no silent reroute
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["system"] == [
            {
                "type": "text",
                "text": "you are helpful",
                "cache_control": {"type": "ephemeral"},
            }
        ]
        assert kwargs["messages"] == [{"role": "user", "content": "hello"}]

    def test_custom_model_max_tokens_and_effort_are_forwarded(self):
        calls = []
        client = _fake_client(_response([_text_block("hi")]), capture=calls)
        gen = ClaudeGenerator(model="claude-opus-4-8", max_tokens=4000, effort="low", client=client)

        gen.generate("sys", [])

        kwargs = calls[0]
        assert kwargs["model"] == "claude-opus-4-8"
        assert kwargs["max_tokens"] == 4000
        assert kwargs["output_config"] == {"effort": "low"}

    def test_messages_passed_as_list_copy(self):
        calls = []
        client = _fake_client(_response([_text_block("hi")]), capture=calls)
        gen = ClaudeGenerator(client=client)

        messages = ({"role": "user", "content": "a"}, {"role": "assistant", "content": "b"})
        gen.generate("sys", messages)

        assert calls[0]["messages"] == [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        assert isinstance(calls[0]["messages"], list)


class TestClaudeGeneratorResponseHandling:
    def test_text_blocks_concatenated_and_thinking_ignored(self):
        response = _response(
            [
                _thinking_block("reasoning..."),
                _text_block("hello "),
                _thinking_block("more reasoning"),
                _text_block("world"),
            ]
        )
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        result = gen.generate("sys", [])

        assert result.text == "hello world"

    def test_usage_copied_from_response(self):
        response = _response(
            [_text_block("x")],
            usage=_usage(input_tokens=111, output_tokens=222, cache_read=33, cache_creation=44),
        )
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        result = gen.generate("sys", [])

        assert result.usage == Usage(
            input_tokens=111,
            output_tokens=222,
            cache_read_input_tokens=33,
            cache_creation_input_tokens=44,
        )

    def test_usage_missing_fields_default_to_zero(self):
        bare_usage = types.SimpleNamespace(input_tokens=5, output_tokens=7)
        response = _response([_text_block("x")], usage=bare_usage)
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        result = gen.generate("sys", [])

        assert result.usage == Usage(
            input_tokens=5,
            output_tokens=7,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )

    def test_stop_reason_copied(self):
        response = _response([_text_block("x")], stop_reason="tool_use")
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        result = gen.generate("sys", [])

        assert result.stop_reason == "tool_use"

    def test_latency_is_measured(self):
        response = _response([_text_block("x")])
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        result = gen.generate("sys", [])

        assert isinstance(result.latency_s, float)
        assert result.latency_s >= 0.0

    def test_refusal_raises(self):
        response = _response([], stop_reason="refusal")
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        with pytest.raises(GenerationRefused):
            gen.generate("sys", [])

    def test_returns_generation_instance(self):
        response = _response([_text_block("x")])
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        result = gen.generate("sys", [])

        assert isinstance(result, Generation)


class TestClaudeGeneratorLazyClient:
    def test_no_anthropic_client_constructed_when_client_injected(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError(
                "anthropic.Anthropic() must not be constructed when a client is injected"
            )

        import anthropic

        monkeypatch.setattr(anthropic, "Anthropic", boom)

        response = _response([_text_block("x")])
        client = _fake_client(response)
        gen = ClaudeGenerator(client=client)

        gen.generate("sys", [])  # must not raise

    def test_no_api_key_client_is_ever_constructed(self, monkeypatch):
        """Project policy: no API-key transport; a generator without an injected client
        refuses instead of building ``anthropic.Anthropic()`` from the environment."""
        import anthropic

        from lattice.llm.client import ApiKeyTransportDisabled

        calls = []
        monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: calls.append(1))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-be-used")
        gen = ClaudeGenerator()
        with pytest.raises(ApiKeyTransportDisabled):
            gen.generate("sys", [])
        assert calls == []


class TestScriptedGenerator:
    def test_returns_texts_in_order(self):
        gen = ScriptedGenerator(["first", "second"])

        r1 = gen.generate("sys1", [{"role": "user", "content": "a"}])
        r2 = gen.generate("sys2", [{"role": "user", "content": "b"}])

        assert r1.text == "first"
        assert r2.text == "second"

    def test_records_calls(self):
        gen = ScriptedGenerator(["only"])

        messages = [{"role": "user", "content": "a"}]
        gen.generate("sys", messages)

        assert gen.calls == [("sys", messages)]

    def test_raises_index_error_when_exhausted(self):
        gen = ScriptedGenerator(["only"])
        gen.generate("sys", [])

        with pytest.raises(IndexError):
            gen.generate("sys", [])

    def test_returns_generation_instance(self):
        gen = ScriptedGenerator(["text"])

        result = gen.generate("sys", [])

        assert isinstance(result, Generation)


class TestExtractCode:
    def test_prose_around_one_python_block(self):
        text = "Here is the code:\n\n```python\nprint(1)\n```\n\nThat's it."
        assert extract_code(text) == "print(1)\n"

    def test_two_blocks_first_python_wins(self):
        text = "```python\nprint('first')\n```\n\n```python\nprint('second')\n```\n"
        assert extract_code(text) == "print('first')\n"

    def test_py_info_string(self):
        text = "```py\nprint(1)\n```\n"
        assert extract_code(text) == "print(1)\n"

    def test_bare_fence_with_no_info_string(self):
        text = "```\nprint(1)\n```\n"
        assert extract_code(text) == "print(1)\n"

    def test_sql_only_block_returns_none(self):
        text = "```sql\nSELECT 1;\n```\n"
        assert extract_code(text) is None

    def test_no_fenced_block_returns_none(self):
        text = "just some prose, no code fences here"
        assert extract_code(text) is None

    def test_crlf_line_endings_tolerated(self):
        text = "```python\r\nprint(1)\r\n```\r\n"
        assert extract_code(text) == "print(1)\n"

    def test_python_block_wins_over_earlier_bare_block(self):
        text = "```\nprint('bare')\n```\n\n```python\nprint('python')\n```\n"
        assert extract_code(text) == "print('python')\n"

    def test_falls_back_to_bare_block_when_no_python_block(self):
        text = "```sql\nSELECT 1;\n```\n\n```\nprint(1)\n```\n"
        assert extract_code(text) == "print(1)\n"


class TestServedModelAndRefusalAccounting:
    """Regression tests for review finding minor 7."""

    def test_served_model_is_recorded(self):
        response = _response([_text_block("x")])
        response.model = "claude-opus-4-8"
        gen = ClaudeGenerator(client=_fake_client(response))

        result = gen.generate("sys", [])

        assert result.model == "claude-opus-4-8"

    def test_generation_model_defaults_to_empty(self):
        gen = Generation(text="x", usage=Usage(1, 2), latency_s=0.0, stop_reason="end_turn")
        assert gen.model == ""

    def test_usage_total_input_includes_cache_tokens(self):
        usage = Usage(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=1000,
        )
        assert usage.total_input_tokens == 1110

    def test_refusal_carries_usage_latency_and_model(self):
        response = _response(
            [],
            stop_reason="refusal",
            usage=_usage(input_tokens=7, output_tokens=3, cache_read=11, cache_creation=0),
        )
        response.model = "claude-opus-5"
        gen = ClaudeGenerator(client=_fake_client(response), refusal_retries=0)

        with pytest.raises(GenerationRefused) as info:
            gen.generate("sys", [])

        assert info.value.usage == Usage(7, 3, 11, 0)
        assert info.value.model == "claude-opus-5"
        assert info.value.latency_s >= 0.0

    def test_generation_refused_defaults(self):
        exc = GenerationRefused("nope")
        assert str(exc) == "nope"
        assert exc.usage == Usage(0, 0)
        assert exc.model == ""
        assert exc.latency_s == 0.0


def test_refusal_message_includes_category_and_explanation():
    response = _response([], stop_reason="refusal")
    response.stop_details = types.SimpleNamespace(category="cyber", explanation="flagged")
    gen = ClaudeGenerator(client=_fake_client(response))
    with pytest.raises(GenerationRefused, match="category='cyber'.*flagged"):
        gen.generate("s", [{"role": "user", "content": "u"}])


def test_refusal_is_retried_and_usage_accumulates():
    refusal = _response([], stop_reason="refusal", usage=_usage(input_tokens=5, output_tokens=0))
    ok = _response([_text_block("done")], usage=_usage(input_tokens=7, output_tokens=3))
    replies = iter([refusal, ok])
    client = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **_: next(replies)))
    gen = ClaudeGenerator(client=client, refusal_retries=2)

    result = gen.generate("s", [{"role": "user", "content": "u"}])

    assert result.text == "done"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 3)
    assert result.refusals == 1


def test_refusal_raises_after_retries_are_exhausted():
    refusal = _response([], stop_reason="refusal")
    gen = ClaudeGenerator(client=_fake_client(refusal), refusal_retries=1)
    with pytest.raises(GenerationRefused):
        gen.generate("s", [{"role": "user", "content": "u"}])


def test_haiku_requests_omit_effort():
    calls = []
    gen = ClaudeGenerator(
        model="claude-haiku-4-5", client=_fake_client(_response([_text_block("x")]), capture=calls)
    )
    gen.generate("s", [{"role": "user", "content": "u"}])
    assert "output_config" not in calls[0]
