
import json
import os
import random
import sys
import time

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config" / "x_actions.json"
STATE_FILE = ROOT_DIR / "data" / "execution_state.json"


class ExecutionController:

    def __init__(self):
        self.config = self._load_config()
        execution = self.config.get("execution", {})

        self.timezone = ZoneInfo(self.config["timezone"])
        self.enabled = execution.get("enabled", True)
        self.max_runs_per_day = execution.get("max_runs_per_day", 1)
        self.random_delay_minutes = execution.get(
            "random_delay_minutes", 15
        )

        self._validate_config()

    def _load_config(self):
        with CONFIG_FILE.open("r", encoding="utf-8") as file:
            config = json.load(file)

        if not isinstance(config, dict):
            raise ValueError("Invalid x_actions.json structure")

        if "timezone" not in config:
            raise ValueError("Missing timezone in x_actions.json")

        return config

    def _validate_config(self):
        if not isinstance(self.enabled, bool):
            raise ValueError("execution.enabled must be boolean")

        if (
            not isinstance(self.max_runs_per_day, int)
            or isinstance(self.max_runs_per_day, bool)
            or self.max_runs_per_day < 1
        ):
            raise ValueError(
                "execution.max_runs_per_day must be an integer >= 1"
            )

        if (
            not isinstance(self.random_delay_minutes, int)
            or isinstance(self.random_delay_minutes, bool)
            or self.random_delay_minutes < 0
        ):
            raise ValueError(
                "execution.random_delay_minutes must be an integer >= 0"
            )

    def _now(self):
        return datetime.now(self.timezone)

    def _load_state(self):
        if not STATE_FILE.exists():
            return {"date": "", "runs": []}

        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if (
            not isinstance(state, dict)
            or not isinstance(state.get("runs"), list)
        ):
            raise ValueError("Invalid execution_state.json structure")

        return state

    def _save_state(self, state):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary_file = STATE_FILE.with_suffix(".tmp")

        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(state, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        temporary_file.replace(STATE_FILE)

    def _get_today_state(self):
        today = self._now().date().isoformat()
        state = self._load_state()

        if state.get("date") != today:
            state = {"date": today, "runs": []}

        return state

    def _write_github_output(self, should_run):
        output_file = os.getenv("GITHUB_OUTPUT")

        if output_file:
            with open(output_file, "a", encoding="utf-8") as file:
                file.write(
                    f"should_run={'true' if should_run else 'false'}\n"
                )

    def prepare_execution(self):
        if not self.enabled:
            print("[EXECUTION] Controller disabled. Continuing.")
            self._write_github_output(True)
            return True

        # First check: determine whether an execution is still needed.
        state = self._get_today_state()

        if len(state["runs"]) >= self.max_runs_per_day:
            print("[EXECUTION] Daily target already reached.")
            self._write_github_output(False)
            return False

        print(
            f"[EXECUTION] Current executions: "
            f"{len(state['runs'])}/{self.max_runs_per_day}"
        )

        # Random delay is applied only when an execution is needed.
        if self.random_delay_minutes > 0:
            max_seconds = self.random_delay_minutes * 60
            delay = random.randint(0, max_seconds)

            print(f"[EXECUTION] Random delay: {delay} seconds.")
            time.sleep(delay)

        # Second check: another run may have completed the daily target.
        state = self._get_today_state()

        if len(state["runs"]) >= self.max_runs_per_day:
            print("[EXECUTION] Daily target reached during delay.")
            self._write_github_output(False)
            return False

        # Reserve the execution before scraping or analyzing posts.
        execution_time = self._now().isoformat()

        state["runs"].append({
            "started_at": execution_time
        })

        self._save_state(state)

        print(
            f"[EXECUTION] Execution authorized: "
            f"{len(state['runs'])}/{self.max_runs_per_day}"
        )

        self._write_github_output(True)
        return True


def main():
    try:
        controller = ExecutionController()
        controller.prepare_execution()
    except Exception as error:
        print(f"[EXECUTION] Controller error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
