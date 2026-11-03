import { describe, expect, it } from "vitest";
import { askSentence } from "./sentences";

describe("what the Agent asks to do, in plain words", () => {
  it("names the account whose balance it reads", () => {
    expect(askSentence("get_balance", { account_id: "A002" })).toBe(
      "read the balance of account A002",
    );
  });

  it.each([
    ["list_accounts", { customer_id: "C002" }, "list the accounts of C002"],
    ["get_customer_profile", { customer_id: "C001" }, "read the profile of C001"],
    [
      "list_transactions",
      { account_id: "A001", limit: 5 },
      "list the last 5 transactions on account A001",
    ],
    [
      "raise_dispute",
      { transaction_id: "T00418", reason: "charged twice" },
      "dispute transaction T00418",
    ],
    [
      "refund",
      { transaction_id: "T00418", amount_paise: 149900, idempotency_key: "k" },
      "refund ₹1,499.00 of transaction T00418",
    ],
    ["list_tickets", {}, "list the ticket inbox"],
    ["get_ticket", { ticket_id: "TK-0091" }, "read ticket TK-0091"],
    ["send_email", { to: "a@example.in", subject: "Hi", body: "…" }, "email a@example.in"],
    ["send_sms", { to: "+91 98111 20457", body: "…" }, "send a text to +91 98111 20457"],
    [
      "convert",
      { amount: 10000, from_currency: "INR", to_currency: "USD", reference: "" },
      "convert 10,000 INR to USD",
    ],
  ])("says what %s does", (tool, args, sentence) => {
    expect(askSentence(tool, args)).toBe(sentence);
  });

  it("falls back to the tool's name for a tool it doesn't know", () => {
    expect(askSentence("close_account", { account_id: "A001" })).toBe("use close_account");
  });
});
