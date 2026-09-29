from bcn.align import align
from bcn.srt import Cue
from bcn.text import sounds_alike, tokens


def cues(*texts, dur=3.0):
    return [Cue(i + 1, i * dur, i * dur + dur - 0.1, t, i * 4 + 2) for i, t in enumerate(texts)]


NARR = [
    "Every project begins with a topic. A topic is broad and hard to study.",
    "Think of the process as a funnel. Each step adds a constraint to the question.",
    "You might use a Likert scale. Or you might run interviews instead.",
]


def test_normalisation_is_symmetric():
    assert tokens("It's 15% of 1st-year students")[0] == ["it", "is", "fifteen", "percent", "of", "first", "year", "students"]
    assert tokens("don't")[0] == tokens("do not")[0]


def test_exact_match_places_every_boundary_on_a_cue_start():
    c = cues("Every project begins with a topic.", "A topic is broad and hard to study.",
             "Think of the process as a funnel.", "Each step adds a constraint to the question.",
             "You might use a Likert scale.", "Or you might run interviews instead.")
    a = align(NARR, c)
    assert [b.timecode for b in a.boundaries] == [0.0, 6.0, 12.0]
    assert all(b.confidence == 1.0 for b in a.boundaries)
    assert a.divergences == [] and a.divergence_ratio == 0


def test_mistranscription_is_classified():
    c = cues("Every project begins with a topic.", "A topic is broad and hard to study.",
             "Think of the process as a funnel.", "Each step adds a constraint to the question.",
             "You might use a like it scale.", "Or you might run interviews instead.")
    a = align(NARR, c)
    kinds = [(d.kind, d.script, d.srt) for d in a.divergences]
    assert kinds == [("mistranscription", "Likert", "like it")]


def test_cut_across_a_boundary_has_low_confidence():
    c = cues("Every project begins with a topic.", "A topic is broad",
             "Each step adds a constraint to the question.",
             "You might use a Likert scale.", "Or you might run interviews instead.")
    a = align(NARR, c)
    b2 = a.boundaries[1]
    assert b2.confidence < 0.5 and "spans the slide break" in b2.reason
    cut = next(d for d in a.divergences if d.kind == "cut")
    assert cut.spans_boundary and cut.boundary_slides == [2]


def test_manual_override_wins():
    c = cues("Every project begins with a topic.", "A topic is broad",
             "Each step adds a constraint to the question.", "You might use a Likert scale.", "Or interviews.")
    a = align(NARR, c, overrides={2: 5.5})
    assert a.boundaries[1].timecode == 5.5 and a.boundaries[1].source == "manual"


def test_paraphrase_is_one_region():
    c = cues("Every project begins with a topic.", "A topic is wide and tricky to research properly.",
             "Think of the process as a funnel.", "Each step adds a constraint to the question.",
             "You might use a Likert scale.", "Or you might run interviews instead.")
    a = align(NARR, c)
    assert [d.kind for d in a.divergences] == ["paraphrase"]


def test_wrong_srt_is_heavy_divergence():
    c = cues("Completely different words about cooking pasta.", "Boil the water and add salt.",
             "Then drain it and serve with sauce.")
    a = align(NARR, c)
    assert a.divergence_ratio > 0.5


def test_sounds_alike():
    assert sounds_alike(["likert"], ["like", "it"]) >= 0.6
    assert sounds_alike(["measure"], ["study"]) < 0.6
