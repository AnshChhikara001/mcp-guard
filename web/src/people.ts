/** The three People you can log in as, what to try as each, and who lives in the demo bank. */

import suggested from "./suggested-prompts.json";

export type PersonId = "C001" | "S001" | "S004";

export type SuggestedPrompt = { text: string; gateway: "allows" | "blocks" };

export const ROLES: { id: PersonId; label: string }[] = [
  { id: "C001", label: "Customer" },
  { id: "S001", label: "Staff" },
  { id: "S004", label: "Supervisor" },
];

export const ROLE_LABEL = { customer: "Customer", staff: "Staff", supervisor: "Supervisor" };

export const INTRO: Record<PersonId, string> = {
  C001: "You are Ananya Iyer, a bank Customer. Ask about your own money, or try to make the Agent show you someone else's.",
  S001: "You are a support Staff member. You serve Ananya Iyer and a few other Customers, and nobody else.",
  S004: "You are the Supervisor. You may look up every Customer in the bank.",
};

/** Checked against the real Agent API and fake model by agent/tests/test_agent_api.py. */
export const SUGGESTED = suggested as Record<PersonId, SuggestedPrompt[]>;

/** The seeded fake bank's people the suggested prompts name. */
export const DEMO_BANK: { id: string; name: string; what: string }[] = [
  { id: "C001", name: "Ananya Iyer", what: "Customer · account A001" },
  { id: "C002", name: "Rohan Mehta", what: "Customer · account A002" },
  { id: "S001", name: "Staff S001", what: "Serves C001, not C002" },
  { id: "S004", name: "Supervisor S004", what: "May see every Customer" },
];
