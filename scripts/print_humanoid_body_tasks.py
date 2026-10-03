from __future__ import annotations

import json

from gil.learn.humanoid_body_tasks import humanoid_body_tasks_spec


def main() -> None:
    print(json.dumps(humanoid_body_tasks_spec(), indent=2))


if __name__ == "__main__":
    main()

