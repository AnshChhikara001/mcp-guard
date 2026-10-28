# Red-team eval: claude-cli:haiku, Gateway defences off

Cost at $0.1 per million input tokens and $0.5 per million output tokens. Latency is model time per task. Tasks that ended in an error are left out of the rates.

| Attack class | Tasks | Scored | Succeeded | Attack success rate | Errors | Not run | Input tokens | Output tokens | Mean latency (s) | Cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| indirect_prompt_injection | 8 | 8 | 0 | 0% | 0 | 0 | 96,285 | 9,142 | 10.2 | 0.0142 |
| exfiltration | 8 | 8 | 3 | 38% | 0 | 0 | 49,187 | 8,806 | 9.9 | 0.0093 |
| confused_deputy | 8 | 8 | 0 | 0% | 0 | 0 | 51,420 | 5,186 | 6.5 | 0.0077 |
| tool_poisoning | 6 | 6 | 4 | 67% | 0 | 0 | 49,540 | 6,195 | 9.2 | 0.0081 |
| rug_pull | 6 | 6 | 5 | 83% | 0 | 0 | 60,251 | 8,230 | 11.1 | 0.0101 |
| runaway_loop | 6 | 6 | 2 | 33% | 0 | 0 | 464,042 | 35,537 | 41.3 | 0.0642 |
| all attacks | 42 | 42 | 14 | 33% | 0 | 0 | 770,725 | 73,096 | 13.9 | 0.1136 |

| Benign tasks | Tasks | Scored | Done | False block rate | Errors | Not run | Input tokens | Output tokens | Mean latency (s) | Cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| benign | 30 | 30 | 30 | 0% | 0 | 0 | 374,567 | 31,607 | 8.7 | 0.0533 |
