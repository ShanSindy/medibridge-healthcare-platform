from unittest.mock import MagicMock

import provider_service


def mock_database(monkeypatch, rows=None, total=0):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows if rows is not None else []
    cursor.fetchone.return_value = (total,)

    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

    monkeypatch.setattr(
        provider_service,
        "get_connection",
        lambda: connection,
    )

    return cursor


def test_search_providers_specialty_filter(monkeypatch):
    cursor = mock_database(
        monkeypatch,
        rows=[(7, "Jane", "Smith", "Cardiology", None, None, None)],
        total=1,
    )

    providers, total = provider_service.search_providers(
        specialty="Card",
    )

    assert total == 1
    assert providers[0]["specialty"] == "Cardiology"

    calls = cursor.execute.call_args_list
    assert "specialty ILIKE %s" in calls[0].args[0]
    assert calls[0].args[1] == ("Card%", 10, 0)
    assert calls[1].args[1] == ("Card%",)


def test_search_providers_combined_filters_and_pagination(monkeypatch):
    cursor = mock_database(monkeypatch)

    provider_service.search_providers(
        family="Sm",
        given="Ja",
        specialty="Card",
        count=5,
        offset=10,
    )

    calls = cursor.execute.call_args_list
    query = calls[0].args[0]

    assert "last_name ILIKE %s" in query
    assert "first_name ILIKE %s" in query
    assert "specialty ILIKE %s" in query
    assert calls[0].args[1] == ("Sm%", "Ja%", "Card%", 5, 10)
    assert calls[1].args[1] == ("Sm%", "Ja%", "Card%")


def test_search_providers_escapes_specialty_wildcards(monkeypatch):
    cursor = mock_database(monkeypatch)

    provider_service.search_providers(
        specialty="Card_%",
    )

    calls = cursor.execute.call_args_list

    assert calls[0].args[1] == (r"Card\_\%%", 10, 0)
    assert calls[1].args[1] == (r"Card\_\%%",)
