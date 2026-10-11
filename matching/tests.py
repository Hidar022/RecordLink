import csv
import io

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django.urls import reverse

from datasets.models import Dataset, DatasetImport, DatasetRecord
from audit.models import AuditEvent
from .engine import (
    build_blocking_keys,
    classify_score,
    compare_records,
    execute_matching_job,
    generate_candidate_pairs,
)
from .evaluation import calculate_evaluation_metrics
from .models import CandidatePair, MatchingJob, ReferenceLabel


User = get_user_model()


class EvaluationMetricTests(SimpleTestCase):
    def test_metrics_use_independent_labels(self):
        metrics = calculate_evaluation_metrics([
            (
                CandidatePair.Classification.LIKELY_MATCH,
                ReferenceLabel.Label.TRUE_MATCH,
            ),
            (
                CandidatePair.Classification.LIKELY_MATCH,
                ReferenceLabel.Label.TRUE_NON_MATCH,
            ),
            (
                CandidatePair.Classification.REVIEW,
                ReferenceLabel.Label.TRUE_MATCH,
            ),
            (
                CandidatePair.Classification.NON_MATCH,
                ReferenceLabel.Label.TRUE_NON_MATCH,
            ),
        ])

        self.assertEqual(metrics["labelled_pairs"], 4)
        self.assertEqual(metrics["true_positives"], 1)
        self.assertEqual(metrics["false_positives"], 1)
        self.assertEqual(metrics["false_negatives"], 1)
        self.assertEqual(metrics["true_negatives"], 1)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["f1_score"], 0.5)

    def test_zero_denominators_are_unavailable_or_zero(self):
        empty_metrics = calculate_evaluation_metrics([])
        self.assertIsNone(empty_metrics["precision"])
        self.assertIsNone(empty_metrics["recall"])
        self.assertIsNone(empty_metrics["f1_score"])

        no_positive_predictions = calculate_evaluation_metrics([
            (
                CandidatePair.Classification.NON_MATCH,
                ReferenceLabel.Label.TRUE_MATCH,
            ),
        ])
        self.assertIsNone(no_positive_predictions["precision"])
        self.assertEqual(no_positive_predictions["recall"], 0)
        self.assertEqual(no_positive_predictions["f1_score"], 0)


class MatchingEngineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="linkage_tester",
            password="test-password-123",
        )

        self.dataset_a = Dataset.objects.create(
            name="Hospital A",
            source_name="Hospital A",
            uploaded_by=self.user,
            status=Dataset.Status.READY,
        )
        self.dataset_b = Dataset.objects.create(
            name="Hospital B",
            source_name="Hospital B",
            uploaded_by=self.user,
            status=Dataset.Status.READY,
        )

        self.import_a = DatasetImport.objects.create(
            dataset=self.dataset_a,
            uploaded_by=self.user,
            source_file="datasets/imports/test-a.csv",
            original_filename="test-a.csv",
            status=DatasetImport.Status.COMPLETED,
        )
        self.import_b = DatasetImport.objects.create(
            dataset=self.dataset_b,
            uploaded_by=self.user,
            source_file="datasets/imports/test-b.csv",
            original_filename="test-b.csv",
            status=DatasetImport.Status.COMPLETED,
        )

    def make_record(self, dataset, dataset_import, row, data, valid=True):
        return DatasetRecord.objects.create(
            dataset=dataset,
            dataset_import=dataset_import,
            source_row_number=row,
            original_data=data,
            normalized_data=data,
            validation_status=(
                DatasetRecord.ValidationStatus.VALID
                if valid
                else DatasetRecord.ValidationStatus.INVALID
            ),
        )

    def make_job(self):
        return MatchingJob.objects.create(
            user=self.user,
            left_dataset=self.dataset_a,
            right_dataset=self.dataset_b,
        )

    def test_blocking_uses_shared_email(self):
        keys_a = build_blocking_keys({"email": "Person@example.com"})
        keys_b = build_blocking_keys({"email": "person@example.com"})

        self.assertIn(("email", "person@example.com"), keys_a)
        self.assertEqual(keys_a, keys_b)

    def test_matching_names_and_date_produces_high_score(self):
        score, details = compare_records(
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
            },
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
            },
        )

        self.assertGreaterEqual(score, 0.82)
        self.assertEqual(details["date_of_birth"]["similarity"], 1.0)

    def test_different_dates_reduce_similarity(self):
        score, _details = compare_records(
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
            },
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1980-05-10",
            },
        )

        self.assertLess(score, 1.0)

    def test_missing_fields_are_not_treated_as_agreements(self):
        score, details = compare_records(
            {"first_name": "Amina"},
            {"first_name": "Amina"},
        )

        self.assertEqual(score, 1.0)
        self.assertIsNone(details["date_of_birth"]["similarity"])

    def test_classification_respects_thresholds(self):
        job = self.make_job()

        self.assertEqual(
            classify_score(0.90, job),
            CandidatePair.Classification.LIKELY_MATCH,
        )
        self.assertEqual(
            classify_score(0.65, job),
            CandidatePair.Classification.REVIEW,
        )
        self.assertEqual(
            classify_score(0.30, job),
            CandidatePair.Classification.NON_MATCH,
        )

    def test_candidate_generation_uses_blocking(self):
        record_a = self.make_record(
            self.dataset_a,
            self.import_a,
            2,
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
            },
        )
        record_b = self.make_record(
            self.dataset_b,
            self.import_b,
            2,
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
            },
        )

        candidates = generate_candidate_pairs([record_a], [record_b])

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0], (record_a, record_b))

    def test_invalid_records_are_excluded_from_execution(self):
        self.make_record(
            self.dataset_a,
            self.import_a,
            2,
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
            },
        )
        self.make_record(
            self.dataset_b,
            self.import_b,
            2,
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
            },
            valid=False,
        )

        job = self.make_job()
        execute_matching_job(job)

        job.refresh_from_db()
        self.assertEqual(job.status, MatchingJob.Status.COMPLETED)
        self.assertEqual(job.total_candidates, 0)

    def test_execution_persists_candidate_and_summary(self):
        self.make_record(
            self.dataset_a,
            self.import_a,
            2,
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
                "email": "amina@example.com",
            },
        )
        self.make_record(
            self.dataset_b,
            self.import_b,
            2,
            {
                "first_name": "Amina",
                "last_name": "Ibrahim",
                "date_of_birth": "1998-05-10",
                "email": "amina@example.com",
            },
        )

        job = self.make_job()
        execute_matching_job(job)

        job.refresh_from_db()
        self.assertEqual(job.status, MatchingJob.Status.COMPLETED)
        self.assertEqual(job.total_candidates, 1)
        self.assertEqual(job.likely_matches, 1)
        self.assertEqual(job.candidate_pairs.count(), 1)

    def test_user_cannot_view_another_users_job(self):
        job = self.make_job()
        other_user = User.objects.create_user(
            username="another_user",
            password="test-password-456",
        )

        self.client.force_login(other_user)

        response = self.client.get(
            reverse("matching:job_detail", args=[job.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_job_list_requires_login(self):
        response = self.client.get(reverse("matching:job_list"))

        self.assertEqual(response.status_code, 302)


class PhaseFourWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="phase_four_user",
            password="test-password-123",
        )
        self.other_user = User.objects.create_user(
            username="phase_four_other",
            password="test-password-456",
        )
        self.dataset_a = Dataset.objects.create(
            name="Source A",
            source_name="Source A",
            uploaded_by=self.user,
            status=Dataset.Status.READY,
        )
        self.dataset_b = Dataset.objects.create(
            name="Source B",
            source_name="Source B",
            uploaded_by=self.user,
            status=Dataset.Status.READY,
        )
        self.import_a = DatasetImport.objects.create(
            dataset=self.dataset_a,
            uploaded_by=self.user,
            source_file="datasets/imports/source-a.csv",
            original_filename="source-a.csv",
            status=DatasetImport.Status.COMPLETED,
        )
        self.import_b = DatasetImport.objects.create(
            dataset=self.dataset_b,
            uploaded_by=self.user,
            source_file="datasets/imports/source-b.csv",
            original_filename="source-b.csv",
            status=DatasetImport.Status.COMPLETED,
        )

    def make_job(self, user=None):
        return MatchingJob.objects.create(
            user=user or self.user,
            left_dataset=self.dataset_a,
            right_dataset=self.dataset_b,
        )

    def make_pair(self, job=None, classification=None, row_offset=0):
        record_a = DatasetRecord.objects.create(
            dataset=self.dataset_a,
            dataset_import=self.import_a,
            source_row_number=2 + row_offset,
            original_data={"first_name": "Amina"},
            normalized_data={"first_name": "Amina"},
        )
        record_b = DatasetRecord.objects.create(
            dataset=self.dataset_b,
            dataset_import=self.import_b,
            source_row_number=3 + row_offset,
            original_data={"first_name": "Amina"},
            normalized_data={"first_name": "Amina"},
        )
        return CandidatePair.objects.create(
            job=job or self.make_job(),
            record_a=record_a,
            record_b=record_b,
            score=0.91,
            classification=(
                classification or CandidatePair.Classification.LIKELY_MATCH
            ),
            comparison_details={
                "first_name": {
                    "value_a": "Amina",
                    "value_b": "Amina",
                    "similarity": 1.0,
                },
                "date_of_birth": {
                    "value_a": "",
                    "value_b": "",
                    "similarity": None,
                    "note": "Missing value; excluded from score",
                },
            },
        )

    def test_anonymous_user_cannot_access_review_queue(self):
        response = self.client.get(reverse("matching:review_queue"))
        self.assertEqual(response.status_code, 302)

    def test_review_queue_and_pair_detail_are_scoped_to_owner(self):
        pair = self.make_pair(job=self.make_job(user=self.other_user))
        self.client.force_login(self.user)

        queue_response = self.client.get(reverse("matching:review_queue"))
        detail_response = self.client.get(
            reverse(
                "matching:candidate_detail",
                args=[pair.job_id, pair.pk],
            )
        )

        self.assertEqual(queue_response.status_code, 200)
        self.assertFalse(queue_response.context["page_obj"].object_list)
        self.assertEqual(detail_response.status_code, 404)

    def test_owner_can_confirm_match_and_audit_event_is_created(self):
        pair = self.make_pair()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("matching:review_decision", args=[pair.pk]),
            {"decision": CandidatePair.ReviewStatus.CONFIRMED_MATCH},
        )

        pair.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            pair.review_status,
            CandidatePair.ReviewStatus.CONFIRMED_MATCH,
        )
        self.assertEqual(
            pair.classification,
            CandidatePair.Classification.LIKELY_MATCH,
        )
        self.assertEqual(pair.reviewed_by, self.user)
        self.assertIsNotNone(pair.reviewed_at)
        self.assertTrue(AuditEvent.objects.filter(
            user=self.user,
            action="candidate_pair_confirmed_match",
            entity_id=str(pair.pk),
        ).exists())

    def test_owner_can_confirm_non_match(self):
        pair = self.make_pair()
        self.client.force_login(self.user)

        self.client.post(
            reverse("matching:review_decision", args=[pair.pk]),
            {"decision": CandidatePair.ReviewStatus.CONFIRMED_NON_MATCH},
        )

        pair.refresh_from_db()
        self.assertEqual(
            pair.review_status,
            CandidatePair.ReviewStatus.CONFIRMED_NON_MATCH,
        )
        self.assertEqual(
            pair.classification,
            CandidatePair.Classification.LIKELY_MATCH,
        )
        self.assertTrue(AuditEvent.objects.filter(
            action="candidate_pair_confirmed_non_match",
            entity_id=str(pair.pk),
        ).exists())

    def test_invalid_decision_is_rejected_without_mutation(self):
        pair = self.make_pair()
        self.client.force_login(self.user)

        self.client.post(
            reverse("matching:review_decision", args=[pair.pk]),
            {"decision": "maybe"},
        )

        pair.refresh_from_db()
        self.assertEqual(pair.review_status, CandidatePair.ReviewStatus.PENDING)
        self.assertIsNone(pair.reviewed_by)
        self.assertFalse(AuditEvent.objects.filter(
            entity_type="candidate_pair",
            entity_id=str(pair.pk),
        ).exists())

    def test_get_does_not_change_review_decision(self):
        pair = self.make_pair()
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("matching:review_decision", args=[pair.pk])
        )

        pair.refresh_from_db()
        self.assertEqual(response.status_code, 405)
        self.assertEqual(pair.review_status, CandidatePair.ReviewStatus.PENDING)
        self.assertIsNone(pair.reviewed_at)

    def test_forged_pair_id_cannot_submit_another_users_decision(self):
        pair = self.make_pair(job=self.make_job(user=self.other_user))
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("matching:review_decision", args=[pair.pk]),
            {"decision": CandidatePair.ReviewStatus.CONFIRMED_MATCH},
        )

        pair.refresh_from_db()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(pair.review_status, CandidatePair.ReviewStatus.PENDING)

    def test_repeated_decision_is_safe_and_does_not_duplicate_audit(self):
        pair = self.make_pair()
        self.client.force_login(self.user)
        url = reverse("matching:review_decision", args=[pair.pk])
        data = {"decision": CandidatePair.ReviewStatus.CONFIRMED_MATCH}

        self.client.post(url, data)
        pair.refresh_from_db()
        reviewed_at = pair.reviewed_at
        self.client.post(url, data)

        pair.refresh_from_db()
        self.assertEqual(pair.reviewed_at, reviewed_at)
        self.assertEqual(
            AuditEvent.objects.filter(
                action="candidate_pair_confirmed_match",
                entity_id=str(pair.pk),
            ).count(),
            1,
        )

    def test_user_cannot_export_another_users_job(self):
        job = self.make_job(user=self.other_user)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("matching:export_job_csv", args=[job.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_csv_export_contains_expected_columns_and_candidate_data(self):
        job = self.make_job()
        pair = self.make_pair(job=job)
        pair.review_status = CandidatePair.ReviewStatus.CONFIRMED_MATCH
        pair.reviewed_by = self.user
        pair.reviewed_at = timezone.now()
        pair.save()
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("matching:export_job_csv", args=[job.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(f'matching-job-{job.pk}-candidates.csv', response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))
        self.assertEqual(rows[0], [
            "job_id", "left_dataset", "right_dataset", "record_a_id",
            "record_a_row", "record_b_id", "record_b_row", "score",
            "classification", "review_status", "reviewed_by", "reviewed_at",
            "comparison_details",
        ])
        self.assertEqual(rows[1][0], str(job.pk))
        self.assertEqual(rows[1][3], str(pair.record_a_id))
        self.assertEqual(rows[1][4], "2")
        self.assertEqual(rows[1][9], CandidatePair.ReviewStatus.CONFIRMED_MATCH)
        self.assertEqual(rows[1][10], self.user.get_username())

    def test_csv_export_neutralizes_spreadsheet_formulas(self):
        job = self.make_job()
        job.left_dataset.name = '=HYPERLINK("https://example.invalid","x")'
        job.left_dataset.save(update_fields=["name"])
        self.make_pair(job=job)
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("matching:export_job_csv", args=[job.pk])
        )
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8"))))

        self.assertEqual(rows[1][1], "'=HYPERLINK(\"https://example.invalid\",\"x\")")

    def test_reference_label_does_not_change_prediction_or_review_status(self):
        pair = self.make_pair()
        original_classification = pair.classification
        self.client.force_login(self.user)

        response = self.client.post(
            reverse(
                "matching:save_reference_label",
                args=[pair.job_id, pair.pk],
            ),
            {"label": ReferenceLabel.Label.TRUE_NON_MATCH},
        )

        pair.refresh_from_db()
        label = ReferenceLabel.objects.get(candidate_pair=pair)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(pair.classification, original_classification)
        self.assertEqual(pair.review_status, CandidatePair.ReviewStatus.PENDING)
        self.assertEqual(label.label, ReferenceLabel.Label.TRUE_NON_MATCH)
        self.assertEqual(label.labelled_by, self.user)

    def test_dashboard_statistics_only_include_current_users_data(self):
        own_pair = self.make_pair()
        own_pair.review_status = CandidatePair.ReviewStatus.CONFIRMED_MATCH
        own_pair.save(update_fields=["review_status"])
        other_job = self.make_job(user=self.other_user)
        self.make_pair(job=other_job, row_offset=10)
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_jobs"], 1)
        self.assertEqual(response.context["completed_jobs"], 0)
        self.assertEqual(response.context["pending_review_pairs"], 0)
        self.assertEqual(response.context["reviewed_pairs"], 1)

    def test_audit_history_is_private_to_current_user(self):
        AuditEvent.objects.create(
            user=self.user,
            action="own_event",
            entity_type="matching_job",
            entity_id="1",
        )
        AuditEvent.objects.create(
            user=self.other_user,
            action="other_event",
            entity_type="matching_job",
            entity_id="2",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("audit:history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "own_event")
        self.assertNotContains(response, "other_event")