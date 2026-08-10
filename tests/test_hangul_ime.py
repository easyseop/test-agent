"""한글 조합 입력 — 조합 단계 계산과 설정 검증.

브라우저 없이 확인할 수 있는 부분만 여기서 본다. 실제로 조합 이벤트가
발생하는지는 브라우저가 필요하므로 scripts/check_ime.py로 확인한다.
"""
import pytest

from webtest_agent.config import ConfigError, load_config
from webtest_agent.hangul import composition_states, is_syllable, syllable_stages


def test_syllable_with_final_consonant():
    """받침이 있는 글자는 세 단계를 거친다."""
    assert syllable_stages("한") == ["ㅎ", "하", "한"]


def test_syllable_without_final_consonant():
    """받침이 없으면 두 단계다 — 없는 단계를 지어내지 않는다."""
    assert syllable_stages("가") == ["ㄱ", "가"]


def test_non_syllable_has_no_stages():
    """영문·숫자·낱자는 조합 과정이 없다."""
    assert syllable_stages("a") == ["a"]
    assert syllable_stages("ㄱ") == ["ㄱ"]      # 완성형이 아닌 낱자
    assert not is_syllable("ㄱ") and is_syllable("한")


def test_word_composition_keeps_settled_prefix():
    """앞 글자는 확정된 채로 두고 마지막 글자만 조합된다."""
    assert composition_states("한글") == ["ㅎ", "하", "한", "한ㄱ", "한그", "한글"]


def test_mixed_text():
    """한글과 영문이 섞여도 순서가 유지된다."""
    assert composition_states("a가") == ["a", "a" + "ㄱ", "a가"]


def test_empty_text_produces_no_states():
    assert composition_states("") == []


def test_last_state_is_always_the_full_text():
    """마지막 상태는 입력하려던 값과 같아야 한다.

    이게 어긋나면 조합이 끝난 뒤 화면에 남는 글자가 의도와 달라진다.
    """
    for word in ["한글", "테스트", "안녕하세요", "값 검증", "OpenMetadata 한글"]:
        assert composition_states(word)[-1] == word


def test_every_state_extends_the_previous_prefix():
    """각 상태는 직전 확정 부분을 그대로 이어받는다 — 앞 글자가 사라지지 않는다."""
    states = composition_states("한글날")
    for earlier, later in zip(states, states[1:]):
        # 같은 글자를 조합 중이면 마지막 한 글자만 바뀌고, 다음 글자로 넘어가면
        # 직전 상태가 접두사가 된다.
        assert later[:-1] == earlier[:-1] or later.startswith(earlier[:-1])


# ------------------------------------------------------------- 설정 검증

def _write(tmp_path, browser):
    p = tmp_path / "c.yaml"
    p.write_text(
        f"target:\n  base_url: http://x.test\n  browser: {browser}\n"
        "spec_checks:\n"
        "  - name: 한글입력\n"
        "    steps:\n"
        "      - {action: type_ime, selector: '#editor', value: 한글}\n",
        encoding="utf-8",
    )
    return str(p)


def test_type_ime_rejected_on_non_chromium(tmp_path):
    """재현할 수 없는 엔진에서는 시작 전에 막는다.

    돌려 놓고 중간에 실패하게 두면, 조합을 재현하지 못한 실행에 판정이 붙는다.
    """
    with pytest.raises(ConfigError, match="chromium"):
        load_config(_write(tmp_path, "firefox"))


def test_type_ime_allowed_on_chromium(tmp_path):
    cfg = load_config(_write(tmp_path, "chromium"))
    assert cfg.spec_checks[0].steps[0].action == "type_ime"
