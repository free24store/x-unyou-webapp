import random
from datetime import date, timedelta

PHASE_POSTS_PER_DAY = {"player": (4, 6), "early": (5, 8), "monetize": (5, 10)}
WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


def generate_calendar(week_no: int, phase: str, vocab: dict) -> list:
    """Return a list of 7 day-dicts, each with a 'slots' list.

    Each slot: {post_no, type, education_name}
    """
    lo, hi = PHASE_POSTS_PER_DAY.get(phase, (5, 8))
    types = vocab["post_types_default10"] + vocab["player_phase_categories"]
    edu_stages = vocab["education_stages_basic"] + vocab["education_stages_boost"]

    rnd = random.Random(week_no)
    start = date.today()
    days = []
    for d in range(7):
        day = start + timedelta(days=d)
        n_posts = rnd.randint(lo, hi)
        slots = []
        for i in range(n_posts):
            slot_type = "興味付けポスト" if i == 0 else rnd.choice(types)
            edu = rnd.choice(edu_stages)
            slots.append({
                "post_no": i + 1,
                "type": slot_type,
                "education_name": edu["name"],
            })
        days.append({
            "date": day.isoformat(),
            "weekday": WEEKDAYS[day.weekday()],
            "slots": slots,
        })
    return days
