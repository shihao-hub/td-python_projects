import pytest

from next_scene.llm import LLMError, extract_json, normalize_directions


def test_extract_json_plain():
    assert extract_json('{"directions": []}') == {"directions": []}


def test_extract_json_fenced():
    text = '```json\n{"directions": [1]}\n```'
    assert extract_json(text) == {"directions": [1]}


def test_extract_json_with_prefix_text():
    text = '好的，以下是结果：{"directions": [{"title": "当面对质"}]}'
    assert extract_json(text)["directions"][0]["title"] == "当面对质"


def test_extract_json_nested_braces():
    text = '前缀 {"a": {"b": 1}} 后缀'
    assert extract_json(text) == {"a": {"b": 1}}


def test_extract_json_fenced_with_trailing_text():
    text = '```json\n{"directions": []}\n```\n以上。'
    assert extract_json(text) == {"directions": []}


def test_extract_json_invalid_raises():
    with pytest.raises(LLMError):
        extract_json("完全不是 JSON")


def test_normalize_directions_from_dict():
    data = {
        "directions": [
            {
                "title": "当面对质",
                "action": "她拿着信去问母亲",
                "link": "承接伪造落款",
                "consequence": "母子信任破裂",
                "extra": "ignored",
            }
        ]
    }
    result = normalize_directions(data)
    assert len(result) == 1
    assert set(result[0]) == {"title", "action", "link", "consequence"}


def test_normalize_directions_from_list_truncates_to_three():
    data = [{"title": f"方向{i}", "action": "行动", "link": "承接", "consequence": "后果"} for i in range(5)]
    assert len(normalize_directions(data)) == 3


def test_normalize_directions_skips_invalid_items():
    data = {"directions": ["not-a-dict", {"title": "有效", "action": "行动", "link": "", "consequence": ""}]}
    result = normalize_directions(data)
    assert len(result) == 1
    assert result[0]["title"] == "有效"


def test_normalize_directions_missing_title_raises():
    with pytest.raises(LLMError):
        normalize_directions({"directions": [{"action": "只有行动"}]})


def test_normalize_directions_empty_raises():
    with pytest.raises(LLMError):
        normalize_directions({"directions": []})
