from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Dataset, DatasetImport, DatasetRecord
from .record_processing import process_dataset_import

User = get_user_model()


class DatasetUploadTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="recordlink_tester",
            password="test-password-123",
        )
        self.client.force_login(self.user)

    def make_csv(self, content, name="patients.csv"):
        return SimpleUploadedFile(
            name,
            content.encode("utf-8"),
            content_type="text/csv",
        )

    def test_upload_creates_dataset_and_import(self):
        response = self.client.post(
            reverse("datasets:dataset_upload"),
            {
                "name": "Hospital Patient Register",
                "source_name": "General Hospital",
                "description": "Synthetic patient records.",
                "file": self.make_csv(
                    "first_name,last_name,date_of_birth\n"
                    "Amina,Yusuf,1990-05-12\n"
                    "Musa,Ibrahim,1985-02-21\n"
                ),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Dataset.objects.count(), 1)
        self.assertEqual(DatasetImport.objects.count(), 1)

        dataset = Dataset.objects.get()
        dataset_import = DatasetImport.objects.get()

        self.assertEqual(dataset.uploaded_by, self.user)
        self.assertEqual(dataset.record_count, 0)
        self.assertEqual(
            dataset_import.status,
            DatasetImport.Status.PREVIEWED,
        )
        self.assertEqual(dataset_import.total_rows, 2)
        self.assertEqual(
            dataset_import.validation_summary["headers"],
            ["first_name", "last_name", "date_of_birth"],
        )

        self.assertEqual(
            response.url,
            reverse(
                "datasets:dataset_preview",
                kwargs={"import_id": dataset_import.pk},
            ),
        )

    def test_preview_displays_csv_headers_and_rows(self):
        self.client.post(
            reverse("datasets:dataset_upload"),
            {
                "name": "Hospital Patient Register",
                "source_name": "General Hospital",
                "description": "",
                "file": self.make_csv(
                    "first_name,last_name\n"
                    "Amina,Yusuf\n"
                    "Musa,Ibrahim\n"
                ),
            },
        )

        dataset_import = DatasetImport.objects.get()

        response = self.client.get(
            reverse(
                "datasets:dataset_preview",
                kwargs={"import_id": dataset_import.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "first_name")
        self.assertContains(response, "Amina")
        self.assertContains(response, "Musa")

    def test_non_csv_file_is_rejected(self):
        response = self.client.post(
            reverse("datasets:dataset_upload"),
            {
                "name": "Invalid Dataset",
                "source_name": "General Hospital",
                "description": "",
                "file": SimpleUploadedFile(
                    "patients.txt",
                    b"first_name,last_name\nAmina,Yusuf\n",
                    content_type="text/plain",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Dataset.objects.count(), 0)
        self.assertEqual(DatasetImport.objects.count(), 0)
        self.assertTrue(response.context["form"].errors)

    def test_user_cannot_view_another_users_import(self):
        other_user = User.objects.create_user(
            username="another_tester",
            password="another-password-123",
        )

        other_dataset = Dataset.objects.create(
            name="Private Dataset",
            source_name="Private Hospital",
            uploaded_by=other_user,
            original_filename="private.csv",
        )

        dataset_import = DatasetImport.objects.create(
            dataset=other_dataset,
            uploaded_by=other_user,
            source_file=self.make_csv(
                "first_name,last_name\n"
                "Private,Patient\n"
            ),
            original_filename="private.csv",
            status=DatasetImport.Status.PREVIEWED,
        )

        response = self.client.get(
            reverse(
                "datasets:dataset_preview",
                kwargs={"import_id": dataset_import.pk},
            )
        )

        self.assertEqual(response.status_code, 404)


    def test_column_mapping_is_saved(self):
        self.client.post(
            reverse("datasets:dataset_upload"),
            {
                "name": "Hospital Patient Register",
                "source_name": "General Hospital",
                "description": "",
                "file": self.make_csv(
                    "first_name,last_name,date_of_birth\n"
                    "Amina,Yusuf,1990-05-12\n"
                ),
            },
        )

        dataset_import = DatasetImport.objects.get()

        response = self.client.post(
            reverse(
                "datasets:dataset_preview",
                kwargs={"import_id": dataset_import.pk},
            ),
            {
                "first_name": "first_name",
                "last_name": "last_name",
                "date_of_birth": "date_of_birth",
            },
        )

        self.assertEqual(response.status_code, 302)

        dataset_import.refresh_from_db()

        self.assertEqual(
            dataset_import.status,
            DatasetImport.Status.MAPPED,
        )
        self.assertEqual(
            dataset_import.column_mapping,
            {
                "first_name": "first_name",
                "last_name": "last_name",
                "date_of_birth": "date_of_birth",
            },
        )

    def test_duplicate_source_column_mapping_is_rejected(self):
        self.client.post(
            reverse("datasets:dataset_upload"),
            {
                "name": "Hospital Patient Register",
                "source_name": "General Hospital",
                "description": "",
                "file": self.make_csv(
                    "first_name,last_name,date_of_birth\n"
                    "Amina,Yusuf,1990-05-12\n"
                ),
            },
        )

        dataset_import = DatasetImport.objects.get()

        response = self.client.post(
            reverse(
                "datasets:dataset_preview",
                kwargs={"import_id": dataset_import.pk},
            ),
            {
                "first_name": "first_name",
                "last_name": "first_name",
                "date_of_birth": "date_of_birth",
            },
        )

        self.assertEqual(response.status_code, 200)

        dataset_import.refresh_from_db()

        self.assertNotEqual(
            dataset_import.status,
            DatasetImport.Status.MAPPED,
        )
        self.assertTrue(
            response.context["mapping_form"].non_field_errors()
        )
        

class DatasetRecordProcessingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="recordlink_processor",
            password="test-password-123",
        )

        self.dataset = Dataset.objects.create(
            name="Hospital Patient Records",
            source_name="Test Hospital",
            uploaded_by=self.user,
            status=Dataset.Status.PENDING,
        )

        csv_content = (
            "first_name,last_name,date_of_birth,email,hospital_record_id\n"
            "Amina,Musa,1995-04-12,AMINA@EXAMPLE.COM,HSP001\n"
            "Ibrahim,Bello,not-a-date,ibrahim@example.com,HSP002\n"
            "Fatima,Ali,1990-05-10,fatima@example.com\n"
        )

        uploaded_file = SimpleUploadedFile(
            "patients.csv",
            csv_content.encode("utf-8"),
            content_type="text/csv",
        )

        self.dataset_import = DatasetImport.objects.create(
            dataset=self.dataset,
            uploaded_by=self.user,
            source_file=uploaded_file,
            original_filename="patients.csv",
            status=DatasetImport.Status.MAPPED,
            column_mapping={
                "first_name": "first_name",
                "last_name": "last_name",
                "date_of_birth": "date_of_birth",
                "email": "email",
                "hospital_record_id": "hospital_record_id",
            },
            total_rows=3,
        )

    def test_processing_saves_valid_and_invalid_records(self):
        processed_import = process_dataset_import(
            self.dataset_import.pk,
            self.user,
        )

        self.assertEqual(
            processed_import.status,
            DatasetImport.Status.COMPLETED_WITH_ERRORS,
        )
        self.assertEqual(processed_import.processed_rows, 3)
        self.assertEqual(processed_import.accepted_rows, 1)
        self.assertEqual(processed_import.rejected_rows, 2)

        self.assertEqual(
            DatasetRecord.objects.filter(
                dataset_import=processed_import
            ).count(),
            3,
        )

        valid_record = DatasetRecord.objects.get(
            dataset_import=processed_import,
            source_row_number=2,
        )

        self.assertEqual(
            valid_record.validation_status,
            DatasetRecord.ValidationStatus.VALID,
        )
        self.assertEqual(
            valid_record.normalized_data["first_name"],
            "amina",
        )
        self.assertEqual(
            valid_record.normalized_data["email"],
            "amina@example.com",
        )

        invalid_date_record = DatasetRecord.objects.get(
            dataset_import=processed_import,
            source_row_number=3,
        )

        self.assertEqual(
            invalid_date_record.validation_status,
            DatasetRecord.ValidationStatus.INVALID,
        )
        self.assertTrue(invalid_date_record.validation_errors)

        malformed_record = DatasetRecord.objects.get(
            dataset_import=processed_import,
            source_row_number=4,
        )

        self.assertEqual(
            malformed_record.validation_status,
            DatasetRecord.ValidationStatus.INVALID,
        )
        self.assertTrue(malformed_record.validation_errors)

        # The original data must remain unchanged.
        self.assertEqual(
            valid_record.original_data["first_name"],
            "Amina",
        )
        self.assertEqual(
            valid_record.original_data["email"],
            "AMINA@EXAMPLE.COM",
        )

        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.status, Dataset.Status.READY)
        self.assertEqual(self.dataset.record_count, 1)

    def test_reprocessing_does_not_duplicate_records(self):
        process_dataset_import(
            self.dataset_import.pk,
            self.user,
        )

        process_dataset_import(
            self.dataset_import.pk,
            self.user,
        )

        self.assertEqual(
            DatasetRecord.objects.filter(
                dataset_import=self.dataset_import
            ).count(),
            3,
        )

    def test_import_requires_a_saved_mapping(self):
        self.dataset_import.column_mapping = {}
        self.dataset_import.save(update_fields=["column_mapping"])

        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            process_dataset_import(
                self.dataset_import.pk,
                self.user,
            )

        self.dataset_import.refresh_from_db()

        self.assertEqual(
            self.dataset_import.status,
            DatasetImport.Status.MAPPED,
        )        