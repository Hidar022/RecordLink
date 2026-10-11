from django import forms
from django.core.exceptions import ValidationError

from .csv_utils import inspect_csv


STANDARD_FIELDS = {
    "full_name": "Full Name",
    "first_name": "First Name",
    "middle_name": "Middle Name",
    "last_name": "Last Name",
    "date_of_birth": "Date of Birth",
    "sex": "Sex / Gender",
    "phone": "Phone Number",
    "email": "Email Address",
    "address": "Address",
    "hospital_record_id": "Hospital Record ID",
    "national_id": "National ID",
}

IDENTITY_FIELDS = {
    "full_name",
    "first_name",
    "last_name",
    "date_of_birth",
    "phone",
    "email",
    "hospital_record_id",
    "national_id",
}


class DatasetUploadForm(forms.Form):
    name = forms.CharField(
        max_length=150,
        label="Dataset name",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. Hospital Patient Register",
            }
        ),
    )

    source_name = forms.CharField(
        max_length=150,
        label="Source or organization",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. General Hospital",
            }
        ),
    )

    description = forms.CharField(
        required=False,
        label="Description",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Describe the dataset and its purpose.",
            }
        ),
    )

    file = forms.FileField(
        label="CSV file",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".csv,text/csv",
            }
        ),
    )

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        self.csv_info = inspect_csv(uploaded_file)
        return uploaded_file


class DatasetMappingForm(forms.Form):
    def __init__(self, *args, headers, **kwargs):
        super().__init__(*args, **kwargs)

        self.headers = headers
        choices = [("", "— Do not map this field —")]
        choices.extend((header, header) for header in headers)

        for field_name, label in STANDARD_FIELDS.items():
            self.fields[field_name] = forms.ChoiceField(
                label=label,
                required=False,
                choices=choices,
                widget=forms.Select(
                    attrs={"class": "form-select"}
                ),
            )

    def clean(self):
        cleaned_data = super().clean()

        selected_headers = [
            cleaned_data.get(field_name)
            for field_name in STANDARD_FIELDS
            if cleaned_data.get(field_name)
        ]

        if len(selected_headers) != len(set(selected_headers)):
            raise ValidationError(
                "Each source column can only be mapped to one standard field."
            )

        mapping = {
            field_name: cleaned_data[field_name]
            for field_name in STANDARD_FIELDS
            if cleaned_data.get(field_name)
        }

        if len(mapping) < 2:
            raise ValidationError(
                "Map at least two standard fields before saving."
            )

        if not IDENTITY_FIELDS.intersection(mapping):
            raise ValidationError(
                "Map at least one identity field, such as a name, "
                "date of birth, phone number, email, or record ID."
            )

        return cleaned_data

    def get_mapping(self):
        return {
            field_name: self.cleaned_data[field_name]
            for field_name in STANDARD_FIELDS
            if self.cleaned_data.get(field_name)
        }