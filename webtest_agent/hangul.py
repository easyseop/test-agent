"""한글 조합(IME) 중간 단계 생성.

사람이 "한글"을 칠 때 화면은 ㅎ → 하 → 한 → 한ㄱ → 한그 → 한글 순서로
바뀐다. 이 중간 상태들이 실제로 브라우저에 전달돼야 "조합 중에 글자가
유실되는가"를 재현할 수 있다. 완성된 문자열만 한 번에 넣으면 조합 자체가
일어나지 않아, 조합을 깨뜨리는 결함이 있어도 그대로 통과한다.

여기는 순수 계산만 한다. 브라우저를 몰라야 표준 유니코드 규칙만으로
검증할 수 있다.
"""

# 유니코드 한글 음절: 0xAC00 + (초성*21 + 중성)*28 + 종성
_BASE = 0xAC00
_LAST = 0xD7A3
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def is_syllable(ch: str) -> bool:
    """완성형 한글 음절인지. 낱자(ㄱ)나 영문은 조합 단계가 없다."""
    return len(ch) == 1 and _BASE <= ord(ch) <= _LAST


def syllable_stages(ch: str) -> list[str]:
    """음절 하나가 조합되며 거치는 중간 글자들.

    '한' → ['ㅎ', '하', '한'] · '가' → ['ㄱ', '가']
    """
    if not is_syllable(ch):
        return [ch]
    index = ord(ch) - _BASE
    cho, jung, jong = index // 588, (index % 588) // 28, index % 28
    stages = [_CHO[cho], chr(_BASE + (cho * 21 + jung) * 28)]
    if jong:
        stages.append(ch)
    return stages


def composition_states(text: str) -> list[str]:
    """문자열 전체를 칠 때 조합칸에 나타나는 상태들을 순서대로.

    앞서 확정된 글자는 그대로 두고 마지막 음절만 조합되므로, 각 상태는
    '확정된 앞부분 + 조합 중인 글자' 형태가 된다.

    '한글' → ['ㅎ', '하', '한', '한ㄱ', '한그', '한글']
    """
    states: list[str] = []
    settled = ""
    for ch in text:
        for stage in syllable_stages(ch):
            states.append(settled + stage)
        settled += ch
    return states
