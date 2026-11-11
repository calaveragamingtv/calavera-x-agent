
import json
import os
import random
import sys
import time

from datetime import datetime, timedelta
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
        self.calls_per_day = execution.get("calls_per_day", 1)
        self.max_runs_per_day = execution.get("max_runs_per_day", 1)
        self.random_delay_minutes = execution.get(
            "random_delay_minutes", 15
        )
        self.reservation_timeout_minutes = execution.get(
            "reservation_timeout_minutes", 60
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

        for name, value in (
            ("calls_per_day", self.calls_per_day),
            ("max_runs_per_day", self.max_runs_per_day),
            ("random_delay_minutes", self.random_delay_minutes),
            ("reservation_timeout_minutes",
             self.reservation_timeout_minutes),
        ):
            minimum = 0 if name in (
                "random_delay_minutes",
                "reservation_timeout_minutes",
            ) else 1

            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
            ):
                raise ValueError(
                    f"execution.{name} must be an integer >= {minimum}"
                )

        if self.max_runs_per_day > self.calls_per_day:
            raise ValueError(
                "max_runs_per_day cannot exceed calls_per_day"
            )

    def _now(self):
        return datetime.now(self.timezone)

    def _load_state(self):
        if not STATE_FILE.exists():
            return {
                "date": "",
                "calls": 0,
                "runs": [],
                "call_log": []
            }

        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            raise ValueError("Invalid execution_state.json structure")

        state.setdefault("date", "")
        state.setdefault("calls", 0)
        state.setdefault("runs", [])
        state.setdefault("call_log", [])

        if not isinstance(state["runs"], list):
            raise ValueError("Invalid runs list in execution state")

        if not isinstance(state["call_log"], list):
            raise ValueError("Invalid call_log list in execution state")

        if (
            not isinstance(state["calls"], int)
            or isinstance(state["calls"], bool)
            or state["calls"] < 0
        ):
            raise ValueError("Invalid calls counter in execution state")

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
            state = {
                "date": today,
                "calls": 0,
                "runs": [],
                "call_log": []
            }

        return state

    def _count_started_runs(self, state):
        return sum(
            1
            for run in state["runs"]
            if run.get("status") in (
                "started",
                "completed",
                "failed"
            )
        )

    def _count_active_reservations(self, state):
        return sum(
            1
            for run in state["runs"]
            if run.get("status") == "reserved"
        )

    def _find_run(self, state):
        for run in state["runs"]:
            if run.get("run_id") == self.run_id:
                return run
        return None

    def _clean_abandoned_reservations(self, state):
        now = self._now()
        valid_runs = []

        for run in state["runs"]:
            if run.get("status") != "reserved":
                valid_runs.append(run)
                continue

            reserved_at = run.get("reserved_at")

            try:
                reserved_time = datetime.fromisoformat(reserved_at)
                if reserved_time.tzinfo is None:
                    reserved_time = reserved_time.replace(
                        tzinfo=self.timezone
                    )
            except (TypeError, ValueError):
                continue

            age = now - reserved_time

            if age <= timedelta(
                minutes=self.reservation_timeout_minutes
            ):
                valid_runs.append(run)
            else:
                print(
                    "[EXECUTION] Removing abandoned reservation: "
                    f"{run.get('run_id')}"
                )

        state["runs"] = valid_runs

    def _write_github_output(self, should_run):
        output_file = os.getenv("GITHUB_OUTPUT")

        if output_file:
            with open(output_file, "a", encoding="utf-8") as file:
                file.write(
                    f"should_run={'true' if should_run else 'false'}\n"
                )

    def _record_call(self, state, status, reason):
        state["call_log"].append({
            "run_id": self.run_id,
            "call": state["calls"],
            "status": status,
            "reason": reason,
            "timestamp": self._now().isoformat()
        })

    def _decision(self, state):
        current_call = state["calls"]
        current_runs = self._count_started_runs(state)
        active_reservations = self._count_active_reservations(state)

        remaining_runs = (
            self.max_runs_per_day
            - current_runs
            - active_reservations
        )

        if remaining_runs <= 0:
            return False, "daily_execution_target_reached"

        if current_call > self.calls_per_day:
            return False, "daily_call_limit_reached"

        remaining_calls = self.calls_per_day - current_call + 1

        if remaining_calls <= remaining_runs:
            return True, "mandatory_execution"

        probability = remaining_runs / remaining_calls
        random_value = random.random()

        print(
            f"[EXECUTION] Random decision: "
            f"{random_value:.4f} < {probability:.4f}"
        )

        if random_value < probability:
            return True, "random_selection"

        return False, "random_skip"

    def prepare(self):
        if not self.enabled:
            print("[EXECUTION] Controller disabled. Continuing.")
            self._write_github_output(True)
            return True

        state = self._get_today_state()

        # Avoid duplicate reservations for the same workflow run.
        existing_run = self._find_run(state)

        if existing_run:
            if existing_run.get("status") in (
                "reserved",
                "started",
                "completed",
            ):
                print(
                    "[EXECUTION] This workflow run already has "
                    "an execution record."
                )
                self._write_github_output(
                    existing_run.get("status") in (
                        "reserved",
                        "started",
                    )
                )
                return existing_run.get("status") in (
                    "reserved",
                    "started",
                )

        self._clean_abandoned_reservations(state)

        state["calls"] += 1

        print(
            f"[EXECUTION] Call {state['calls']}/{self.calls_per_day}"
        )

        should_run, reason = self._decision(state)

        if not should_run:
            self._record_call(state, "skipped", reason)
            self._save_state(state)
            print(f"[EXECUTION] Skipping: {reason}")
            self._write_github_output(False)
            return False

        self._record_call(state, "selected", reason)
        self._save_state(state)

        if self.random_delay_minutes > 0:
            delay = random.randint(
                0,
                self.random_delay_minutes * 60
            )
            print(f"[EXECUTION] Random delay: {delay} seconds.")
            time.sleep(delay)

        # Reload the original day's state. A delay may cross midnight.
        state = self._load_state()

        existing_run = self._find_run(state)

        if existing_run:
            print("[EXECUTION] Reservation already exists.")
            self._write_github_output(True)
            return True

        current_runs = self._count_started_runs(state)
        active_reservations = self._count_active_reservations(state)

        if (
            current_runs + active_reservations
            >= self.max_runs_per_day
        ):
            print("[EXECUTION] Daily execution target reached.")
            self._write_github_output(False)
            return False

        state["runs"].append({
            "run_id": self.run_id,
            "status": "reserved",
            "reserved_at": self._now().isoformat(),
            "call_date": state.get("date")
        })

        self._save_state(state)

        print("[EXECUTION] Execution slot reserved.")
        self._write_github_output(True)
        return True

    def start(self):
        if not self.enabled:
            print("[EXECUTION] Controller disabled.")
            return

        state = self._load_state()
        run = self._find_run(state)

        if not run:
            raise RuntimeError(
                "No execution reservation found for this workflow run"
            )

        if run.get("status") == "started":
            print("[EXECUTION] Execution already started.")
            return

        if run.get("status") != "reserved":
            raise RuntimeError(
                f"Cannot start execution with status: {run.get('status')}"
            )

        run["status"] = "started"
        run["started_at"] = self._now().isoformat()

        self._save_state(state)

        print("[EXECUTION] Execution started.")

    def complete(self):
        if not self.enabled:
            return

        state = self._load_state()
        run = self._find_run(state)

        if not run:
            print("[EXECUTION] No execution record to complete.")
            return

        if run.get("status") == "completed":
            print("[EXECUTION] Execution already completed.")
            return

        if run.get("status") != "started":
            raise RuntimeError(
                "Cannot complete an execution that was not started"
            )

        run["status"] = "completed"
        run["completed_at"] = self._now().isoformat()

        self._save_state(state)

        print("[EXECUTION] Execution completed.")

    def cancel(self):
        if not self.enabled:
            return

        state = self._load_state()
        run = self._find_run(state)

        if not run:
            print("[EXECUTION] No reservation to cancel.")
            return

        if run.get("status") == "reserved":
            run["status"] = "cancelled"
            run["cancelled_at"] = self._now().isoformat()
            self._save_state(state)
            print("[EXECUTION] Reservation cancelled.")
            return

        print(
            "[EXECUTION] Started executions cannot be cancelled "
            "as reservations."
        )


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "prepare"

    try:
        controller = ExecutionController()

        if command == "prepare":
            controller.prepare()
        elif command == "start":
            controller.start()
        elif command == "complete":
            controller.complete()
        elif command == "cancel":
            controller.cancel()
        else:
            raise ValueError(
                f"Unknown command: {command}. "
                "Use prepare, start, complete or cancel."
            )

    except Exception as error:
        print(f"[EXECUTION] Error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
