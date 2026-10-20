import random
import statistics

from chessfish_host.engine.humanize import humanized_delay


def med(**kw):
    rng = random.Random(1)
    args = dict(remaining_ms=600_000, increment_ms=5000, move_number=20, legal_count=30,
                top2_spread_cp=None, is_recapture=False, min_ms=800, max_ms=20000)
    args.update(kw)
    return statistics.median(humanized_delay(**args, rng=rng) for _ in range(500))


def test_bounds():
    rng = random.Random(0)
    for _ in range(2000):
        d = humanized_delay(rng.choice([None, 5000, 60_000, 600_000]), rng.choice([0, 2000]), rng.randint(1, 80),
                            rng.randint(1, 40), rng.choice([None, 0, 50, 400]), rng.random() < .3, 800, 20000, rng)
        assert 0 <= d <= 20000


def test_never_more_than_8_percent_of_clock():
    assert med(remaining_ms=5000) <= 400


def test_shape():
    normal = med()
    assert med(legal_count=1) < normal * 0.4
    assert med(is_recapture=True) < normal * 0.7
    assert med(move_number=3) < normal * 0.6
    assert med(top2_spread_cp=5) > med(top2_spread_cp=300)
    assert med(remaining_ms=60_000) < normal
