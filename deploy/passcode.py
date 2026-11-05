"""Print the demo passcode (from SSM Parameter Store) to give to the judges."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecture_agent import config  # noqa: E402

print(config.session().client("ssm").get_parameter(Name="/study-corner/demo-passcode",
                                                   WithDecryption=True)["Parameter"]["Value"])
