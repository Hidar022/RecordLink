from .models import CandidatePair, ReferenceLabel


def calculate_evaluation_metrics(labelled_pairs):
    """Measure binary predictions on independently labelled candidates.

    ``likely_match`` is treated as a positive prediction. ``review`` and
    ``non_match`` are treated as not predicted positive. The population is
    limited to candidate pairs with a supplied reference label.
    """
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    true_negatives = 0
    labelled_count = 0

    for classification, label in labelled_pairs:
        labelled_count += 1
        predicted_positive = (
            classification == CandidatePair.Classification.LIKELY_MATCH
        )
        actual_positive = label == ReferenceLabel.Label.TRUE_MATCH

        if predicted_positive and actual_positive:
            true_positives += 1
        elif predicted_positive:
            false_positives += 1
        elif actual_positive:
            false_negatives += 1
        else:
            true_negatives += 1

    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    f1_denominator = 2 * true_positives + false_positives + false_negatives

    precision = (
        true_positives / precision_denominator
        if precision_denominator
        else None
    )
    recall = (
        true_positives / recall_denominator
        if recall_denominator
        else None
    )
    f1_score = (
        2 * true_positives / f1_denominator
        if f1_denominator
        else None
    )

    return {
        "labelled_pairs": labelled_count,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_negatives": true_negatives,
        "precision": precision,
        "recall": recall,
        "f1_score": f1_score,
    }