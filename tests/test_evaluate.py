from resume_fitter.evaluate import compare_truth_risk, evaluate_bullet


def test_evaluate_bullet_strong_xyz_bullet():
    text = "Improved API performance by 33% refactoring multipart payloads and serving images from alternate endpoints."

    ev = evaluate_bullet(text)

    assert ev.has_action_verb is True
    assert ev.has_metric is True
    assert ev.has_result_clause is True
    assert ev.xyz_score == 1.0


def test_evaluate_bullet_short_heading_scores_low():
    ev = evaluate_bullet("Onchain Team")

    assert ev.has_action_verb is False
    assert ev.has_metric is False
    assert ev.has_result_clause is False
    assert ev.xyz_score == 0.0
    assert ev.verbosity_score < 1.0


def test_evaluate_bullet_skills_list_partial_xyz():
    text = "Utilized: NextJS, Docker/Kubernetes, TailwindCSS, PostgreSQL, SWR, React Hook Form, Zod"

    ev = evaluate_bullet(text)

    assert ev.has_action_verb is True  # "Utilized:" ends in "ed"
    assert ev.has_metric is False
    assert ev.xyz_score == 1 / 3


def test_evaluate_bullet_word_count_and_verbosity_in_range():
    text = "Improved API performance by 33% refactoring multipart payloads and serving images from alternate endpoints."

    ev = evaluate_bullet(text)

    assert ev.word_count == len(text.split())
    assert ev.verbosity_score == 1.0


def test_compare_truth_risk_identical_text_is_low():
    text = "Improved API performance by 33% refactoring multipart payloads."

    result = compare_truth_risk(text, text)

    assert result.truth_risk == "low"
    assert result.changed_entities == []


def test_compare_truth_risk_new_metric_is_high():
    original = "Improved API performance by 33% refactoring multipart payloads."
    candidate = "Improved API performance by 50% refactoring multipart payloads."

    result = compare_truth_risk(original, candidate)

    assert result.truth_risk == "high"
    assert "50%" in result.changed_entities


def test_compare_truth_risk_new_entity_is_medium():
    original = "Improved API performance by 33% refactoring multipart payloads."
    candidate = "Improved API performance by 33% refactoring multipart payloads using Redis."

    result = compare_truth_risk(original, candidate)

    assert result.truth_risk == "medium"
    assert "Redis." in result.changed_entities or "Redis" in result.changed_entities
