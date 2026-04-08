import pytest
from pydantic import ValidationError

import factory.api_server as api_server
from factory.schemas import (
    BulkArchiveRequest,
    ChatCreateRequest,
    ImprovementReviewRequest,
    QwenFixRequest,
    RunCreateRequest,
    VisionRequest,
    WorkItemCreateRequest,
    WorkItemPatchRequest,
)


def test_api_server_uses_schemas_models():
    assert api_server.WorkItemPatchRequest is WorkItemPatchRequest
    assert api_server.BulkArchiveRequest is BulkArchiveRequest
    assert api_server.ImprovementReviewRequest is ImprovementReviewRequest
    assert api_server.VisionRequest is VisionRequest
    assert api_server.WorkItemCreateRequest is WorkItemCreateRequest
    assert api_server.ChatCreateRequest is ChatCreateRequest
    assert api_server.QwenFixRequest is QwenFixRequest
    assert api_server.RunCreateRequest is RunCreateRequest


def test_schema_defaults_and_validation():
    assert ImprovementReviewRequest().reviewed_by == "dashboard"
    assert WorkItemCreateRequest(title="t").kind == "vision"
    assert ChatCreateRequest(prompt="hello").context == {}

    with pytest.raises(ValidationError):
        VisionRequest(title="")

    with pytest.raises(ValidationError):
        RunCreateRequest(work_item_id="")

    with pytest.raises(ValidationError):
        QwenFixRequest(message="")

    patch = WorkItemPatchRequest(title="new", description="desc")
    assert patch.title == "new"
    assert patch.description == "desc"

    archive = BulkArchiveRequest(ids=["a", "b"], filter="done")
    assert archive.ids == ["a", "b"]
    assert archive.filter == "done"
