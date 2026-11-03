/** What the Agent asks to do with a Tool call, in plain words, to follow "The Agent asks to …". */

type Arguments = Record<string, unknown>;

const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
});
const number = new Intl.NumberFormat("en-IN");

/** A value as text; a model can send anything, so nothing here may throw. */
function show(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return number.format(value);
  return value === undefined ? "?" : JSON.stringify(value);
}

function paise(value: unknown): string {
  return typeof value === "number" ? rupees.format(value / 100) : show(value);
}

const VERBS: Record<string, (a: Arguments) => string> = {
  get_balance: (a) => `read the balance of account ${show(a.account_id)}`,
  list_accounts: (a) => `list the accounts of ${show(a.customer_id)}`,
  get_customer_profile: (a) => `read the profile of ${show(a.customer_id)}`,
  list_transactions: (a) =>
    `list the last ${a.limit === undefined ? "" : `${show(a.limit)} `}transactions on account ${show(a.account_id)}`,
  raise_dispute: (a) => `dispute transaction ${show(a.transaction_id)}`,
  refund: (a) => `refund ${paise(a.amount_paise)} of transaction ${show(a.transaction_id)}`,
  list_tickets: () => "list the ticket inbox",
  get_ticket: (a) => `read ticket ${show(a.ticket_id)}`,
  send_email: (a) => `email ${show(a.to)}`,
  send_sms: (a) => `send a text to ${show(a.to)}`,
  convert: (a) =>
    `convert ${show(a.amount)} ${show(a.from_currency)} to ${show(a.to_currency)}`,
};

export function askSentence(tool: string, args: Arguments): string {
  const verb = VERBS[tool];
  return verb ? verb(args) : `use ${tool}`;
}
