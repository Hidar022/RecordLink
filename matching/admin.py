from django.contrib import admin

from .models import CandidatePair, MatchingJob, ReferenceLabel


class CandidatePairInline(admin.TabularInline):
    model = CandidatePair
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "record_a",
        "record_b",
        "score",
        "classification",
        "review_status",
    )
    readonly_fields = fields


@admin.register(MatchingJob)
class MatchingJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "left_dataset",
        "right_dataset",
        "status",
        "total_candidates",
        "likely_matches",
        "review_candidates",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "left_dataset__name",
        "right_dataset__name",
        "user__username",
    )
    readonly_fields = (
        "status",
        "total_candidates",
        "likely_matches",
        "review_candidates",
        "non_matches",
        "error_message",
        "started_at",
        "finished_at",
    )
    inlines = [CandidatePairInline]


@admin.register(CandidatePair)
class CandidatePairAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "record_a",
        "record_b",
        "score",
        "classification",
        "review_status",
    )
    list_filter = (
        "classification",
        "review_status",
        "created_at",
    )
    search_fields = (
        "job__id",
        "record_a__dataset__name",
        "record_b__dataset__name",
    )
    readonly_fields = (
        "job",
        "record_a",
        "record_b",
        "score",
        "classification",
        "comparison_details",
        "blocking_keys",
        "created_at",
    )


@admin.register(ReferenceLabel)
class ReferenceLabelAdmin(admin.ModelAdmin):
    list_display = (
        "candidate_pair",
        "label",
        "labelled_by",
        "created_at",
        "updated_at",
    )
    list_filter = ("label", "created_at")
    search_fields = ("candidate_pair__job__id", "labelled_by__username")
    readonly_fields = (
        "candidate_pair",
        "label",
        "labelled_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False