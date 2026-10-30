from jamcrammer.economy import (
    YIELDS,
    cp_to_next,
    hours_to_level,
    level_for,
    rate_from_runs,
)


def test_the_level_ladder() -> None:
    assert level_for(0) == 0 and level_for(50) == 1 and level_for(249) == 1
    assert level_for(17_000) == 10 and level_for(200_000) == 21
    assert cp_to_next(0) == 50 and cp_to_next(17_000) == 5_000 and cp_to_next(200_000) is None


def test_rates_from_runs() -> None:
    r = rate_from_runs("mystery_below_hard", runs=3, minutes_total=66.0)
    assert r.runs == 3 and abs(r.hours - 1.1) < 1e-9
    assert r.cp == YIELDS["mystery_below_hard"].cp_typical * 3
    assert 4_000 < r.cp_per_hour < 6_000
    assert r.gems == 900 and r.notes
    r2 = rate_from_runs("double_up", runs=10, minutes_total=7.5, gems_seen=400)
    assert r2.gems == 400 and not r2.notes and r2.gems_per_hour == 3_200
    assert hours_to_level(0, 10, 5_000) == 3.4
    assert hours_to_level(0, 99, 5_000) is None
