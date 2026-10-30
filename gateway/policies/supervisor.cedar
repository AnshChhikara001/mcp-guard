// A Supervisor does what Staff do, for every Customer.
@id("supervisor-acts-for-every-customer")
permit (
  principal is Supervisor,
  action in [Action::"ActForCustomer", Action::"MessageCustomer", Action::"ReadInbox"],
  resource
);
