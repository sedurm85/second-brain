"""테스트용 '오늘' 고정. 픽스처가 특정 날짜(예: 2026-09-30)를 전제할 때, 실행일과 무관하게 같은 결과가 나오게 한다.

scripts/*.py는 `from datetime import date, datetime`로 이름을 가져다 쓰므로, 각 모듈의 date·datetime 이름을
today()/now()만 고정된 하위 클래스로 바꿔 끼운다(나머지 동작·산술·isinstance는 그대로). 서버 스레드도 같은 모듈을 쓴다.
"""
import datetime as _dt
import sys

_MODULES = ("brain", "agenda", "career", "studio", "weather", "reminders", "mailer", "apple_notes")


def freeze(today, hour=9, minute=0):
    """today(date)·그날 hour:minute(로컬 시간대)로 고정하고, 되돌리는 함수를 돌려준다."""
    tz = _dt.datetime.now().astimezone().tzinfo
    fixed_dt = _dt.datetime(today.year, today.month, today.day, hour, minute, tzinfo=tz)

    class FrozenDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(today.year, today.month, today.day)

    class FrozenDateTime(_dt.datetime):
        @classmethod
        def now(cls, tz_=None):
            return fixed_dt.astimezone(tz_) if tz_ else fixed_dt.replace(tzinfo=None)

        @classmethod
        def today(cls):
            return fixed_dt.replace(tzinfo=None)

    saved = []
    for name in _MODULES:
        mod = sys.modules.get(name)
        if not mod:
            continue
        for attr, repl in (("date", FrozenDate), ("datetime", FrozenDateTime)):
            if getattr(mod, attr, None) in (_dt.date, _dt.datetime) or getattr(getattr(mod, attr, None), "__mro__", ())[1:2] in ((_dt.date,), (_dt.datetime,)):
                saved.append((mod, attr, getattr(mod, attr)))
                setattr(mod, attr, repl)

    def restore():
        for mod, attr, orig in reversed(saved):
            setattr(mod, attr, orig)
    return restore
