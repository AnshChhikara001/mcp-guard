// A Customer acts on their own data only: their profile, accounts, balances and transactions,
// disputes and refunds of their own transactions, and messages to their own registered email
// or phone.
@id("customer-acts-for-themselves")
permit (
  principal is Customer,
  action in [Action::"ActForCustomer", Action::"MessageCustomer"],
  resource is Customer
)
when { resource == principal };

// A Customer reads their own tickets, one at a time, but not the inbox.
@id("customer-reads-own-ticket")
permit (
  principal is Customer,
  action == Action::"get_ticket",
  resource is Ticket
)
when { resource.owner == principal };
