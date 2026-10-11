from collections import defaultdict
from datetime import date

from django.db import transaction
from django.utils import timezone
from rapidfuzz.fuzz import ratio

from datasets.models import DatasetRecord
from .models import CandidatePair, MatchingJob


# Higher weights mean a field contributes more to the comparison.
FIELD_WEIGHTS = {
    "hospital_record_id": 1.0,
    "national_id": 1.0,
    "email": 0.95,
    "phone": 0.90,
    "date_of_birth": 0.90,
    "first_name": 0.65,
    "last_name": 0.75,
    "middle_name": 0.30,
    "sex": 0.15,
}


def clean_value(value):
    if value is None:
        return ""

    value = str(value).strip().casefold()

    if value in {"", "none", "null", "n/a", "na", "unknown"}:
        return ""

    return " ".join(value.split())


def normalize_date_for_comparison(value):
    value = clean_value(value)

    if not value:
        return ""

    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return value


def get_comparable_value(field, data):
    value = clean_value(data.get(field))

    if field == "date_of_birth":
        return normalize_date_for_comparison(value)

    return value


def build_blocking_keys(data):
    """Create conservative keys for retrieving promising candidate pairs."""
    keys = set()

    email = clean_value(data.get("email"))
    phone = "".join(
        character for character in clean_value(data.get("phone"))
        if character.isdigit()
    )
    hospital_id = clean_value(data.get("hospital_record_id"))
    national_id = clean_value(data.get("national_id"))
    first_name = clean_value(data.get("first_name"))
    last_name = clean_value(data.get("last_name"))
    dob = normalize_date_for_comparison(data.get("date_of_birth"))

    if email:
        keys.add(("email", email))

    # Require a reasonably long phone value to reduce collisions.
    if len(phone) >= 7:
        keys.add(("phone", phone))

    if hospital_id:
        keys.add(("hospital_record_id", hospital_id))

    if national_id:
        keys.add(("national_id", national_id))

    if dob and last_name:
        keys.add(("dob_last_name", dob, last_name[:4]))

    if dob and first_name and last_name:
        keys.add(("dob_name", dob, first_name[:2], last_name[:2]))

    return keys


def generate_candidate_pairs(left_records, right_records):
    """
    Return distinct candidate pairs using blocking.

    This deliberately avoids an unrestricted Cartesian comparison.
    """
    right_index = defaultdict(list)

    for record in right_records:
        data = record.normalized_data or {}

        for key in build_blocking_keys(data):
            right_index[key].append(record)

    candidates = {}

    for left_record in left_records:
        left_data = left_record.normalized_data or {}
        matching_records = {}

        for key in build_blocking_keys(left_data):
            for right_record in right_index.get(key, []):
                matching_records[right_record.pk] = right_record

        for right_record in matching_records.values():
            candidates[right_record.pk, left_record.pk] = (
                left_record,
                right_record,
            )

    return list(candidates.values())


def compare_records(data_a, data_b):
    """
    Calculate a weighted similarity score from 0 to 1.

    Missing fields are excluded from the denominator rather than treated
    as agreements. This is a heuristic score, not a calibrated probability.
    """
    weighted_score = 0.0
    total_weight = 0.0
    details = {}

    for field, weight in FIELD_WEIGHTS.items():
        value_a = get_comparable_value(field, data_a)
        value_b = get_comparable_value(field, data_b)

        if not value_a or not value_b:
            details[field] = {
                "value_a": value_a,
                "value_b": value_b,
                "similarity": None,
                "weight": weight,
                "note": "Missing value; excluded from score",
            }
            continue

        if field in {
            "hospital_record_id",
            "national_id",
            "email",
            "phone",
            "date_of_birth",
            "sex",
        }:
            similarity = 1.0 if value_a == value_b else 0.0
        else:
            similarity = ratio(value_a, value_b) / 100.0

        weighted_score += similarity * weight
        total_weight += weight

        details[field] = {
            "value_a": value_a,
            "value_b": value_b,
            "similarity": round(similarity, 4),
            "weight": weight,
        }

    score = weighted_score / total_weight if total_weight else 0.0

    return round(score, 4), details


def classify_score(score, job):
    if score >= job.match_threshold:
        return CandidatePair.Classification.LIKELY_MATCH

    if score >= job.review_threshold:
        return CandidatePair.Classification.REVIEW

    return CandidatePair.Classification.NON_MATCH


@transaction.atomic
def execute_matching_job(job):
    """Execute a pending job and persist its candidate comparisons."""
    job = MatchingJob.objects.select_for_update().get(pk=job.pk)

    if job.status == MatchingJob.Status.COMPLETED:
        return job

    if job.status == MatchingJob.Status.RUNNING:
        raise ValueError("This matching job is already running.")

    if job.left_dataset_id == job.right_dataset_id:
        raise ValueError("Choose two different datasets.")

    if (
        job.left_dataset.status != "ready"
        or job.right_dataset.status != "ready"
    ):
        raise ValueError("Both datasets must be ready before matching.")

    job.status = MatchingJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "error_message"])

    try:
        left_records = list(
            DatasetRecord.objects.filter(
                dataset=job.left_dataset,
                validation_status=DatasetRecord.ValidationStatus.VALID,
            ).order_by("pk")
        )
        right_records = list(
            DatasetRecord.objects.filter(
                dataset=job.right_dataset,
                validation_status=DatasetRecord.ValidationStatus.VALID,
            ).order_by("pk")
        )

        candidate_pairs = generate_candidate_pairs(
            left_records,
            right_records,
        )

        pair_objects = []

        for record_a, record_b in candidate_pairs:
            score, details = compare_records(
                record_a.normalized_data or {},
                record_b.normalized_data or {},
            )

            shared_keys = sorted(
                repr(key)
                for key in (
                    build_blocking_keys(record_a.normalized_data or {})
                    & build_blocking_keys(record_b.normalized_data or {})
                )
            )

            pair_objects.append(
                CandidatePair(
                    job=job,
                    record_a=record_a,
                    record_b=record_b,
                    score=score,
                    classification=classify_score(score, job),
                    comparison_details=details,
                    blocking_keys=shared_keys,
                )
            )

        CandidatePair.objects.bulk_create(
            pair_objects,
            batch_size=500,
        )

        job.total_candidates = len(pair_objects)
        job.likely_matches = sum(
            pair.classification == CandidatePair.Classification.LIKELY_MATCH
            for pair in pair_objects
        )
        job.review_candidates = sum(
            pair.classification == CandidatePair.Classification.REVIEW
            for pair in pair_objects
        )
        job.non_matches = sum(
            pair.classification == CandidatePair.Classification.NON_MATCH
            for pair in pair_objects
        )
        job.status = MatchingJob.Status.COMPLETED
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "total_candidates",
                "likely_matches",
                "review_candidates",
                "non_matches",
                "status",
                "finished_at",
            ]
        )

    except Exception as exc:
        CandidatePair.objects.filter(job=job).delete()

        job.total_candidates = 0
        job.likely_matches = 0
        job.review_candidates = 0
        job.non_matches = 0
        job.status = MatchingJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "total_candidates",
                "likely_matches",
                "review_candidates",
                "non_matches",
                "status",
                "error_message",
                "finished_at",
            ]
        )

    return job