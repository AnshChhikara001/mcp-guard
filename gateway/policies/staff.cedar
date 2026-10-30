// Staff do for each Customer they serve what that Customer could do themselves, messages
// included, and nothing for the Customers they don't serve.
@id("staff-act-for-customers-they-serve")
permit (
  principal is Staff,
  action in [Action::"ActForCustomer", Action::"MessageCustomer"],
  resource is Customer
)
when { principal.serves.contains(resource) };

// Staff work the whole support inbox.
@id("staff-read-the-inbox")
permit (
  principal is Staff,
  action in Action::"ReadInbox",
  resource
);
