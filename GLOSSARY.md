# mcp-guard

A gateway that sits between AI agents and the MCP servers they use, enforcing security on every tool call, measured by a red-team eval that attacks agents with and without it.

## Language

**Gateway**:
The component between an Agent and its MCP servers that decides, for every Tool call, whether it proceeds, is changed, waits for approval, or is blocked.
_Avoid_: Firewall, proxy, middleware, guard

**Agent**:
An LLM-driven program that reaches tools only through MCP servers; the thing the Gateway protects and constrains.
_Avoid_: Bot, assistant, client

**Tool call**:
One request from an Agent to invoke a named tool on an MCP server, with arguments, plus its result.
_Avoid_: Action, function call, request

**Decision**:
The Gateway's verdict on one Tool call: allow, redact, hold, or deny. Stopping a runaway loop is a deny with a reason.
_Avoid_: Verdict, action, outcome

**Hold**:
A Decision that pauses a Tool call until a Supervisor approves or rejects it.
_Avoid_: Pending, HITL, approval request

**Redact**:
A Decision that lets a Tool call proceed after the Gateway removes or replaces sensitive data in its arguments or result.
_Avoid_: Mask, sanitize, scrub

**Pseudonym**:
A placeholder like `<PHONE_1>` that stands for one piece of a Customer's PII, so the model reasons over the placeholder and never sees the real value.
_Avoid_: Token (collides with Login token), mask, placeholder

**Taint**:
The state of a conversation after the Agent has read untrusted content; while tainted, any Tool call that could send data outside the bank is held.
_Avoid_: Contaminated, flagged, unsafe

**Pinned tool**:
A tool whose definition (name, description, input schema) the Gateway has recorded and approved; if the definition later changes, the tool is disabled until a Supervisor approves the change.
_Avoid_: Locked, approved tool, whitelisted tool

### People

**Person**:
Whoever is logged in and has the Agent work for them: one Customer, Staff member or Supervisor. When Staff member S003 asks about Customer C001, the Person is S003 and C001 is the Customer being served.
_Avoid_: User, identity, principal, caller

**Customer**:
A bank customer who talks to the Agent about their own accounts only.
_Avoid_: User, client, account holder

**Staff**:
A bank support employee who talks to the Agent on behalf of many Customers.
_Avoid_: Support agent (collides with Agent), employee, operator

**Supervisor**:
A Staff member allowed to approve or reject Holds.
_Avoid_: Admin, approver, manager

### Login

**Login token**:
A short-lived statement, signed by the Gateway, naming one Person and their role (Customer, Staff or Supervisor). Each Person gets two at login, an MCP token and a UI token, which differ only in where they are accepted.
_Avoid_: Token (collides with Pseudonym), session, credential

**MCP token**:
The Login token accepted only for connecting to the Gateway and making Tool calls; the only one an Agent ever holds.
_Avoid_: Agent token, access token

**UI token**:
The Login token accepted only by the Gateway's endpoints for people, such as revealing Pseudonyms and approving Holds; never given to an Agent.
_Avoid_: Admin token, web token

### Evaluation

**Red-team eval**:
A fixed, versioned set of Attacks run against an Agent with and without the Gateway to measure how often each attack succeeds and how often legitimate work is wrongly blocked.
_Avoid_: Benchmark, test suite, pentest

**Attack**:
One scripted attempt in the Red-team eval to make an Agent do something its user or operator did not authorize, with a defined success condition.
_Avoid_: Exploit, jailbreak, test case

**Attack class**:
A category of Attack in scope: indirect prompt injection, exfiltration, confused deputy, tool poisoning, rug pull, or runaway loop. Jailbreaks and harmful content are out of scope.
_Avoid_: Threat type, vulnerability

**Attack success rate**:
The share of Attacks in a Red-team eval run whose success condition was met.
_Avoid_: ASR, hit rate, failure rate

**Benign task**:
An ordinary, legitimate request in the Red-team eval that the Agent should complete without being blocked; used to measure False blocks.
_Avoid_: Normal case, control, happy path

**Canary**:
A unique fake value planted in the seeded bank data whose appearance in any outgoing Tool call proves an exfiltration Attack succeeded, unless the call sends a Customer's own details to that Customer's registered email or phone.
_Avoid_: Honeytoken, marker, tracer

**False block**:
A legitimate Tool call that the Gateway blocked or held for approval when it should have proceeded. The Red-team eval measures it by exact checks, as a Benign task whose work didn't get done; with the Gateway's defences off, that share is the model's own failure rate, so the Gateway's False blocks are the difference between the off and on runs.
_Avoid_: False positive, over-blocking
