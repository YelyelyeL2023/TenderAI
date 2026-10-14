import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import check_leakage


def test_few_shot_examples_are_not_eval_questions():
    cases = check_leakage.eval_cases()
    assert cases, "evaluation sets not found"
    assert check_leakage.few_shot_leaks(cases, check_leakage.few_shot_examples()) == []


def test_eval_identifiers_and_figures_are_not_in_agent_source():
    assert check_leakage.hard_coded(check_leakage.eval_cases()) == []


def test_similarity_flags_a_reworded_question():
    # the kind of example that used to sit in the router prompt
    assert check_leakage.similarity("Көлемі алдыңғы жылдармен салыстырғанда күрт өскен сатып алуларды тап",
                                    "Көлемі алдыңғы жылдармен салыстырғанда әдеттен тыс өскен сатып алуларды тап") >= 0.5
