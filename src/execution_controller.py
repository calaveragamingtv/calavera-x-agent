
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

        self.run_id = os.getenv(
            "GITHUB_RUN_ID", f"local-{os.getpid()}"
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

        if not all(isinstance(run, dict) for run in state["runs"]):
            raise ValueError("Invalid execution entry in state file")

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

    def _count_started_runs(self, state):
        # Entries from the previous controller version had no status.
        # They are treated as started to preserve the existing count.
        return sum(
            1
            for run in state["runs"]
            if run.get("status") in ("started", "completed")
            or "status" not in run
        )

    def _write_github_output(self, should_run):
        output_file = os.getenv("GITHUB_OUTPUT")

        if output_file:
            with open(output_file, "a", encoding="utf-8") as file:
                file.write(
                    f"should_run={'true' if should_run else 'false'}\n"
                )

    def prepare(self):
        if not self.enabled:
            print("[EXECUTION] Controller disabled. Continuing.")
            self._write_github_output(True)
            return True

        state = self._get_today_state()

        # A previous workflow may have stopped before starting the agent.
        # The workflow concurrency group ensures no previous run is active.
        state["runs"] = [
            run for run in state["runs"]
            if run.get("status") != "reserved"
        ]

        current_count = self._count_started_runs(state)

        if current_count >= self.max_runs_per_day:
            print(
                f"[EXECUTION] Daily target reached: "
                f"{current_count}/{self.max_runs_per_day}"
            )
            self._save_state(state)
            self._write_github_output(False)
            return False

        print(
            f"[EXECUTION] Execution needed: "
            f"{current_count}/{self.max_runs_per_day}"
        )

        if self.random_delay_minutes > 0:
            max_seconds = self.random_delay_minutes * 60
            delay = random.randint(0, max_seconds)

            print(f"[EXECUTION] Random delay: {delay} seconds.")
            time.sleep(delay)

        # Check again after sleeping.
        state = self._get_today_state()

        state["runs"] = [
            run for run in state["runs"]
            if run.get("status") != "reserved"
        ]

        current_count = self._count_started_runs(state)

        if current_count >= self.max_runs_per_day:
            print(
                "[EXECUTION] Daily target reached during delay. "
                "Skipping."
            )
            self._save_state(state)
            self._write_github_output(False)
            return False

        state["runs"].append({
            "run_id": self.run_id,
            "status": "reserved",
            "reserved_at": self._now().isoformat()
        })

        self._save_state(state)

        print("[EXECUTION] Execution slot reserved.")
        self._write_github_output(True)
        return True

    def start(self):
        if not self.enabled:
            print("[EXECUTION] Controller disabled. Nothing to register.")
            return

        state = self._get_today_state()

        for run in state["runs"]:
            if (
                run.get("run_id") == self.run_id
                and run.get("status") == "reserved"
            ):
                run["status"] = "started"
                run["started_at"] = self._now().isoformat()
                self._save_state(state)

                print(
                    f"[EXECUTION] Execution started: "
                    f"{self._count_started_runs(state)}/"
                    f"{self.max_runs_per_day}"
                )
                return

        raise RuntimeError(
            "No execution reservation found for this workflow run"
        )

    def cancel(self):
        if not self.enabled:
            return

        state = self._get_today_state()
        original_count = len(state["runs"])

        state["runs"] = [
            run for run in state["runs"]
            if not (
                run.get("run_id") == self.run_id
                and run.get("status") == "reserved"
            )
        ]

        if len(state["runs"]) != original_count:
            self._save_state(state)
            print("[EXECUTION] Unstarted reservation released.")
        else:
            print("[EXECUTION] No reservation to release.")


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "prepare"

    try:
        controller = ExecutionController()

        if command == "prepare":
            controller.prepare()
        elif command == "start":
            controller.start()
        elif command == "cancel":
            controller.cancel()
        else:
            raise ValueError(
                f"Unknown command: {command}. "
                "Use prepare, start or cancel."
            )

    except Exception as error:
        print(f"[EXECUTION] Error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
