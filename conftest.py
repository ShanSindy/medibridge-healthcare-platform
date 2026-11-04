
import os
from dotenv import load_dotenv

load_dotenv(".env.test", override=True)

if os.getenv("DB_NAME") != "medibridge_test":
    raise RuntimeError(
        "Tests must use the medibridge_test database."
    )
