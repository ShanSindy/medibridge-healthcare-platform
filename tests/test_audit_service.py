from unittest.mock import MagicMock, patch

import pytest

from audit_service import get_audit_logs


def test_get_audit_logs_returns_records():
    mock_cursor = MagicMock()

    mock_cursor.description = [
        MagicMock(name="id"),
        MagicMock(name="user_id"),
        MagicMock(name="action"),
        MagicMock(name="resource_type"),
        MagicMock(name="resource_id"),
        MagicMock(name="outcome"),
        MagicMock(name="created_at"),
    ]

    columns = [
        "id",
        "user_id",
        "action",
        "resource_type",
        "resource_id",
        "outcome",
        "created_at",
    ]

    for column, description in zip(
        columns,
        mock_cursor.description,
    ):
        description.name = column

    mock_cursor.fetchall.return_value = [
        (
            1,
            5,
            "patient.create",
            "patient",
            "42",
            "success",
            "2026-11-12",
        )
    ]

    mock_connection = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

    with patch("audit_service.get_connection") as mock_get_connection:
        mock_get_connection.return_value.__enter__.return_value = mock_connection

        result = get_audit_logs(limit=10, offset=0)

    assert len(result) == 1
    assert result[0]["action"] == "patient.create"
    assert result[0]["user_id"] == 5

    query, params = mock_cursor.execute.call_args.args

    assert "FROM audit_logs" in query
    assert "ORDER BY created_at DESC, id DESC" in query
    assert params == (10, 0)


@pytest.mark.parametrize(
    "limit,offset",
    [
        (0, 0),
        (101, 0),
        (10, -1),
    ],
)
def test_get_audit_logs_rejects_invalid_pagination(
    limit,
    offset,
):
    with patch("audit_service.get_connection") as mock_connection:
        with pytest.raises(ValueError):
            get_audit_logs(limit=limit, offset=offset)

        mock_connection.assert_not_called()
