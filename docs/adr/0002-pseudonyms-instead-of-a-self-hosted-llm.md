# Pseudonyms instead of a self-hosted LLM

Banks keep Customer PII away from third-party LLMs, usually by self-hosting a model, which needs GPUs we can't afford. Instead, the Gateway replaces PII in Tool call results with per-conversation Pseudonyms (`<PHONE_1>`) before the Agent sees them, swaps a Pseudonym back to the real value only when it's passed into an allowed Tool call, and reveals real values in the chat UI only to a viewer whose role allows it. The model, its provider, the Agent's memory and the traces never hold raw PII.

## Considered Options

- **Self-hosted LLM for PII-bearing turns:** the usual approach, but it needs a GPU host and a weaker model would do the reasoning.
- **Let the model see PII and only block it on the way out:** simpler, but every prompt sends Customer PII to a third party.

## Consequences

- The Gateway keeps a per-conversation Pseudonym store; losing it makes old transcripts unreadable, which is acceptable.
- The chat UI needs a trusted reveal call to the Gateway, authorized by Cedar and recorded in the audit log; the Agent can never call it.
- Answers that need the real value in reasoning (e.g. "is this the same phone as last month?") can only compare Pseudonyms within one conversation.
