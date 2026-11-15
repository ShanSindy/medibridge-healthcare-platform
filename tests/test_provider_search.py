from unittest.mock import MagicMock

import provider_service


def test_search_providers_pagination(monkeypatch):
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        (1, "Jane", "Smith", "Cardiology", None, None, None)
    ]
    cursor.fetchone.return_value = (3,)

    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

    monkeypatch.setattr(
        provider_service,
        "get_connection",
        lambda: connection,
    )

    providers, total = provider_service.search_providers(
        count=1,
        offset=1,
    )

    assert len(providers) == 1
    assert providers[0]["first_name"] == "Jane"
    assert total == 3

    calls = cursor.execute.call_args_list
    assert "LIMIT %s OFFSET %s" in calls[0].args[0]
    assert calls[0].args[1] == (1, 1)


def test_search_providers_name_filters(monkeypatch):
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)

    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor

    monkeypatch.setattr(
        provider_service,
        "get_connection",
        lambda: connection,
    )

    providers, total = provider_service.search_providers(
        family="Sm",
        given="Ja",
    )

    assert providers == []
    assert total == 0

    calls = cursor.execute.call_args_list
    assert "last_name ILIKE %s" in calls[0].args[0]
    assert "first_name ILIKE %s" in calls[0].args[0]
    assert calls[0].args[1] == ("Sm%", "Ja%", 10, 0)
    assert calls[1].args[1] == ("Sm%", "Ja%")
