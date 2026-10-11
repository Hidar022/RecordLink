from django.conf import settings
from django.db import models

from datasets.models import Dataset, DatasetRecord


class MatchingJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="matching_jobs",
    )
    left_dataset = models.ForeignKey(
        Dataset,
        on_delete=models.PROTECT,
        related_name="left_matching_jobs",
    )
    right_dataset = models.ForeignKey(
        Dataset,
        on_delete=models.PROTECT,
        related_name="right_matching_jobs",
    )

    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )

    # Scores are between 0 and 1. These are initial heuristic thresholds.
    match_threshold = models.FloatField(default=0.82)
    review_threshold = models.FloatField(default=0.55)

    configuration = models.JSONField(default=dict, blank=True)
    total_candidates = models.PositiveIntegerField(default=0)
    likely_matches = models.PositiveIntegerField(default=0)
    review_candidates = models.PositiveIntegerField(default=0)
    non_matches = models.PositiveIntegerField(default=0)

    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(left_dataset=models.F("right_dataset")),
                name="matching_job_datasets_different",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(match_threshold__gte=0)
                    & models.Q(match_threshold__lte=1)
                    & models.Q(review_threshold__gte=0)
                    & models.Q(review_threshold__lte=1)
                    & models.Q(match_threshold__gt=models.F("review_threshold"))
                ),
                name="matching_job_thresholds_valid",
            ),
        ]

    def __str__(self):
        return f"Job #{self.pk}: {self.left_dataset} vs {self.right_dataset}"


class CandidatePair(models.Model):
    class Classification(models.TextChoices):
        LIKELY_MATCH = "likely_match", "Likely match"
        REVIEW = "review", "Needs review"
        NON_MATCH = "non_match", "Non-match"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending review"
        CONFIRMED_MATCH = "confirmed_match", "Confirmed match"
        CONFIRMED_NON_MATCH = "confirmed_non_match", "Confirmed non-match"

    job = models.ForeignKey(
        MatchingJob,
        on_delete=models.CASCADE,
        related_name="candidate_pairs",
    )
    record_a = models.ForeignKey(
        DatasetRecord,
        on_delete=models.PROTECT,
        related_name="candidate_pairs_as_a",
    )
    record_b = models.ForeignKey(
        DatasetRecord,
        on_delete=models.PROTECT,
        related_name="candidate_pairs_as_b",
    )

    score = models.FloatField()
    classification = models.CharField(
        max_length=20,
        choices=Classification.choices,
    )
    review_status = models.CharField(
        max_length=24,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )

    comparison_details = models.JSONField(default=dict, blank=True)
    blocking_keys = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_candidate_pairs",
    )

    class Meta:
        ordering = ["-score", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["job", "record_a", "record_b"],
                name="unique_candidate_pair_per_job",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(score__gte=0)
                    & models.Q(score__lte=1)
                ),
                name="candidate_pair_score_between_zero_and_one",
            ),
        ]
        indexes = [
            models.Index(fields=["job", "classification", "score"]),
            models.Index(fields=["review_status", "created_at"]),
        ]

    def __str__(self):
        return f"Job #{self.job_id}: {self.record_a_id} ↔ {self.record_b_id}"


class ReferenceLabel(models.Model):
    class Label(models.TextChoices):
        TRUE_MATCH = "true_match", "True match"
        TRUE_NON_MATCH = "true_non_match", "True non-match"

    candidate_pair = models.OneToOneField(
        CandidatePair,
        on_delete=models.CASCADE,
        related_name="reference_label",
    )
    label = models.CharField(max_length=16, choices=Label.choices)
    labelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reference_labels",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Candidate #{self.candidate_pair_id}: {self.get_label_display()}"