"""Explicitly retry a frozen review request after checking its files again."""

from copy import deepcopy

from review_commit_transport import commit_review
from review_pending import get_pending_review
from review_sources import open_review_source
from review_storage import load_processing_result
from review_validation import prepare_review_draft


def retry_review(root, project_id, session, operation_id):
    """Recheck original and result before sending the identical stored request.

    Pending records remain available after every outcome, including success.
    The caller must provide a trusted session and project from the application.
    """
    pending = get_pending_review(root, project_id, session, operation_id)
    snapshot = pending.snapshot
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("source_ref"), dict) or "processing" not in snapshot:
        raise ValueError("재시도할 검토 기록의 필수 필드가 없습니다.")
    source, _ = open_review_source(project_id, session, root, snapshot["source_ref"])
    processing = load_processing_result(root, session, source, snapshot["processing"])
    draft = deepcopy(snapshot)
    draft["processing"] = processing
    prepare_review_draft(project_id, session, root, draft)
    return commit_review(project_id, session, snapshot, pending.expected_version,
                         pending.operation_id, pending.revision)
