from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditEvent
from datasets.models import Dataset


User = get_user_model()


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="recordlink_test",
            password="TestPassword_12345!",
        )

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("login"),
            response.url,
        )

    def test_public_home_shows_landing_page_and_valid_navigation(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/home.html")
        self.assertContains(response, "Discover Duplicate Records.")
        self.assertContains(response, "Build More Reliable Data.")
        self.assertContains(response, f'href="{reverse("login")}"')
        self.assertContains(
            response,
            f'href="{reverse("login")}">Access Workspace',
        )
        self.assertContains(response, 'href="#features"')
        self.assertContains(response, 'href="#how-it-works"')
        self.assertContains(response, 'id="features"')
        self.assertContains(response, 'id="how-it-works"')
        self.assertContains(response, 'id="about"')

    def test_authenticated_home_redirects_to_dashboard(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("dashboard"))

    def test_login_and_protected_dashboard_flow_remains_available(self):
        protected_response = self.client.get(reverse("dashboard"))
        login_response = self.client.get(reverse("login"))

        self.assertRedirects(
            protected_response,
            f'{reverse("login")}?next={reverse("dashboard")}',
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertTemplateUsed(login_response, "registration/login.html")

        login_response = self.client.post(
            reverse("login"),
            {"username": self.user.username, "password": "TestPassword_12345!"},
        )

        self.assertRedirects(login_response, reverse("dashboard"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        logout_response = self.client.post(reverse("logout"))

        self.assertRedirects(logout_response, reverse("login"))
        self.assertRedirects(
            self.client.get(reverse("dashboard")),
            f'{reverse("login")}?next={reverse("dashboard")}',
        )

    def test_authenticated_user_can_access_dashboard(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "RecordLink")

    def test_dashboard_shows_only_current_users_datasets(self):
        other_user = User.objects.create_user(
            username="another_user",
            password="AnotherPassword_123!",
        )

        Dataset.objects.create(
            name="My Dataset",
            source_name="Source A",
            uploaded_by=self.user,
            original_filename="my_dataset.csv",
        )

        Dataset.objects.create(
            name="Another User Dataset",
            source_name="Source B",
            uploaded_by=other_user,
            original_filename="other_dataset.csv",
        )

        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_datasets"], 1)
        self.assertContains(response, "My Dataset")
        self.assertNotContains(response, "Another User Dataset")


class FoundationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="model_test",
            password="TestPassword_12345!",
        )

    def test_dataset_defaults_to_pending(self):
        dataset = Dataset.objects.create(
            name="Test Dataset",
            source_name="Source A",
            uploaded_by=self.user,
            original_filename="test.csv",
        )

        self.assertEqual(
            dataset.status,
            Dataset.Status.PENDING,
        )
        self.assertEqual(dataset.record_count, 0)

    def test_audit_event_is_associated_with_user(self):
        event = AuditEvent.objects.create(
            user=self.user,
            action="dataset.registered",
            description="Dataset registration test",
        )

        self.assertEqual(event.user, self.user)
        self.assertEqual(event.action, "dataset.registered")