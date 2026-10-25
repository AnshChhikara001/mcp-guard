# An in-house eval harness instead of Inspect AI

The Red-team eval runs on a small harness of our own in `eval/`, not on Inspect AI. The spec allowed this if Inspect couldn't host our model adapters cleanly, and it can't: Inspect's solvers call Inspect's own model layer, while our Agent calls its models through the Agent's model interface (Gemini, OpenAI, the scripted fake and adapters loaded from a file outside the repository). A custom solver would have run our Agent with Inspect's model set to "none", so Inspect would have counted no tokens, no latency and no cost, and we'd have recorded them ourselves anyway.

What was left for Inspect to do (a loop over samples, a log, a viewer) didn't fit the rules agreed for the eval either: each task's result saved the moment it finishes, a re-run that skips tasks already done and retries only errors, runs limited to a number of tasks or chosen Attack classes, and results small enough to commit. Inspect's logs are large per-run files, and it would have added dozens of packages (cloud storage clients, a terminal UI, numpy) to an eval that needs none of them.

## Considered Options

- **Inspect AI with a custom solver driving our Agent:** kept the Inspect name and viewer, but with its model layer unused, metrics recorded by hand and resume rules fought against its log format.
- **Inspect AI with each of our adapters wrapped as an Inspect model provider:** the Agent's LangGraph loop would then have to call models through Inspect, converting every message between the two formats, for every adapter including the private one.

## Consequences

- The task file format (TOML, one file per Attack or Benign task) is ours; it is plain enough to convert to another harness's dataset format if one is ever needed.
- The harness is about 800 lines we maintain: loading tasks, running them, scoring, results and the summary.
- A Benign task is scored by an exact check that its work got done. When it isn't done, it counts as a False block, so the False block rate with the Gateway's defences off is the model's own failure rate, and the Gateway's cost is the difference between the off and on runs.
