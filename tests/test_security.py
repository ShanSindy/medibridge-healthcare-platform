from security import hash_password, verify_password


def test_hash_password_does_not_store_plaintext():
    password = "DemoPassword123!"

    password_hash = hash_password(password)

    assert password_hash != password
    assert password_hash.startswith("$argon2id$")


def test_verify_password_accepts_correct_password():
    password = "DemoPassword123!"
    password_hash = hash_password(password)

    assert verify_password(password, password_hash) is True


def test_verify_password_rejects_incorrect_password():
    password_hash = hash_password("DemoPassword123!")

    assert verify_password("WrongPassword", password_hash) is False


def test_password_hashes_use_unique_salts():
    password = "DemoPassword123!"

    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash != second_hash
    assert verify_password(password, first_hash) is True
    assert verify_password(password, second_hash) is True


def test_verify_password_rejects_invalid_hash():
    assert verify_password("DemoPassword123!", "invalid-hash") is False
