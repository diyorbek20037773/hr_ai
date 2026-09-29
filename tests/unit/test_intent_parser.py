import pytest

from app.services.intent_parser import IntentParseError, IntentParser
from app.services.llm_service import FakeLLMService

RETRY = "Return only valid JSON matching the schema."
Q = "Oliy ma'lumotli, 30 yoshdan katta ayollar"


def _parse(output):
    fake = FakeLLMService({Q: output})
    return IntentParser(fake).parse(Q), fake


def test_valid_first_attempt():
    outcome, fake = _parse('{"education":"Oliy","fromAge":30,"sex":"Ayol"}')
    assert outcome.intent.to_compact_dict() == {"education": "Oliy", "fromAge": 30, "sex": "Ayol"}
    assert outcome.attempts == 1
    assert len(fake.calls) == 1
    assert outcome.prompt_version


def test_query_is_last_user_message():
    _, fake = _parse("{}")
    assert fake.calls[0][-1] == {"role": "user", "content": Q}
    assert fake.calls[0][0]["role"] == "system"


def test_fenced_output():
    outcome, _ = _parse('```json\n{"sex": "Ayol"}\n```')
    assert outcome.intent.to_compact_dict() == {"sex": "Ayol"}
    assert outcome.attempts == 1


def test_prefixed_output():
    outcome, _ = _parse('Natija: {"fromAge": 30}')
    assert outcome.intent.to_compact_dict() == {"fromAge": 30}


def test_trailing_commas_output():
    outcome, _ = _parse('{"fromAge": 30, "sex": "Ayol",}')
    assert outcome.intent.to_compact_dict() == {"fromAge": 30, "sex": "Ayol"}


def test_empty_output_object():
    outcome, _ = _parse("{}")
    assert outcome.intent.is_empty()


def test_garbage_one_retry_then_error():
    fake = FakeLLMService({Q: "I cannot do that"})
    with pytest.raises(IntentParseError) as exc:
        IntentParser(fake).parse(Q)
    assert getattr(exc.value, "code", None) == "LLM_OUTPUT_INVALID"
    assert len(fake.calls) == 2  # exactly one retry
    assert fake.calls[1][-1]["role"] == "user"
    assert fake.calls[1][-1]["content"] == RETRY


def test_valid_on_retry():
    fake = FakeLLMService({Q: ["sorry, no json", '{"fromAge": 30}']})
    outcome = IntentParser(fake).parse(Q)
    assert outcome.attempts == 2
    assert outcome.intent.to_compact_dict() == {"fromAge": 30}
    assert len(fake.calls) == 2
