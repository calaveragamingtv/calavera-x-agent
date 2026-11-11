
import json
import random
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
        self.execution_config = self.config.get("execution", {})
        self.timezone = ZoneInfo(self.config["timezone"])

        self.enabled = self.execution_config.get("enabled", True)
        self.max_runs_per_day = self.execution_config.get(
            "max_runs_per_day", 1
        )
        self.random_delay_minutes = self.execution_config.get(
            "random_delay_minutes", 15
        )

        self._validate_config()

    def _load_config(self):
        if not CONFIG_FILE.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {CONFIG_FILE}"
            )

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
            return {
                "date": self._now().date().isoformat(),
                "runs": []
            }

        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            raise ValueError("Invalid execution_state.json structure")

        if not isinstance(state.get("runs"), list):
            raise ValueError("Invalid execution_state.json runs")

        return state

    def _save_state(self, state):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        temporary_file = STATE_FILE.with_suffix(".tmp")

        with temporary_file.open("w", encoding="utf-8") as file:
            json.dump(state, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()

        temporary_file.replace(STATE_FILE)

    def _get_today_state(self):
        today = self._now().date().isoformat()
        state = self._load_state()

        if state.get("date") != today:
            state = {
                "date": today,
                "runs": []
            }

        return state

    def _has_remaining_runs(self, state):
        completed_or_started = len(state["runs"])

        return completed_or_started < self.max_runs_per_day

    def prepare_execution(self):
        """
        Returns True when the agent is allowed to continue.
        Returns False when the daily execution target was reached.
        """

        if not self.enabled:
            print("[EXECUTION] Controller disabled. Continuing.")
            return True

        # First check: do we still need an execution?
        state = self._get_today_state()

        if not self._has_remaining_runs(state):
            print(
                "[EXECUTION] Daily execution target already reached. "
                "Skipping this invocation."
            )
            return False

        print(
            f"[EXECUTION] Daily executions: {len(state['runs'])}/"
            f"{self.max_runs_per_day}"
        )

        # Random delay to simulate natural user activity.
        if self.random_delay_minutes > 0:
            max_delay_seconds = self.random_delay_minutes * 60
            delay_seconds = random.randint(0, max_delay_seconds)

            print(
                f"[EXECUTION] Random delay: "
                f"{delay_seconds} seconds."
            )

            time.sleep(delay_seconds)

        # Second check: another invocation may have completed the target.
        state = self._get_today_state()

        if not self._has_remaining_runs(state):
            print(
                "[EXECUTION] Daily target reached during random delay. "
                "Skipping execution."
            )
            return False

        # Register the execution immediately before allowing the agent
        # to continue.
        execution_time = self._now().isoformat()

        state["runs"].append({
            "started_at": execution_time
        })

        self._save_state(state)

        print(
            f"[EXECUTION] Execution authorized: "
            f"{len(state['runs'])}/{self.max_runs_per_day}"
        )

        return True
