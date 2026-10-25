"""Run the Red-team eval from the terminal: `make eval`, or `uv run mcp-guard-eval`.

    mcp-guard-eval --defences off [--model gemini:gemini-3.5-flash] [--only exfiltration]
                   [--limit 30] [--price 1.5 9]

MODEL (or --model) picks the model as `provider:model`, as for the chat; `fake`, the default,
is the scripted fake model, which runs only the tasks that script it and needs no API key.
Results go to eval/results/<model>/defences-<off|on>/ unless --results says otherwise; running
the same command again carries on where it stopped. The run resets the fake bank in
DATABASE_URL for every task. OTEL_EXPORTER_OTLP_ENDPOINT turns tracing on.
"""

import argparse
import logging
import os
from pathlib import Path

import anyio

from mcp_guard.agent.adapters import load_model
from mcp_guard.agent.model import Model
from mcp_guard.agent.tracing import start_tracing
from mcp_guard.fake_servers.reset import LOCAL_DATABASE_URL
from mcp_guard.redteam.fake import FAKE, fake_model
from mcp_guard.redteam.harness import run_eval
from mcp_guard.redteam.prices import PRICES, Price
from mcp_guard.redteam.results import DEFENCES, Defences, ItemResult
from mcp_guard.redteam.tasks import ATTACK_CLASSES, Task, load_tasks


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Red-team eval.")
    parser.add_argument(
        "--defences", required=True, choices=DEFENCES, help="the Gateway's defences"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("MODEL") or FAKE,
        help="provider:model, or fake (default: MODEL, else fake)",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=[*ATTACK_CLASSES, "benign"],
        help="run only this Attack class, or the Benign tasks (repeatable)",
    )
    parser.add_argument("--limit", type=int, help="run at most this many tasks now")
    parser.add_argument(
        "--price",
        nargs=2,
        type=float,
        metavar=("INPUT", "OUTPUT"),
        help="US dollars per million input and output tokens, for a model with no listed price",
    )
    parser.add_argument("--tasks", type=Path, default=Path("eval/tasks"), help="the tasks folder")
    parser.add_argument("--results", type=Path, help="the run's results folder")
    args = parser.parse_args()
    # The MCP SDK logs every HTTP request the Agent makes to the Gateway.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    price = (
        Price(input_per_million=args.price[0], output_per_million=args.price[1])
        if args.price
        else PRICES.get(args.model)
    )
    if price is None:
        parser.error(f"There is no listed price for {args.model}; pass --price INPUT OUTPUT")
    tasks = load_tasks(args.tasks)
    if args.model == FAKE:
        tasks = [t for t in tasks if t.spec.fake_model]
        model_for = fake_model
    else:
        model = load_model(args.model, os.environ.get("MODEL_ADAPTERS_FILE"))

        def model_for(task: Task) -> Model:
            return model

    defences: Defences = args.defences
    run_dir = args.results or Path("eval/results") / args.model.replace(":", "_") / (
        f"defences-{defences}"
    )
    print(f"Running {len(tasks)} tasks with {args.model}, Gateway defences {defences}.")
    print(f"Results in {run_dir}. The bank is reset for every task.")

    def show(result: ItemResult) -> None:
        print(f"{result.task}: {result.outcome}" + (f" ({result.error})" if result.error else ""))

    start_tracing("mcp-guard-eval")
    summary = anyio.run(
        lambda: run_eval(
            tasks,
            model_for,
            run_dir,
            model_name=args.model,
            defences=defences,
            price=price,
            database_url=os.environ.get("DATABASE_URL", LOCAL_DATABASE_URL),
            only=args.only,
            limit=args.limit,
            on_result=show,
        )
    )
    print()
    print((run_dir / "summary.md").read_text())
    if summary.not_run:
        print(f"{len(summary.not_run)} tasks have no result yet.")


if __name__ == "__main__":
    main()
