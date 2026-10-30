// Anyone may convert currencies: the call names no Customer.
@id("anyone-converts-currency")
permit (
  principal,
  action == Action::"convert",
  resource is Vendor
);
